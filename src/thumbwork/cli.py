"""Task CLI with readable task listings and paths, JSON results, and stderr progress."""
from __future__ import annotations
import argparse
import contextlib
import getpass
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from . import __version__
from .models import ModelRegistry, client_for_profile, normalize_base_url, public_profile, verify_profile, validate_name
from .storage import atomic_json, config_root, projects_root


class CommandArgumentError(ValueError):
    def __init__(self, reason, help_text):
        super().__init__(reason)
        self.help_text = help_text


class CommandHelpFormatter(argparse.HelpFormatter):
    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            # Each command has its own row; omit the redundant choice-list row.
            return ''.join(self._format_action(command) for command in action._get_subactions())
        return super()._format_action(action)


class CommandExamplesFormatter(CommandHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Keep command examples on separate lines while retaining compact command rows."""


class CommandParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('formatter_class', CommandHelpFormatter)
        super().__init__(*args, **kwargs)

    def add_subparsers(self, **kwargs):
        kwargs.setdefault('title', 'commands')
        return super().add_subparsers(**kwargs)

    def error(self, message):
        raise CommandArgumentError(message, self.format_help())


def common(parser):
    # SUPPRESS lets options work before or after subcommands without overwriting a parent value.
    parser.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                        help='Output results as JSON for scripts and AI agents')


def task_output(parser):
    parser.add_argument('--projects-dir', default=argparse.SUPPRESS, metavar='PATH',
                        help='Projects folder for prompts and runs (default: THUMBWORK_PROJECTS_DIR or ~/thumbwork_projects)')


def parser():
    p = CommandParser(prog='thumbwork', description='Automate tasks on your Android phone with AI.',
                      epilog='Configuration is stored in ~/.thumbwork by default. Use thumbwork models path to show its location.')
    p.add_argument('--version', action='version', version=__version__)
    common(p)
    commands = p.add_subparsers(dest='command', required=True)
    models = commands.add_parser('models', help='Manage model configuration',
                                 formatter_class=CommandExamplesFormatter,
                                 epilog='Examples:\n'
                                        '  thumbwork models add            Add a model interactively\n'
                                        '  thumbwork models list           Find saved model names\n'
                                        '  thumbwork models default NAME   Set the default model\n'
                                        '  thumbwork models default        Show the current default\n'
                                        '\nReplace NAME with a saved name from models list.\n'
                                        'For command details: thumbwork models COMMAND --help')
    common(models)
    sub = models.add_subparsers(dest='model_command', required=True)
    add = sub.add_parser('add', help='Add and test a model interactively',
                        description='Enter the endpoint URL, API key and model name. Check capabilities and image input before saving. Smoke tests can be run separately afterward.'); common(add)
    add.add_argument('name', nargs='?', help='Optional saved profile name; defaults to the model name')
    add.add_argument('--base-url', help='API prefix or complete /chat/completions URL')
    add.add_argument('--api-key-env'); add.add_argument('--model-id')
    add.add_argument('--context-window', type=int)
    thinking = add.add_mutually_exclusive_group()
    thinking.add_argument('--thinking', choices=['on', 'off', 'default', 'unsupported'],
                          help='Turn thinking on/off, leave the server default, or declare no switch support')
    thinking.add_argument('--enable-thinking', choices=['true', 'false'], help='Alias for --thinking on/off')
    add.add_argument('--replace', action='store_true')
    for cmd, help_text in [('list', 'List configured models'), ('show', 'Show a model profile'), ('verify', 'Verify a model connection')]:
        child = sub.add_parser(cmd, help=help_text); common(child)
        if cmd != 'list': child.add_argument('name')
    default = sub.add_parser('default', help='Set with default NAME; omit NAME to show the current default',
                             description='Choose the saved model that run and smoke use when --model is omitted.',
                             formatter_class=CommandExamplesFormatter,
                             epilog='Examples:\n'
                                    '  thumbwork models default NAME   Set NAME as the default\n'
                                    '  thumbwork models default        Show the current default\n'
                                    '\nUse thumbwork models list to find saved model names.'); common(default)
    default.add_argument('name', nargs='?', metavar='NAME', help='Saved model name to set; omit to show the current default')
    path = sub.add_parser('path', help='Show the model configuration folder',
                          description='Print the model configuration folder without creating it. Defaults to ~/.thumbwork; advanced setups can override it with THUMBWORK_CONFIG_DIR.')
    common(path)
    run = commands.add_parser('run', help='List tasks or run a saved task or terminal prompt',
                              description='Without NAME or --prompt, list tasks in the projects folder.\n'
                                          'Run NAME using ~/thumbwork_projects/NAME/NAME.md, or pass\n'
                                          'instructions directly with --prompt. Saved task runs go in\n'
                                          'NAME/runs/; terminal prompt runs go in the projects folder\'s runs/.',
                              formatter_class=CommandExamplesFormatter,
                              epilog='Examples:\n'
                                     '  thumbwork run NAME\n'
                                     '  thumbwork run --prompt "Open Google and search for Li Auto"'); common(run)
    task_output(run)
    prompt = run.add_mutually_exclusive_group()
    prompt.add_argument('prompt', nargs='?', type=Path, metavar='NAME', help='Task name, NAME.md, or full project prompt path')
    prompt.add_argument('--prompt', dest='prompt_text', metavar='TEXT', help='Run instructions typed in the terminal, without a saved task file')
    run.add_argument('--model', help='Model profile (uses the saved default when omitted)')
    run.add_argument('--system-prompt-path', type=Path)
    run.add_argument('--max-steps', type=int, default=80)
    run.add_argument('--compact-threshold', type=float, default=0.70)
    run.add_argument('--debug', action='store_true',
                     help='Save screenshots, annotations, model traces, and logs in debug/ (off by default; can use substantial disk space)')
    resume = commands.add_parser('resume', help='Continue a run after human intervention',
                                 description='Continue an existing run using its saved settings, including whether --debug was enabled.'); common(resume)
    resume.add_argument('run_directory', type=Path); resume.add_argument('--max-steps', type=int)
    smoke = commands.add_parser('smoke', help='Run screenshot-only model qualification'); common(smoke)
    task_output(smoke)
    smoke.add_argument('--model', help='Model profile (uses the saved default when omitted)')
    smoke.add_argument('--task', choices=['all', 'open_weibo', 'extract_wikipedia', 'dead_phone_back'], default='all')
    smoke.add_argument('--mode', choices=['quick', 'qualify'], default='quick')
    smoke.add_argument('--debug', action='store_true',
                       help='Save model traces in debug/ (off by default; can use substantial disk space)')
    return p


def setup_input(label, validator=None, *, secret=False):
    while True:
        value = (getpass.getpass(label + ': ') if secret else input(label + ': ')).strip()
        if not value:
            print('Please enter a value.')
            continue
        try:
            return validator(value) if validator else value
        except ValueError as exc:
            print(str(exc))


def setup_context():
    print('The server did not report its maximum context length. Enter the token limit from your model/server configuration; a short test cannot measure it.')
    def positive_integer(value):
        try:
            count = int(value)
        except ValueError:
            raise ValueError('Enter a positive whole number of tokens, such as 32768.') from None
        if count <= 0:
            raise ValueError('Context length must be greater than zero.')
        return count
    return setup_input('Maximum context length (tokens)', positive_integer)


def setup_thinking(supported):
    if supported is True:
        print('The server reports a thinking switch. Choose on, off, or default to keep the server setting.')
        choices = ('on', 'off', 'default')
    else:
        print('The server did not report thinking-switch support. Choose on/off if your endpoint supports it, unsupported if it does not, or default if unsure (send no override).')
        choices = ('on', 'off', 'unsupported', 'default')
    def choose(value):
        value = value.lower()
        if value not in choices:
            raise ValueError('Choose ' + ', '.join(choices) + '.')
        return value
    return setup_input('Thinking mode [' + '/'.join(choices) + ']', choose)


def model_setup_result(name, profile):
    return {'name': name, **public_profile(profile),
            'suggested_smoke_command': f'thumbwork smoke --model {name} --task all --mode quick'}


def configure(args, registry):
    cmd = args.model_command
    if cmd == 'list':
        data = registry.read()
        return {'default_model': data.get('default_model'), 'models': {name: public_profile(registry.get(name)) for name in data['models']}}
    if cmd == 'default':
        if args.name is not None: registry.set_default(args.name)
        return {'default_model': registry.read().get('default_model')}
    if cmd == 'show': return {'name': args.name, **public_profile(registry.get(args.name))}
    if cmd == 'verify':
        existing = registry.get(args.name)
        # Rediscover server limits for profiles originally populated from metadata.
        if existing.get('context_source') == 'server_reported': existing.pop('context_window', None)
        interactive = sys.stdin.isatty()
        verified = verify_profile(existing, ask_context=setup_context if interactive else None,
                                   ask_thinking=setup_thinking if interactive else None, progress=print)
        registry.save(args.name, verified, replace=True)
        return model_setup_result(args.name, verified)
    if args.name is not None:
        validate_name(args.name)
    if args.name is not None and args.name in registry.read()['models'] and not args.replace:
        raise ValueError(f'Model profile {args.name} already exists; use --replace.')
    interactive = sys.stdin.isatty()
    def field(value, label, flag, validator=None):
        if value:
            return validator(value) if validator else value.strip()
        if interactive:
            return setup_input(label, validator)
        raise ValueError(f'Missing {flag}; pass it for noninteractive setup or run thumbwork models add in a terminal.')
    base = field(args.base_url, 'Endpoint URL', '--base-url', normalize_base_url)
    print(f'API base URL: {base}')
    if args.api_key_env:
        key = os.environ.get(args.api_key_env)
        if not key: raise ValueError('The named API-key environment variable is empty or missing.')
    elif interactive:
        key = setup_input('API key (hidden)', secret=True)
    else:
        raise ValueError('Noninteractive setup requires --api-key-env VARIABLE.')
    model = field(args.model_id, 'Model name (server model ID)', '--model-id')
    name = args.name or re.sub(r'[^\w.-]', '-', model).strip('.-')[:100]
    validate_name(name)
    if name in registry.read()['models'] and not args.replace:
        if not interactive:
            raise ValueError(f'Model profile {name} already exists; supply another profile name or use --replace.')
        print(f'A profile named {name} already exists. Enter a different name to keep it.')
        def unused_name(value):
            validate_name(value)
            if value in registry.read()['models']:
                raise ValueError('That profile name is already in use.')
            return value
        name = setup_input('Save as profile name', unused_name)
    profile = {'base_url': base, 'api_key': key, 'model_id': model}
    if args.context_window is not None: profile['context_window'] = args.context_window
    if args.enable_thinking is not None: profile['enable_thinking'] = args.enable_thinking == 'true'
    if args.thinking is not None: profile['thinking_mode'] = args.thinking
    verified = verify_profile(profile, ask_context=setup_context if interactive else None,
                               ask_thinking=setup_thinking if interactive else None, progress=print)
    registry.save(name, verified, replace=args.replace)
    return model_setup_result(name, verified)


def smoke_suite(args, profile):
    from .smoke_runner import default_scenario_paths, load_scenario, run_scenario
    paths = default_scenario_paths()
    selected = list(paths) if args.task == 'all' else [args.task]
    root = projects_root(getattr(args, 'projects_dir', None)) / '_smoke' / 'runs' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8])
    root.mkdir(parents=True)
    system = (Path(__file__).parent / 'resources/system_prompt.md').read_text(encoding='utf-8')
    results = []
    repetitions = 3 if args.mode == 'qualify' else 1
    for name in selected:
        for repetition in range(1, repetitions + 1):
            client = client_for_profile(profile, root / 'debug' / name / str(repetition) if args.debug else None)
            result = run_scenario(load_scenario(paths[name]), system, client, context_window=profile["context_window"])
            result['repetition'] = repetition
            results.append(result)
            print(f"{'PASS' if result['passed'] else 'FAIL'} {name}: {result.get('message', '')}")
    verdicts = {name: sum(r['passed'] for r in results if r['task'] == name) >= (2 if repetitions == 3 else 1) for name in selected}
    passed = all(verdicts.values())
    code = 0 if passed else (2 if any(r.get('failure_category') == 'infrastructure' for r in results) else 1)
    report = {'status': 'success' if passed else 'error' if code == 2 else 'failure', 'passed': passed, 'mode': args.mode, 'model': args.model, 'run_directory': str(root), 'task_verdicts': verdicts, 'results': results}
    atomic_json(root / 'result.json', report)
    return code, report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    as_json = '--json' in argv
    p = parser()
    try:
        args = p.parse_args(argv)
        if args.command == 'run' and args.prompt_text is not None and not args.prompt_text.strip():
            raise CommandArgumentError('--prompt must not be empty or whitespace', p.format_help())
        if args.command == 'run' and args.prompt is None and args.prompt_text is None:
            from .workspace import available_tasks
            root = projects_root(getattr(args, 'projects_dir', None))
            tasks = [{'name': prompt.stem, 'prompt_path': str(prompt)} for prompt in available_tasks(root)]
            if as_json:
                print(json.dumps({'projects_dir': str(root), 'tasks': tasks}, ensure_ascii=False))
            elif tasks:
                print(f'Available tasks in {root}:')
                for task in tasks:
                    print(f'  {task["name"]}')
                print('\nRun a task: thumbwork run NAME')
            else:
                print(f'No tasks found in {root}.')
                print(f'Create a prompt at {root}/NAME/NAME.md, then run thumbwork run NAME.')
            if not as_json:
                print('Or run a terminal prompt: thumbwork run --prompt "Your instructions"')
            return 0
        if args.command == 'models' and args.model_command == 'path':
            path = str(config_root())
            print(json.dumps({'config_dir': path}, ensure_ascii=False) if as_json else path)
            return 0
        registry = ModelRegistry()
        with contextlib.redirect_stdout(sys.stderr):
            if args.command in {'run', 'smoke'}:
                args.model = registry.resolve_name(args.model)
            if args.command == 'models':
                output, code = configure(args, registry), 0
            elif args.command == 'smoke':
                code, output = smoke_suite(args, registry.get(args.model))
            else:
                from .runtime import run_command
                result = run_command(args, registry)
                output, code = result.to_dict(), result.exit_code
    except KeyboardInterrupt:
        output, code = {'status': 'interrupted', 'reason': 'Interrupted by caller'}, 130
    except EOFError:
        output, code = {'status': 'interrupted', 'reason': 'Setup canceled before saving'}, 130
    except CommandArgumentError as exc:
        if not as_json:
            print(exc.help_text, end='', file=sys.stderr)
            return 2
        output, code = {'status': 'error', 'reason': str(exc), 'help': exc.help_text}, 2
    except SystemExit as exc:
        if exc.code in (0, None): return 0
        output, code = {'status': 'error', 'reason': 'Invalid command arguments' if isinstance(exc.code, int) else str(exc.code)}, 2
    except (OSError, ValueError, RuntimeError) as exc:
        output, code = {'status': 'error', 'reason': str(exc)}, 2
    if code == 0 and not as_json and args.command == 'models' and args.model_command in {'add', 'verify'} and sys.stdin.isatty():
        print(f'Model {output["name"]} is ready.')
        print(f'Context: {output["context_window"]:,} tokens. Thinking: {output["thinking_mode"]}. Image checks passed.')
        print(f'Optional: run the baseline smoke test later: {output["suggested_smoke_command"]}')
        print(f'To use it by default: thumbwork models default {output["name"]}')
    elif as_json:
        print(json.dumps(output, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return code
