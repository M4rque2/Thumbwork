# Thumbwork

English | [简体中文](README.zh-CN.md)

A complete phone-use agent, available through a CLI. Thumbwork observes screenshots, acts through taps and swipes, and asks for human help when it cannot proceed. Your coding agent handles setup, helps prepare tasks and schedule runs, and analyzes the results.

## Start with the skill

On a fresh computer, **install only the [thumbwork-phone-use skill](skills/thumbwork-phone-use)**. The same folder works with the local skill formats below; no Thumbwork CLI, Python, Git, Node.js, or ADB installation is required beforehand. Your coding agent must already be installed and able to run local commands on the computer that will connect to the phone.

### Install in your coding agent

1. Download this repository using **Code → Download ZIP** on [GitHub](https://github.com/M4rque2/Thumbwork), then extract it.
2. Copy the entire `skills/thumbwork-phone-use` folder, including `references/`, to **one** destination for your agent in the table below. Create missing parent folders.
3. Start a new agent session, then invoke the skill. Verify the agent can read both `SKILL.md` and its setup reference before beginning installation.

`~` means your user home folder, such as `/Users/you`, `/home/you`, or `C:\Users\you`. Paths beginning with `./` belong inside the project you open in the agent.

| Agent | User-wide destination | Project-only alternative | Invoke after installation |
| --- | --- | --- | --- |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/thumbwork-phone-use/` | `./.claude/skills/thumbwork-phone-use/` | `/thumbwork-phone-use` |
| [Codex](https://learn.chatgpt.com/docs/build-skills) | `~/.agents/skills/thumbwork-phone-use/` | `./.agents/skills/thumbwork-phone-use/` | `$thumbwork-phone-use` in CLI/IDE, or select the skill in the app |
| [Pi](https://pi.dev/docs/latest/skills) | `~/.pi/agent/skills/thumbwork-phone-use/` | `./.pi/skills/thumbwork-phone-use/` | `/skill:thumbwork-phone-use` |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md) | `~/.dsh/skills/thumbwork-phone-use/` | `./.dsh/skills/thumbwork-phone-use/` | Ask it to use `thumbwork-phone-use` |
| [WorkBuddy, local project mode](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project) | `~/.codebuddy/skills/thumbwork-phone-use/` | `./.codebuddy/skills/thumbwork-phone-use/` | Ask it to use `thumbwork-phone-use` |

These paths follow the agents' documented discovery conventions; they are not a claim that live phone onboarding has been tested in every host. DeepSeek Harness needs its filesystem skill provider and skill tool enabled; customized/minimal presets may omit them. WorkBuddy's local configuration is compatible with [CodeBuddy skill directories](https://www.codebuddy.ai/docs/cli/skills); this is a local-folder installation, not a listing in its marketplace.

Current Codex, Pi, and DeepSeek Harness also document the shared `~/.agents/skills/` location. If you use several of them on one computer, one copy there can serve all three. Avoid installing a duplicate of the same skill in both the shared and agent-specific locations. Respect custom skill-home settings when configured.

The installed layout must be:

```text
<chosen-skills-directory>/thumbwork-phone-use/
├── SKILL.md
└── references/
    ├── setup.md
    └── task-iteration.md
```

Do not copy only `SKILL.md`, or nest the whole repository inside the destination. Keep an existing installation outside the skill search directory as a backup before replacing its folder.

For example, from the extracted repository root, install for Claude Code on macOS/Linux:

```sh
mkdir -p "$HOME/.claude/skills"
test ! -e "$HOME/.claude/skills/thumbwork-phone-use" && cp -R skills/thumbwork-phone-use "$HOME/.claude/skills/"
```

For Codex, replace `.claude/skills` with `.agents/skills`. The guard leaves an existing skill untouched. On Windows PowerShell, from the extracted repository root:

```powershell
$skillRoot = Join-Path $HOME '.claude/skills'
$skillTarget = Join-Path $skillRoot 'thumbwork-phone-use'
if (Test-Path $skillTarget) { throw 'Skill already exists; back it up before replacing it.' }
New-Item -ItemType Directory -Force $skillRoot | Out-Null
Copy-Item -Recurse './skills/thumbwork-phone-use' $skillTarget
```

File-manager copying works equally well and needs no terminal. In macOS Finder use **Go → Go to Folder** to enter a hidden skill directory; in Windows File Explorer enter the home-folder path in the address bar.

Codex users can alternatively ask its built-in `$skill-installer` to install `skills/thumbwork-phone-use` from `M4rque2/Thumbwork`. That repository route requires the skill to be present in the downloaded/published revision; an unpublished local copy must be installed from the local folder.

### Begin setup

Then tell your coding agent:

> Use the thumbwork-phone-use skill to set up this computer for Android phone use. Install what is missing, guide me through model configuration and connecting my phone, then help me write and test a prompt for my first task.

Use a local execution session with access to the connected phone. A remote/cloud sandbox does not gain access to your computer's USB devices by installing the skill. If the agent cannot find the skill, check the exact folder layout and selected project, then restart/reload the agent. If it cannot inspect images, it can still read structured outputs, but phone screenshot validation needs a host image-viewing capability or human inspection.

The agent will:

1. Install the Thumbwork CLI and its Python prerequisite if missing.
2. Ask for your model endpoint and model name, arrange private API-key entry, and verify screenshot understanding.
3. Install ADB tools and guide you through connecting an Android phone over USB and approving debugging.
4. Install and enable ADB Keyboard, then test actual phone text entry.
5. Draft a reusable task prompt, run it through the CLI, inspect the results, and refine it when needed.

You provide credentials, connect/unlock the phone, approve phone dialogs, and describe the result you want. The agent handles commands and prompt files. Setup is complete when the phone trial works; your task is validated separately against its requested result. Successful trials provide evidence of a working prompt, not a guarantee across future app or model changes.

## CLI reference

The commands below are for an installed CLI. The skill walks through installation and setup for you.

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

The skill's [setup guide](skills/thumbwork-phone-use/references/setup.md) covers fresh machines; its [task iteration guide](skills/thumbwork-phone-use/references/task-iteration.md) covers writing, testing, and resuming tasks.

List available tasks, then run one by its unique prompt name:

```bash
thumbwork run
thumbwork run xhs_search
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

### Optional debug files

Debug output is off by default. Use `thumbwork run NAME --debug` only when you want to retain screenshots, annotated screenshots, model request/response traces, and logs in `debug/`. These files can use substantial disk space. `smoke --debug` similarly retains model traces.

Without `--debug`, no debug folder or model traces are created. Screenshots still needed for the agent's current context and checkpoint are kept in `state/`; obsolete screenshots are pruned as context is compacted. The saved prompt, extracted output, result, and checkpoint remain available.

`resume` keeps the original run's debug setting. Starting a new run without `--debug` does not remove debug files from older runs. Avoid deleting screenshots referenced by an incomplete run's checkpoint if you plan to resume it.

### Text entry on the phone

The `type` action replaces text in the focused input field; an empty string clears it. It does not press Enter. The agent must check the next screenshot and submit the search or form with a separate action. If no editable field is focused, the agent receives feedback to tap the input field before retrying.

ADB Keyboard is the preferred input method. Text is sent using its [UTF-8 Base64 input protocol](https://github.com/senzhk/ADBKeyBoard#usage-example), preserving spaces, Chinese characters, and punctuation. Clipboard and plain ADB input are fallbacks; unsupported text produces an error instead of silently dropping characters. The original keyboard is restored after a temporary switch, including when typing fails.

Thumbwork waits for ADB Keyboard to be selected and ready before clearing or sending text. Fallbacks are used only when the device advertises the required commands; unsupported clipboard and select-all commands are skipped or reported before modifying the field. A missing input capability pauses the run with setup instructions, so you can enable ADB Keyboard and use `resume` afterward. Device command timeouts also pause for inspection because text may have been partially entered.

Typing logs identify the method used and report `status=sent verification=pending`. The agent then checks a fresh screenshot. Gray placeholder text in an empty field should not be mistaken for text that failed to clear. If restoring the previous keyboard fails, Thumbwork reports it without automatically entering the text again.
