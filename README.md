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

Run folders use the local starting datetime and timezone, plus a unique suffix. Each run keeps its own input snapshots and results. The project root can be overridden with `--projects-dir` or `THUMBWORK_PROJECTS_DIR`; prompts must be in that root's `NAME/NAME.md` folder. The former `--task-name` option is removed.
