"""Task identity, immutable input snapshots and resumable run files."""
from __future__ import annotations
import contextlib
import json
import math
import os
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from .context_manager import MARKDOWN_IMAGE_RE, parse_task_prompt_markdown
from .storage import atomic_json, projects_root, file_lock

RESOURCE_ROOT = Path(__file__).parent / 'resources'


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError(f'Cannot read valid JSON from {path}') from None




def resolve_task_prompt(prompt, projects_dir=None):
    """Resolve one uniquely named prompt in <projects>/<name>/<name>.md."""
    root = projects_root(projects_dir)
    supplied = Path(prompt).expanduser()
    filename = supplied.name
    if len(supplied.parts) == 1 and not filename.endswith('.md'):
        filename += '.md'
    name = Path(filename).stem
    if (not name.strip() or name in {'.', '..'} or filename != name + '.md'
            or any(c in name for c in '/\\\0') or any(ord(c) < 32 for c in name)):
        raise ValueError('Use a task name or a Markdown prompt ending in .md.')
    if unicodedata.normalize('NFKC', name).casefold() in {'_smoke', 'runs'}:
        raise ValueError(f'{name} is reserved; choose a different prompt filename.')
    canonical = root / name / filename
    source = canonical if len(supplied.parts) == 1 else supplied.resolve()
    if source != canonical or not source.resolve().is_relative_to(root):
        raise ValueError(f'Move the task prompt to {canonical}, then run thumbwork run "{name}".')
    if not source.is_file():
        raise ValueError(f'Task prompt not found: {canonical}. Create this file, then run thumbwork run "{name}".')
    # Run snapshots are historical copies, not additional task definitions.
    key = unicodedata.normalize('NFKC', filename).casefold()
    for directory, folders, files in os.walk(root):
        folders[:] = [folder for folder in folders if folder != 'runs']
        for other in files:
            candidate = Path(directory) / other
            if unicodedata.normalize('NFKC', other).casefold() == key and candidate != source:
                raise ValueError(f'Duplicate task prompt name {filename}: {source} and {candidate}. Rename one prompt and its task folder; task names must be unique.')
    return source


def available_tasks(projects_dir=None):
    """List canonical task prompts without creating folders or starting runs."""
    root = projects_root(projects_dir)
    if not root.exists():
        return []
    prompts = []
    for task in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if unicodedata.normalize('NFKC', task.name).casefold() in {'_smoke', 'runs'}:
            continue
        if task.is_dir() and (task / (task.name + '.md')).is_file():
            prompts.append(resolve_task_prompt(task / (task.name + '.md'), root))
    return prompts


def create_run(prompt_path=None, *, model, prompt_text=None, projects_dir=None,
               system_prompt_path=None, max_steps=80, compact_threshold=0.70, debug=False):
    if type(max_steps) is not int or max_steps <= 0: raise ValueError('--max-steps must be positive')
    if not 0.1 <= compact_threshold <= 0.9: raise ValueError('--compact-threshold must be between 0.1 and 0.9')
    if (prompt_path is None) == (prompt_text is None):
        raise ValueError('Provide either a saved task name or --prompt TEXT')
    source = resolve_task_prompt(prompt_path, projects_dir) if prompt_text is None else None
    text = source.read_text(encoding='utf-8').strip() if source else prompt_text.strip()
    if not text: raise ValueError('Task prompt is empty')
    image_base = source.parent if source else Path.cwd()
    # Validate every image before creating a persistent run directory.
    try: parse_task_prompt_markdown(text, source or image_base / 'task.md')
    except SystemExit as exc: raise ValueError(str(exc)) from None
    system = Path(system_prompt_path).expanduser().read_text(encoding='utf-8') if system_prompt_path else (RESOURCE_ROOT / 'system_prompt.md').read_text(encoding='utf-8')
    if not system.strip(): raise ValueError('System prompt is empty')
    name = source.stem if source else 'inline'
    task = source.parent if source else projects_root(projects_dir)
    with file_lock(task / '.task.lock') if source else contextlib.nullcontext():
        if source:
            manifest = task / 'task.json'
            if manifest.exists() and read_json(manifest).get('source_path') != str(source):
                raise ValueError(f'Task name {name} belongs to another prompt. Rename the prompt and its task folder to use a unique name.')
            atomic_json(manifest, {'version': 1, 'name': name, 'source_path': str(source)})
        started = datetime.now().astimezone()
        run = task / 'runs' / (started.strftime('%Y-%m-%d_%H-%M-%S_%f%z') + '-' + uuid4().hex[:8])
        inputs = run / 'input'; inputs.mkdir(parents=True)
        try:
            count = 0
            def copy_image(match):
                nonlocal count
                original = Path(match.group(1).strip())
                if not original.is_absolute(): original = image_base / original
                target = inputs / 'assets' / f'image-{count}{original.suffix.lower()}'
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(original.resolve(), target)
                count += 1
                return f'![Reference image]({target.relative_to(inputs).as_posix()})'
            (inputs / 'task.md').write_text(MARKDOWN_IMAGE_RE.sub(copy_image, text), encoding='utf-8')
            (inputs / 'system.md').write_text(system, encoding='utf-8')
            (run / 'state').mkdir()
            if debug: (run / 'debug').mkdir()
            (run / 'output.jsonl').touch()
            checkpoint = {'version': 1, 'status': 'ready', 'model': model, 'max_steps': max_steps,
                'task_name': name, 'started_at': started.isoformat(),
                'compact_threshold': compact_threshold, 'debug': debug, 'next_step': 0,
                'history': [], 'summary': '', 'previous_expectation': None, 'pending_feedback': None,
                'last_screenshot': None, 'token_calibration': 1.0}
            atomic_json(run / 'checkpoint.json', checkpoint)
        except BaseException:
            shutil.rmtree(run)
            raise
    return run


def load_checkpoint(run):
    run = Path(run).expanduser().resolve()
    state = read_json(run / 'checkpoint.json')
    if not isinstance(state, dict) or state.get('version') != 1:
        raise ValueError('Unsupported checkpoint version')
    valid = (
        isinstance(state.get('history'), list)
        and type(state.get('next_step')) is int and state['next_step'] >= 0
        and type(state.get('max_steps')) is int and state['max_steps'] > 0
        and isinstance(state.get('model'), str) and bool(state['model'])
        and state.get('status') in {'ready','running','success','failure','incomplete','needs_input','error','interrupted'}
        and type(state.get('debug')) is bool
        and isinstance(state.get('summary'), str)
        and type(state.get('compact_threshold')) in (int,float)
        and 0.1 <= state['compact_threshold'] <= 0.9
    )
    if not valid: raise ValueError('Invalid checkpoint state')
    for key in ('token_calibration','counter_calibration'):
        value = state.get(key,1.0)
        if type(value) not in (int,float) or not math.isfinite(value) or value < 1:
            raise ValueError('Invalid checkpoint token calibration')
    for key in ('previous_expectation','pending_feedback'):
        if state.get(key) is not None and not isinstance(state[key],str):
            raise ValueError('Invalid checkpoint feedback or expectation')
    def local_image(value):
        if not isinstance(value,str): return False
        path = Path(value)
        return bool(value) and not path.is_absolute() and '..' not in path.parts and (run/path).resolve().is_relative_to(run)
    if state.get('last_screenshot') is not None and not local_image(state['last_screenshot']):
        raise ValueError('Invalid checkpoint screenshot path')
    for item in state['history']:
        if not isinstance(item,dict) or not isinstance(item.get('output'),str) or not local_image(item.get('image')):
            raise ValueError('Invalid checkpoint history')
    return state


def prune_screenshots(run, state):
    if state['debug']: return
    retained = {entry['image'] for entry in state['history']}
    if state.get('last_screenshot'): retained.add(state['last_screenshot'])
    for path in (Path(run) / 'state').glob('screen-*.png'):
        if path.relative_to(run).as_posix() not in retained: path.unlink()
