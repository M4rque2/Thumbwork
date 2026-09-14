"""Synchronous Android task execution and explicit human-handoff resume."""
from __future__ import annotations
import contextlib
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from .agent_io import AdbTools, annotate_screenshot, append_extract_output, execute_action, format_turn_response, parse_turn_response, rescale_coordinates
from .context_manager import build_collection_memory, build_messages, load_task_prompt_arg
from .models import client_for_profile
from .compaction import ContextManager
from .llm_client import ContextOverflowError
from .storage import atomic_json
from .workspace import create_run, file_lock, load_checkpoint, prune_screenshots

EXIT_CODES = {'success': 0, 'failure': 1, 'incomplete': 1, 'error': 2, 'needs_input': 3, 'interrupted': 130}


@dataclass
class RunResult:
    status: str
    reason: str
    run_directory: str
    output_path: str
    step_count: int
    extraction_count: int
    required_action: str | None = None

    @property
    def exit_code(self): return EXIT_CODES[self.status]
    def to_dict(self): return asdict(self)


def scrub(value, secret):
    if not secret: return value
    if isinstance(value, str): return value.replace(secret, '[REDACTED]')
    if isinstance(value, list): return [scrub(item, secret) for item in value]
    if isinstance(value, dict): return {scrub(k,secret):scrub(v,secret) for k,v in value.items()}
    return value


class ProgressWriter:
    def __init__(self, terminal, logfile, secret):
        self.terminal, self.logfile, self.secret = terminal, logfile, secret
    def write(self, text):
        cleaned = scrub(text, self.secret)
        self.terminal.write(cleaned)
        if self.logfile: self.logfile.write(cleaned)
        return len(text)
    def flush(self):
        self.terminal.flush()
        if self.logfile: self.logfile.flush()
    def isatty(self): return self.terminal.isatty()


def prepare_device(adb):
    print('[PREFLIGHT] Checking device state')
    adb.get_device_state()
    adb.wake_if_needed()
    if not adb.unlock_if_needed(max_attempts=3):
        print('[WARN] Device may still be locked; the model can request human input.')
    adb.go_home_default_page()


def finish(run, state, status, reason, required_action=None, secret='', preserve_checkpoint=False):
    state['status'] = status
    state['required_action'] = scrub(required_action, secret)
    state = scrub(state, secret)
    if not preserve_checkpoint:
        atomic_json(run / 'checkpoint.json', state)
    output = run / 'output.jsonl'
    count = 0
    if output.exists():
        with output.open(encoding='utf-8') as stream:
            count = sum(bool(line.strip()) for line in stream)
    result = RunResult(status, scrub(reason,secret), str(run), str(output), state['next_step'], count, scrub(required_action,secret))
    atomic_json(run / 'result.json', result.to_dict())
    return result


def run_loop(run, state, adb, vlm, *, resume=False, secret='', context_window=32768):
    run = Path(run).resolve()
    try:
        task = load_task_prompt_arg('', str(run / 'input/task.md'))
        system = (run / 'input/system.md').read_text(encoding='utf-8')
        manager = ContextManager(vlm, context_window, state['compact_threshold'], state.get('token_calibration',1.0))
        manager.counter.counter_calibration = state.get('counter_calibration',1.0)
        if not resume: prepare_device(adb)
        # A human may have changed the page; pending feedback must not suppress a fresh capture.
        if resume: state['pending_feedback'] = None
        for step in range(state['next_step'], state['max_steps']):
            state['status'] = 'running'
            state['next_step'] = step + 1
            print(f'[STEP {step + 1}/{state["max_steps"]}]')
            feedback = state.get('pending_feedback')
            if feedback is None or not state.get('last_screenshot'):
                directory = 'debug/screenshots' if state['debug'] else 'state'
                relative = f'{directory}/screen-{step}.png'
                image = run / relative; image.parent.mkdir(parents=True, exist_ok=True)
                if not adb.get_screenshot(str(image)):
                    raise RuntimeError('Device screenshot capture failed')
                state['last_screenshot'] = relative
            else:
                relative = state['last_screenshot']
                image = run / relative
            def build(current):
                history = [{**item, 'image': str(run / item['image'])} for item in current['history']]
                return build_messages(str(image), system, task, history,
                    feedback=feedback, collection_memory=build_collection_memory(str(run/'output.jsonl')),
                    previous_expectation=current['previous_expectation'], summary=current.get('summary'))
            messages = manager.prepare(state,build)
            # Save compacted state before pruning screenshots or making another model call.
            atomic_json(run/'checkpoint.json', scrub(state,secret))
            prune_screenshots(run,state)
            try:
                result = vlm.invoke(messages)
            except ContextOverflowError:
                messages = manager.prepare(state,build,force=True)
                atomic_json(run/'checkpoint.json',scrub(state,secret))
                prune_screenshots(run,state)
                result = vlm.invoke(messages)
            manager.counter.observe(messages,result.usage)
            state['token_calibration'] = manager.counter.calibration
            state['counter_calibration'] = manager.counter.counter_calibration
            state['context_usage'] = {'tokens': manager.counter.count(messages), 'source': manager.counter.last_source,
                'context_window':context_window, 'reported_prompt_tokens':result.usage.get('prompt_tokens')}
            output = scrub(result.content, secret)
            state['pending_feedback'] = None
            print(f'[MODEL OUTPUT]\n{output}')
            entry = {'image': relative, 'output': output, 'previous_expectation': state['previous_expectation']}
            try:
                response = parse_turn_response(output)
            except ValueError as exc:
                print(f'[MODEL PROTOCOL] {exc}')
                state['history'].append(entry)
                atomic_json(run/'checkpoint.json', scrub(state,secret))
                continue
            entry['output'] = format_turn_response(response)
            state['history'].append(entry)
            state['previous_expectation'] = response.get('expectation') or None
            args = response['tool_call']['arguments']
            action = args['action']
            if action == 'extract':
                if isinstance(args.get('data'), dict) and args['data']:
                    try:
                        feedback = append_extract_output(str(run/'output.jsonl'), step, args, response.get('summary'))
                        state['pending_feedback'] = '[EXTRACT FEEDBACK] ' + (feedback or 'Data recorded. Decide the next action.')
                    except ValueError as exc:
                        state['pending_feedback'] = f'[EXTRACT FEEDBACK] Extraction rejected: {exc}. Correct the data and retry.'
                else:
                    state['pending_feedback'] = '[EXTRACT FEEDBACK] Extraction failed: data must be a non-empty object.'
            elif action == 'interact':
                required = args.get('text') or 'Complete the required action on the phone.'
                return finish(run,state,'needs_input','Human intervention required',required,secret)
            elif action == 'terminate':
                return finish(run,state,args['status'],args.get('summary') or response.get('summary') or 'Model terminated the task',secret=secret)
            else:
                width, height = adb.get_display_size()
                try:
                    scaled = rescale_coordinates(args, width, height)
                except ValueError as exc:
                    print(f'[ACTION VALIDATION] {exc}')
                    atomic_json(run/'checkpoint.json', scrub(state,secret))
                    continue
                execute_action(scaled, adb)
                if state['debug']:
                    annotations = run/'debug/annotations'; annotations.mkdir(exist_ok=True)
                    annotate_screenshot(str(image),scaled,str(annotations/f'action-{step}.png'))
                time.sleep(2)
            atomic_json(run/'checkpoint.json', scrub(state,secret))
            prune_screenshots(run,state)
        return finish(run,state,'incomplete','Maximum step count reached',secret=secret)
    except KeyboardInterrupt:
        return finish(run,state,'interrupted','Interrupted by caller; partial output preserved',secret=secret)
    except (Exception, SystemExit) as exc:
        return finish(run,state,'error',str(exc),secret=secret)


def run_command(args, registry):
    resuming = args.command == 'resume'
    if resuming:
        run = args.run_directory.expanduser().resolve()
        if not (run/'checkpoint.json').is_file(): raise ValueError('Run checkpoint not found')
    else:
        # Validate profile before creating any project artifacts.
        registry.get(args.model)
        run = create_run(args.prompt, model=args.model,
            projects_dir=getattr(args,'projects_dir',None), system_prompt_path=args.system_prompt_path,
            max_steps=args.max_steps, compact_threshold=args.compact_threshold, debug=args.debug)
    with file_lock(run/'.run.lock'):
        state = load_checkpoint(run)
        if resuming:
            if state['status'] not in {'needs_input','incomplete'}:
                raise ValueError('Only needs_input or step-limited incomplete runs can resume; start a new run otherwise.')
            if args.max_steps is not None:
                if args.max_steps <= state['next_step'] or args.max_steps < state['max_steps']:
                    raise ValueError('--max-steps must increase the cumulative step budget beyond completed steps.')
                state['max_steps'] = args.max_steps
            if state['next_step'] >= state['max_steps']:
                raise ValueError('No steps remain; increase the cumulative budget with --max-steps.')
        secret = ''
        try:
            profile = registry.get(state['model']); secret = profile['api_key']
            identity = hashlib.sha256((profile['base_url']+'\n'+profile['model_id']).encode()).hexdigest()
            if state.get('model_identity',identity) != identity:
                raise ValueError('The model profile endpoint/model changed since this run; restore it before resuming.')
            state['model_identity'] = identity
            trace = run/'debug/llm-tracer' if state['debug'] else None
            with contextlib.ExitStack() as stack:
                logfile = stack.enter_context((run/'debug/run.log').open('a',encoding='utf-8',buffering=1)) if state['debug'] else None
                writer = ProgressWriter(sys.stderr,logfile,secret)
                stack.enter_context(contextlib.redirect_stdout(writer))
                stack.enter_context(contextlib.redirect_stderr(writer))
                client = client_for_profile(profile,trace)
                adb = AdbTools()
                return run_loop(run,state,adb,client,resume=resuming,secret=secret,context_window=profile['context_window'])
        except KeyboardInterrupt:
            return finish(run,state,'interrupted','Interrupted by caller',secret=secret)
        except (Exception,SystemExit) as exc:
            return finish(run,state,'error',str(exc),secret=secret,preserve_checkpoint=resuming)
