# Thumbwork
A complete phone-use agent, available through a CLI. Thumbwork observes screenshots, acts through taps and swipes, and asks for human help when it cannot proceed. Your coding agent handles setup, helps prepare tasks and schedule runs, and analyzes the results.

Add a model interactively:

```bash
thumbwork models add
```

Enter the endpoint URL, hidden API key, and server model name, in that order. Full `/chat/completions` URLs are normalized automatically. If the endpoint does not report its context limit or thinking-switch settings, setup asks you to supply them. Thinking choices are `on`, `off`, `unsupported`, and `default` (send no override when unsure). `on` and `off` use `chat_template_kwargs.enable_thinking`; successful requests alone do not prove the switch changes model behavior.

Setup checks capabilities and scores two image-understanding probes. Image input is required. The profile is saved after those checks pass; baseline smoke tests do not block setup. A failed replacement leaves the existing profile intact. Use `models add NAME` for a custom saved name, or omit NAME to derive it from the server model name. `models verify NAME` repeats the checks. Setup suggests an optional smoke command to run later:

```bash
thumbwork smoke --model NAME --task all --mode quick
```

The manual smoke test runs the three bundled screenshot scenarios without operating a phone. Its result does not prevent saving or using a model.

Model configuration is stored automatically in `~/.thumbwork`. To print the effective absolute path without creating the folder:

```bash
thumbwork models path
```

Use `thumbwork models path --json` for a JSON object with a `config_dir` field. Tests and advanced setups can select a separate folder through the `THUMBWORK_CONFIG_DIR` environment variable. The projects folder defaults to `~/thumbwork_projects` and can be changed with `THUMBWORK_PROJECTS_DIR` or the `--projects-dir` option after `run` or `smoke`. For example: `thumbwork run collect --projects-dir ~/Documents/thumbwork_projects`. Resuming a task uses its existing run directory.

Running Thumbwork without a command, or with invalid command arguments, displays the relevant help immediately and exits with code 2. With `--json`, argument errors return a JSON object containing `status`, `reason`, and the full help text in `help`.

See the [macOS quick start](QUICK_START_Non_TECH_MACOS_zh.md) for setup and task examples.

List available tasks, then run one by its unique prompt name:

```bash
thumbwork run
thumbwork run xhs_search --debug
```

Or type a prompt directly in the terminal without creating a task file:

```bash
thumbwork run --prompt "Open Google app, search for keyword 'Li Auto', return the first 10 results"
```

Use either a saved task name or `--prompt TEXT`, not both. Terminal prompts use the same model and run options as saved tasks. Their input snapshots and results are saved in `~/thumbwork_projects/runs/<starting-datetime>/` (or the selected projects folder), and can be resumed with `thumbwork resume RUN_DIRECTORY`. They do not appear in the saved task list. Markdown image paths in terminal prompts are resolved relative to the current terminal directory and copied into the run snapshot.

Store each prompt at `~/thumbwork_projects/NAME/NAME.md`, with any referenced images beside it (for example, in `assets/`). `NAME`, `NAME.md`, and the full project prompt path select the same task. The filename determines the task name; duplicate prompt filenames within the projects folder are rejected, including case variants. Historical run snapshots are excluded from that check.

```text
~/thumbwork_projects/NAME/
├── NAME.md
├── assets/
└── runs/
    └── YYYY-MM-DD_HH-MM-SS_microseconds+timezone-id/
        ├── input/
        ├── output.jsonl
        ├── result.json
        ├── checkpoint.json
        ├── state/
        └── debug/          # only with --debug
```

Run folders use the local starting datetime and timezone, plus a unique suffix. Each run keeps its own input snapshots and results. The project root can be overridden with `--projects-dir` or `THUMBWORK_PROJECTS_DIR`; saved task prompts must be in that root's `NAME/NAME.md` folder. The former `--task-name` option is removed.

### Text entry on the phone

The `type` action replaces text in the focused input field; an empty string clears it. It does not press Enter. The agent must check the next screenshot and submit the search or form with a separate action. If no editable field is focused, the agent receives feedback to tap the input field before retrying.

ADB Keyboard is the preferred input method. Text is sent using its [UTF-8 Base64 input protocol](https://github.com/senzhk/ADBKeyBoard#usage-example), preserving spaces, Chinese characters, and punctuation. Clipboard and plain ADB input are fallbacks; unsupported text produces an error instead of silently dropping characters. The original keyboard is restored after a temporary switch, including when typing fails.

Thumbwork waits for ADB Keyboard to be selected and ready before clearing or sending text. Fallbacks are used only when the device advertises the required commands; unsupported clipboard and select-all commands are skipped or reported before modifying the field. A missing input capability pauses the run with setup instructions, so you can enable ADB Keyboard and use `resume` afterward. Device command timeouts also pause for inspection because text may have been partially entered.

Typing logs identify the method used and report `status=sent verification=pending`. The agent then checks a fresh screenshot. Gray placeholder text in an empty field should not be mistaken for text that failed to clear. If restoring the previous keyboard fails, Thumbwork reports it without automatically entering the text again.
