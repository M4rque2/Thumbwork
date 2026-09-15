---
name: thumbwork-phone-use
description: Set up Thumbwork on a fresh computer and carry out Android phone tasks through its CLI. Install the CLI and ADB, configure a vision model, connect a USB phone, prepare ADB Keyboard, and write, test, and refine reusable task prompts. Use for Thumbwork setup, phone task execution, and diagnosing or resuming its runs.
---

# Thumbwork phone use

Turn a user's phone task into a verified Thumbwork run. The user installs this skill; you handle software setup and prompt preparation. Ask the user for information and physical phone actions when needed. Installing this skill does not require Thumbwork, Python, Git, ADB, or a model to exist already.

## Start at the first missing capability

Confirm that commands execute on the host connected to the phone. A cloud/container session without access to that host's ADB cannot complete USB setup. Use the host's available file, shell, and image-viewing tools; no agent-specific tool names are required. If image inspection is unavailable, ask the user to inspect the relevant phone state rather than claim visual verification.

Inspect the host OS, available `thumbwork` command, model profiles, and ADB connection before changing anything. Reuse working components and existing task files. Do not reinstall or replace a profile merely because a new conversation started.

For a fresh machine, follow [setup](references/setup.md) in order:

1. Install Python if needed, then the Thumbwork CLI in an isolated environment.
2. Collect the endpoint and server model ID; let the user enter the API key privately. Verify image understanding and choose a saved model.
3. Install Android SDK Platform-Tools and make `adb` available to the CLI.
4. Guide the user to connect and unlock an Android phone over USB and approve debugging. Verify the intended device.
5. Install and enable ADB Keyboard, then verify actual text entry through a small CLI run.
6. Help the user express their task, run it, inspect the evidence, and refine it using [task iteration](references/task-iteration.md).

Perform authorized installations and fixes yourself. Do not hand the user a long command checklist. Ask for one immediate human action at a time, and continue from the last verified stage afterward. If a required value or physical action is missing, say exactly what is needed and retain the completed setup state.

## Operate through the CLI

- Use `thumbwork --help` and subcommand help to check the installed interface. The references describe this repository's CLI; resolve version mismatches before inventing options.
- Use `--json` for machine-readable results. Progress is on stderr. Read `result.json` and `output.jsonl` from the returned `run_directory`; the latter stores extracted records under each line's `data` field.
- Use direct ADB for setup, connection checks, and diagnosis. Run the actual user task through Thumbwork so that the tested prompt, phone actions, and results are reproducible.
- Keep API keys out of chat, prompts, shell command arguments, and project files. Use the CLI's hidden terminal entry or a user-provided secret environment variable. Show profiles with `models show`, not by reading the raw configuration file.
- Setup permission covers setup. Carry task constraints into the prompt. Do not turn a setup test into an unsolicited message, purchase, deletion, or other consequential action.

## Completion

Report setup readiness separately from task validation. A model probe, screenshot smoke test, or `success` status alone does not establish that the user's task was accomplished. Validate the requested output or visible final state. Deliver the saved prompt path, run result path, exact rerun command (including any required environment), and any remaining blocker. Describe successful trials as observed evidence, never a guarantee of future success.
