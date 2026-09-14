import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from thumbwork.agent_io import append_extract_output
from thumbwork.comment_dedup import normalized


class CommentDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'output.jsonl'
        self.step = 0

    def save(self, comments=None, note='n1', **fields):
        data = {'task': 'test', 'app': 'XHS', 'note_key': note,
                'search_keyword': '理想i9', 'record_type': 'comment_batch',
                'comments': comments or []}
        data.update(fields)
        self.step += 1
        return append_extract_output(str(self.path), self.step, {'data': data})

    @staticmethod
    def comment(key='c1', text='This is a distinctive comment about the car.', **fields):
        data = {'comment_key': key, 'author_name': 'Reader', 'comment_text': text,
                'parent_comment_key': None, 'publish_time': '昨天', 'location': '北京',
                'is_reply': False, 'text_complete': True}
        data.update(fields)
        return data

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def comments(self):
        return [c for row in self.rows() for c in row['data'].get('comments', [])]

    def test_repeated_batch_skips_same_ids_and_preserves_input(self):
        batch = [self.comment(), self.comment('c2', 'Another distinct comment.', author_name='Other')]
        original = copy.deepcopy(batch)
        self.save(batch)
        feedback = self.save(batch)
        self.assertEqual(len(self.comments()), 2)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(batch, original)
        self.assertIn('skipped 2 duplicates', feedback)

    def test_captured_run_duplicate_pattern_28_entries_become_18(self):
        # Synthetic content reproduces the live run's batch sizes, changed IDs,
        # wrong parents, missing metadata and final repeated six-comment batch.
        def c(key, **fields):
            return self.comment(key, f'Distinctive vehicle comment number {key}', **fields)
        root = c('c1')
        r5 = c('r5', parent_comment_key='c1', reply_to_author_name='Target')
        r6 = c('r6', parent_comment_key='c1', reply_to_author_name='Target',
               publish_time=None, location=None)
        c6 = c('c6', author_name='Target6')
        r8 = self.comment('r8', '是的，旗舰配置', parent_comment_key='c4',
                          reply_to_author_name='Target6', publish_time=None, location=None)
        final = [c6, dict(r8, comment_key='r8-alias', parent_comment_key='c6',
                         publish_time='昨天', location='北京'),
                 c('c7'), c('r9'), c('c8'), c('r10')]
        batches = [
            [root, self.comment('c2', text=None, text_complete=None)],
            [c('r1')],
            [c('c3'), c('r2'), c('c4'), c('r3')],
            [c('r4'), r5, r6],
            [dict(r5, comment_key='r5-alias', parent_comment_key='c4'),
             dict(r6, comment_key='r6-alias', parent_comment_key='c4', publish_time='昨天', location='北京'),
             c('c5'), c('r7'), c6, r8], final, final]
        self.assertEqual(sum(map(len, batches)), 28)
        for batch in batches:
            self.save(batch)
        self.assertEqual(len(self.comments()), 18)
        self.assertEqual(len({c['comment_key'] for c in self.comments()}), 18)
        self.save(record_type='note_complete', comments_status='complete', comments_saved=14,
                  limitations=['Reply thread remains unfinished'])
        completion = self.rows()[-1]['data']
        self.assertEqual(completion['comments_saved'], 18)
        self.assertEqual(completion['comments_status'], 'partial')

    def test_changed_ids_match_with_metadata_and_persist_aliases(self):
        self.save([self.comment()])
        feedback = self.save([self.comment('new-id')])
        self.assertIn('"new-id": "c1"', feedback)
        self.assertEqual(self.comments()[0]['source_comment_keys'], ['new-id'])
        # Each invocation reconstructs the index from disk, as after resume.
        self.save([self.comment('new-id', like_count='10')])
        self.assertEqual(len(self.comments()), 1)
        self.assertEqual(self.comments()[0]['like_count'], '10')

    def test_aliases_remap_replies_even_when_parent_is_later_in_batch(self):
        self.save([self.comment()])
        self.save([self.comment('reply', 'A reply', parent_comment_key='alias', is_reply=True),
                   self.comment('alias')])
        self.assertEqual(self.comments()[1]['parent_comment_key'], 'c1')
        self.save([self.comment('reply2', 'A second reply', parent_comment_key='alias', is_reply=True)])
        self.assertEqual(self.comments()[2]['parent_comment_key'], 'c1')

    def test_parent_alias_arriving_in_later_batch_resolves_saved_reply(self):
        self.save([self.comment('reply', 'A reply', parent_comment_key='alias')])
        self.assertIsNone(self.comments()[0]['parent_comment_key'])
        self.save([self.comment()])
        self.save([self.comment('alias')])
        reply = self.comments()[0]
        self.assertEqual(reply['parent_comment_key'], 'c1')
        self.assertNotIn('source_parent_comment_key', reply)

    def test_parent_collision_does_not_erase_previously_resolved_relationship(self):
        self.save([self.comment(), self.comment('reply', 'A reply', parent_comment_key='c1')])
        self.save([self.comment(text='Another comment reusing an ID')])
        self.assertEqual(self.comments()[1]['parent_comment_key'], 'c1')

    def test_partial_text_is_updated_in_place_and_never_shortened(self):
        self.save([self.comment(text='This is', text_complete=False)])
        full = self.comment(text='This is the full comment.', text_complete=True)
        self.assertIn('updated 1', self.save([full]))
        self.save([self.comment(text='This is', text_complete=False)])
        self.assertEqual(len(self.comments()), 1)
        self.assertEqual(self.comments()[0]['comment_text'], full['comment_text'])
        self.assertTrue(self.comments()[0]['text_complete'])

    def test_missing_text_is_filled_and_metadata_is_preserved(self):
        self.save([self.comment(text=None, text_complete=None)])
        self.save([self.comment(text='Now visible', text_complete=True, location=None)])
        self.assertEqual(len(self.comments()), 1)
        self.assertEqual(self.comments()[0]['comment_text'], 'Now visible')
        self.assertEqual(self.comments()[0]['location'], '北京')

    def test_same_id_different_text_preserves_conflict_and_replays_idempotently(self):
        self.save([self.comment(text='First text')])
        self.save([self.comment(text='Different text')])
        self.save([self.comment(text='Different text')])
        self.assertEqual([c['comment_key'] for c in self.comments()], ['c1', 'c1__2'])
        self.assertIn('conflicting', str(self.rows()))

    def test_same_id_different_author_is_not_discarded(self):
        self.save([self.comment()])
        self.save([self.comment(author_name='Someone else')])
        self.assertEqual(len(self.comments()), 2)

    def test_same_id_different_time_is_preserved_as_conflict(self):
        self.save([self.comment()])
        self.save([self.comment(publish_time='今天')])
        self.assertEqual(len(self.comments()), 2)

    def test_ambiguous_content_matches_are_not_arbitrarily_merged(self):
        self.save([self.comment('a', publish_time=None, location=None),
                   self.comment('b', publish_time=None, location=None)])
        self.save([self.comment('a'), self.comment('b')])
        self.save([self.comment('c')])
        self.assertEqual(len(self.comments()), 3)
        self.assertIn('ambiguous identity', str(self.rows()))

    def test_no_fuzzy_matching_of_different_text(self):
        self.save([self.comment(text='我觉得这辆车值得买')])
        self.save([self.comment('c2', text='我觉得这辆车不值得买')])
        self.assertEqual(len(self.comments()), 2)

    def test_identical_short_replies_in_different_threads_are_retained(self):
        self.save([self.comment('r1', '是的', parent_comment_key='p1', is_reply=True,
                                reply_to_author_name='Author')])
        self.save([self.comment('r2', '是的', parent_comment_key='p2', is_reply=True,
                                reply_to_author_name='Author')])
        self.assertEqual(len(self.comments()), 2)

    def test_same_text_different_note_author_time_or_location_is_retained(self):
        self.save([self.comment()])
        self.save([self.comment()], note='n2')
        self.save([self.comment('c2', author_name='Another reader')])
        self.save([self.comment('c3', publish_time='今天')])
        self.save([self.comment('c4', location='上海')])
        self.assertEqual(len(self.comments()), 5)

    def test_changed_ids_without_supporting_context_are_retained(self):
        self.save([self.comment(publish_time=None, location=None)])
        self.save([self.comment('c2', publish_time=None, location=None)])
        self.assertEqual(len(self.comments()), 2)
        self.assertIn('without sufficient context', str(self.rows()))

    def test_overlapping_long_replies_with_changed_parents_merge_but_flag_hierarchy(self):
        batch = [self.comment('r1', parent_comment_key='p1', is_reply=True),
                 self.comment('r2', 'A second distinctive comment about the car.', parent_comment_key='p1', is_reply=True)]
        self.save(batch)
        changed = [dict(c, comment_key='new-' + c['comment_key'], parent_comment_key='p2') for c in batch]
        self.assertIn('skipped 2 duplicates', self.save(changed))
        self.assertEqual(len(self.comments()), 2)
        self.assertIn('conflicting parent', str(self.rows()))

    def test_generic_short_replies_stay_separate_even_in_overlapping_batch(self):
        anchor = self.comment('anchor')
        self.save([anchor, self.comment('r1', '是的', parent_comment_key='p1', reply_to_author_name='Author')])
        self.save([anchor, self.comment('r2', '是的', parent_comment_key='p2', reply_to_author_name='Author')])
        self.assertEqual(len(self.comments()), 3)

    def test_ambiguous_parent_is_not_silently_attached(self):
        self.save([self.comment(text='First text'), self.comment(text='Other text')])
        self.save([self.comment('reply', 'A reply', parent_comment_key='c1')])
        self.assertIsNone(self.comments()[-1]['parent_comment_key'])
        self.assertEqual(self.comments()[-1]['source_parent_comment_key'], 'c1')

    def test_completion_uses_unique_count_and_downgrades_limitations(self):
        self.save([self.comment()])
        self.save([self.comment('alias')])
        self.save(record_type='note_complete', comments_saved=99,
                  comments_status='complete', limitations=['Replies remain collapsed'])
        result = self.rows()[-1]['data']
        self.assertEqual(result['comments_saved'], 1)
        self.assertEqual(result['comments_status'], 'partial')

    def test_completion_is_downgraded_for_unresolved_text_or_identity(self):
        self.save([self.comment(text=None, text_complete=None)])
        self.save(record_type='note_complete', comments_status='complete', limitations=[])
        self.assertEqual(self.rows()[-1]['data']['comments_status'], 'partial')

    def test_completion_count_stays_current_after_late_new_comment(self):
        self.save([self.comment()])
        self.save(record_type='note_complete', comments_status='complete', limitations=[])
        self.save([self.comment('c2', 'Something new', author_name='Other')])
        completion = next(r['data'] for r in self.rows() if r['data']['record_type'] == 'note_complete')
        self.assertEqual(completion['comments_saved'], 2)
        self.assertEqual(completion['comments_status'], 'partial')

    def test_search_keyword_is_taken_from_saved_note(self):
        self.save(record_type='note', note_detail={'author_name': 'Author'})
        self.save([self.comment()], search_keyword='理想 i9')
        self.assertEqual(self.rows()[-1]['data']['search_keyword'], '理想i9')

    def test_atomic_write_failure_preserves_previous_output(self):
        self.save([self.comment()])
        before = self.path.read_bytes()
        with patch('thumbwork.comment_dedup.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.save([self.comment('alias', like_count='10')])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.output.jsonl*')), [])

    def test_invalid_batch_or_corrupt_output_is_not_silently_dropped(self):
        self.save([self.comment()])
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.save([self.comment('good'), {'author_name': 'Missing key'}])
        self.assertEqual(self.path.read_bytes(), before)
        with self.path.open('a') as stream:
            stream.write('{broken')
        corrupted = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'invalid output.jsonl'):
            self.save([self.comment('new')])
        self.assertEqual(self.path.read_bytes(), corrupted)

    def test_spacing_normalization_preserves_meaningful_english_spaces(self):
        self.assertEqual(normalized('这个 i8 都做不到'), normalized('这个i8都做不到'))
        self.assertNotEqual(normalized('now here'), normalized('nowhere'))
        self.assertNotEqual(normalized('可以?'), normalized('可以!'))

    def test_generic_note_extraction_keeps_existing_dedup(self):
        data = {'notes': [{'author_name': 'A', 'note_title': 'Title'},
                          {'author_name': 'A', 'note_title': 'Title'}]}
        with patch('builtins.print'):
            append_extract_output(str(self.path), 1, {'data': data})
        self.assertEqual(len(self.rows()), 1)
