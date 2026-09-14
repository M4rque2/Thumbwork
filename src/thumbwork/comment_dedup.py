"""Deterministic, per-note comment upserts backed by canonical output.jsonl.

The runtime holds the run lock. Each write replaces the JSONL file atomically:
updates and source-key aliases therefore cannot get out of sync with an index.
Raw model responses remain in runtime history/debug traces.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path


def normalized(value):
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    text = re.sub(r'\s+', ' ', text)
    # Ignore OCR spacing around Chinese characters, but keep meaningful English spaces.
    return re.sub(r'(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])', '', text)


def signature(comment):
    return normalized(comment.get('author_name')), normalized(comment.get('comment_text'))


def scope(data):
    return tuple(data.get(key) for key in ('task', 'app', 'note_key'))


def read_rows(path):
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get('data'), dict):
                raise ValueError('expected a record with an object data field')
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f'Cannot update invalid output.jsonl line {number}: {exc}') from exc
        rows.append(row)
    return rows


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CommentIndex:
    def __init__(self, rows, data):
        self.comments = []
        self.owners = {}
        self.aliases = {}
        self.previous = set()
        self.previous_step = -1
        self.warnings = []
        self.keyword = data.get('search_keyword')
        for row in rows:
            saved = row['data']
            if scope(saved) != scope(data):
                continue
            if saved.get('record_type') == 'note':
                self.keyword = saved.get('search_keyword', self.keyword)
            self.warnings.extend(row.get('dedup', {}).get('warnings', []))
            if saved.get('record_type') == 'comment_batch':
                last_seen = row.get('dedup', {}).get('last_seen_step', row['step'])
                if last_seen >= self.previous_step:
                    self.previous = {signature(c) for c in saved['comments']}
                    self.previous_step = last_seen
                for comment in saved['comments']:
                    self.add(comment, row)

    def add(self, comment, owner):
        self.comments.append(comment)
        self.owners[id(comment)] = owner
        for key in [comment['comment_key'], *comment.get('source_comment_keys', [])]:
            self.alias(key, comment)

    def alias(self, key, comment):
        candidates = self.aliases.setdefault(key, [])
        if not any(candidate is comment for candidate in candidates):
            candidates.append(comment)

    def parent(self, key):
        if key is None:
            return None
        candidates = self.aliases.get(key, [])
        return candidates[0]['comment_key'] if len(candidates) == 1 else key

    def parent_reference(self, comment):
        return self.parent(comment.get('parent_comment_key') or comment.get('source_parent_comment_key'))

    @staticmethod
    def compatible(old, new):
        old_author, old_text = signature(old)
        new_author, new_text = signature(new)
        if old_author and new_author and old_author != new_author:
            return False
        for field in ('publish_time', 'location'):
            a, b = normalized(old.get(field)), normalized(new.get(field))
            if a and b and a != b:
                return False
        if old_text == new_text or not old_text or not new_text:
            return True
        return ((old.get('text_complete') is False and new_text.startswith(old_text))
                or (new.get('text_complete') is False and old_text.startswith(new_text)))

    def content_match(self, old, new, overlap):
        author, text = signature(new)
        if not author or not text or signature(old) != (author, text):
            return False
        matches = 0
        for field in ('publish_time', 'location'):
            a, b = normalized(old.get(field)), normalized(new.get(field))
            if a and b and a != b:
                return False
            matches += bool(a and b)
        old_parent, new_parent = self.parent_reference(old), self.parent_reference(new)
        same_thread = old_parent is not None and old_parent == new_parent
        same_target = bool(old.get('reply_to_author_name')) and normalized(old['reply_to_author_name']) == normalized(new.get('reply_to_author_name'))
        if same_thread and matches and (same_target or len(text) >= 12):
            return True
        # Multiple shared comments support overlapping screens even if model IDs drift.
        # Generic short replies in separate threads are deliberately not merged.
        if overlap and len(text) >= 12 and matches == 2:
            return True
        if overlap and len(text) >= 6 and same_target:
            return True
        return old_parent is None and new_parent is None and len(text) >= 12 and matches == 2

    def unique_key(self, proposed):
        key, suffix = proposed, 2
        while key in self.aliases:
            key = f'{proposed}__{suffix}'
            suffix += 1
        return key


def save_comment_extraction(output_path, step, data, summary=None):
    """Return save feedback for comment records, or None for other task schemas."""
    if data.get('record_type') not in {'comment_batch', 'note_complete'}:
        return None
    if not isinstance(data.get('note_key'), str) or not data['note_key'].strip():
        raise ValueError('Comment extraction requires a nonempty note_key')
    path = Path(output_path)
    rows = read_rows(path)
    data = copy.deepcopy(data)
    index = CommentIndex(rows, data)
    if index.keyword is not None:
        data['search_keyword'] = index.keyword
    row = {'step': step, 'summary': summary, 'record_index': 0, 'data': data}
    if data['record_type'] == 'note_complete':
        data['comments_saved'] = len(index.comments)
        limitations = data.get('limitations') or []
        if not isinstance(limitations, list):
            raise ValueError('Completion limitations must be a list')
        if not all(isinstance(item, str) for item in limitations):
            raise ValueError('Completion limitations must contain text')
        limitations = list(limitations)
        if index.warnings:
            limitations.append('Comment identity/content conflicts require review; see dedup warnings in output records.')
        if any(c.get('text_complete') is False or (not c.get('comment_text') and c.get('has_image') is not True) for c in index.comments):
            limitations.append('Some saved comments have incomplete or missing text.')
        if any(c.get('source_parent_comment_key') for c in index.comments):
            limitations.append('Some reply parent references remain unresolved.')
        data['limitations'] = list(dict.fromkeys(limitations))
        if limitations:
            data['comments_status'] = 'partial'
        previous = next((r for r in rows if scope(r['data']) == scope(data) and r['data'].get('record_type') == 'note_complete'), None)
        if previous is not None:
            previous.update(row)
        else:
            rows.append(row)
        write_rows(path, rows)
        return f"Comment completion saved: {data['comments_saved']} unique records; status={data.get('comments_status')}."

    incoming = data.get('comments')
    if not isinstance(incoming, list):
        raise ValueError('comment_batch requires a comments list')
    for c in incoming:
        if not isinstance(c, dict) or not isinstance(c.get('comment_key'), str) or not c['comment_key'].strip():
            raise ValueError('Each comment requires a nonempty comment_key')
        for field in ('author_name', 'comment_text', 'parent_comment_key', 'reply_to_author_name'):
            if c.get(field) is not None and not isinstance(c[field], str):
                raise ValueError(f'Comment {field} must be text or null')
        # These are writer-owned fields; do not trust model-supplied aliases.
        c.pop('source_comment_keys', None)
        c.pop('source_parent_comment_key', None)
    common = {signature(c) for c in incoming} & index.previous
    overlap = (0 <= step - index.previous_step <= 4
               and len(common) >= 2 and any(len(text) >= 12 for _, text in common))
    data['comments'] = []
    added = updated = skipped = 0
    mappings, warnings, pending_parents = {}, [], []
    for new in incoming:
        proposed = new['comment_key']
        aliases = list(index.aliases.get(proposed, []))
        candidates = [c for c in aliases if index.compatible(c, new)]
        if not candidates:
            candidates = [c for c in index.comments if index.content_match(c, new, overlap)]
        if len(candidates) == 1:
            old = candidates[0]
            key = old['comment_key']
            mappings[proposed] = key
            changed = False
            old_text, new_text = normalized(old.get('comment_text')), normalized(new.get('comment_text'))
            extending = bool(new_text) and (not old_text or (old.get('text_complete') is False and new_text.startswith(old_text)))
            for field, value in new.items():
                if field in {'comment_key', 'parent_comment_key', 'source_comment_keys'} or value is None:
                    continue
                if old.get(field) is None or (field in {'comment_text', 'text_complete'} and extending):
                    if old.get(field) != value:
                        old[field] = value
                        changed = True
            if proposed != key:
                old['source_comment_keys'] = sorted(set(old.get('source_comment_keys', [])) | {proposed})
                index.alias(proposed, old)
                if aliases and not any(c is old for c in aliases):
                    warnings.append(f'{proposed}: reused source key refers to multiple comments; canonical match is {key}.')
            for field in ('is_reply', 'reply_to_author_name'):
                if old.get(field) is not None and new.get(field) is not None and old[field] != new[field]:
                    warnings.append(f'{key}: conflicting {field}; kept original, requires review.')
            a, b = index.parent_reference(old), index.parent_reference(new)
            if a is not None and b is not None and a != b:
                warnings.append(f'{key}: conflicting parent keys {a!r} and {b!r}; kept original, requires review.')
            elif a is None and b is not None:
                old['parent_comment_key'] = b
                old['source_parent_comment_key'] = new['parent_comment_key']
                changed = True
            if changed:
                updated += 1
                index.owners[id(old)].setdefault('dedup', {})['last_updated_step'] = step
            else:
                skipped += 1
            index.owners[id(old)].setdefault('dedup', {})['last_seen_step'] = step
            pending_parents.append(old)
        else:
            key = index.unique_key(proposed)
            if aliases or len(candidates) > 1:
                warnings.append(f'{proposed}: conflicting or ambiguous identity; preserved separately as {key}.')
            elif any(signature(c) == signature(new) and signature(new)[1] for c in index.comments):
                warnings.append(f'{proposed}: matching author/text without sufficient context; preserved separately.')
            new['comment_key'] = key
            if new.get('parent_comment_key') is not None:
                new['source_parent_comment_key'] = new['parent_comment_key']
            if key != proposed:
                new['source_comment_keys'] = [proposed]
            data['comments'].append(new)
            index.add(new, row)
            pending_parents.append(new)
            mappings[proposed] = key
            added += 1

    # Resolve aliases after the entire batch, including children preceding parents.
    for comment in index.comments:
        parent = comment.get('source_parent_comment_key')
        if parent is None:
            continue
        candidates = index.aliases.get(parent, [])
        if len(candidates) > 1 or (len(candidates) == 1 and candidates[0] is comment):
            comment['parent_comment_key'] = None
            warnings.append(f"{comment['comment_key']}: ambiguous or self-referencing parent {parent!r}; left unlinked.")
        elif len(candidates) == 1:
            comment['parent_comment_key'] = candidates[0]['comment_key']
            comment.pop('source_parent_comment_key', None)
        else:
            comment['parent_comment_key'] = None

    if data['comments']:
        rows.append(row)
    if warnings:
        # Persist conflicts even when the entire batch was merged into older rows.
        owner = row if data['comments'] else index.owners[id(index.comments[0])]
        metadata = owner.setdefault('dedup', {})
        metadata['warnings'] = list(dict.fromkeys(metadata.get('warnings', []) + warnings))
    # Keep previously emitted completion counts accurate after later updates.
    for saved in rows:
        if scope(saved['data']) == scope(data) and saved['data'].get('record_type') == 'note_complete':
            saved['data']['comments_saved'] = len(index.comments)
            if added or warnings:
                saved['data']['comments_status'] = 'partial'
                saved['data'].setdefault('limitations', []).append('Comments changed after completion; recheck pending threads.')
    write_rows(path, rows)
    feedback = f'Saved {added} new comments, updated {updated}, skipped {skipped} duplicates; {len(index.comments)} unique records.'
    feedback += ' Canonical keys: ' + json.dumps(mappings, ensure_ascii=False) + '.'
    feedback += ' Parents: ' + json.dumps({c['comment_key']: c.get('parent_comment_key') for c in pending_parents}, ensure_ascii=False) + '.'
    if warnings:
        feedback += ' Review required: ' + ' '.join(dict.fromkeys(warnings))
    return feedback
