# First-run setup

This reference is self-contained when only the skill folder is installed. Source: [M4rque2/Thumbwork](https://github.com/M4rque2/Thumbwork). Use a local checkout supplied for this task when present; otherwise install from that repository. Do not assume the PyPI name is a published distribution.

## 1. Install the CLI

Detect the OS, shell, Python version, and existing CLI. Thumbwork requires Python 3.10 or newer. If Python is missing or too old, install a supported version using the host's existing package manager or the [official Python installer](https://www.python.org/downloads/). On Linux, install the distribution's matching venv package if environment creation reports it missing. Verify the selected interpreter afterward. Do not replace the system Python.

Prefer an existing pipx or uv tool installation if available. Otherwise use a dedicated [virtual environment](https://docs.python.org/3/library/venv.html). Example for macOS/Linux, after resolving `python3` to a supported interpreter:

```sh
python3 -m venv "$HOME/.thumbwork/venv"
"$HOME/.thumbwork/venv/bin/python" -m pip install 'https://github.com/M4rque2/Thumbwork/archive/refs/heads/main.zip'
export PATH="$HOME/.thumbwork/venv/bin:$PATH"
thumbwork --version
thumbwork --help
```

Windows PowerShell equivalent, after verifying the Python launcher selects 3.10+:

```powershell
py -3 -m venv "$HOME\.thumbwork\venv"
& "$HOME\.thumbwork\venv\Scripts\python.exe" -m pip install 'https://github.com/M4rque2/Thumbwork/archive/refs/heads/main.zip'
$env:Path = "$HOME\.thumbwork\venv\Scripts;$env:Path"
thumbwork --version
thumbwork --help
```

For a local checkout, replace the archive URL with its absolute directory. The archive route needs no Git. Use an explicit release/commit instead when supplied by the user. Do not recreate an existing environment blindly. Record the installed version and source.

Ensure later agent commands inherit the tool's directory in PATH; shell changes may not persist between tool calls. Use the absolute executable when necessary. Make a minimal, nonduplicating user PATH addition for future terminals using the host's normal mechanism, and verify in a fresh terminal. Until the agent host sees the change, explicitly pass the same environment on every call.

## 2. Configure a vision model

Check `thumbwork models list --json`. If a suitable profile exists, reuse it and verify its connection when needed. Otherwise ask for the OpenAI-compatible endpoint URL and exact server model ID, and explain that the model must understand screenshots and that phone screenshots are sent to this endpoint.

Prefer opening a terminal the user can interact with and starting:

```sh
thumbwork models add
```

It requests endpoint, hidden API key, and model ID in that order. Have the user type the key directly there. A PTY controlled only by the agent is not a private user input surface; if no interactive terminal is available, have the user populate a secret environment variable in the execution environment. Do not ask them to paste the key into the conversation. The noninteractive form is:

```sh
thumbwork models add phone --base-url 'ENDPOINT_URL' --model-id 'SERVER_MODEL_ID' --api-key-env THUMBWORK_SETUP_API_KEY --context-window CONTEXT_TOKENS --thinking default --json
```

Replace nonsecret placeholders using the user's endpoint configuration; `CONTEXT_TOKENS` is a positive integer, not a guessed capability. `--context-window` can be omitted if the server reports it. `default` sends no thinking override; use `on`, `off`, or `unsupported` when known. Never expand the secret variable into the command text. If the key is unavailable in the agent's process, arrange private entry in the user's terminal instead of printing or relaying it.

Setup normalizes full `/chat/completions` URLs, checks metadata, and tests two images before saving. Unknown context limits must come from the server configuration; accepting an image request is insufficient if the image checks fail. A failed replacement keeps the old profile intact. Reuse it or resolve the failure without overwriting it blindly.

```sh
thumbwork models show PROFILE --json
thumbwork models default PROFILE
```

Replace `PROFILE` with the returned saved name. Use an explicit `--model PROFILE` if changing the default would disrupt another task. Configuration, including the key, is stored locally under the path returned by `thumbwork models path`; it is not an OS keychain. Never copy this configuration into task artifacts.

`thumbwork smoke --model PROFILE --task all --mode quick --json` is an optional screenshot-only diagnostic. It does not operate a phone or certify end-to-end readiness.

## 3. Install ADB

Reuse a working `adb` on PATH. Otherwise download the host's standalone [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) from Google and extract to a user tools directory, or use the host's existing package manager. Android Studio is unnecessary for this workflow. Keep the whole extracted platform-tools directory and add it to the PATH used by Thumbwork, including future calls and terminals. Verify `adb version` in that environment.

## 4. Connect the phone

Explain the next physical action: connect an Android phone with a data-capable USB cable, unlock it, enable Developer options and USB debugging, and accept this computer's RSA debugging prompt on the phone. Guide the user with device-specific settings if they need help. Use [Android's device setup guidance](https://developer.android.com/studio/run/device) for details; Windows may need an OEM USB driver and Linux may need USB access rules. This workflow does not support iPhones.

Run `adb devices -l` and inspect the result:

| State | Next action |
| --- | --- |
| No device | Check cable, port, debugging, then host driver/access rules. |
| `unauthorized` | Ask the user to unlock and accept the debugging prompt, then recheck. |
| `offline` | Reconnect and recheck before any task. |
| `device` | Verify it is the intended phone and proceed. |
| Multiple devices/emulators | Ask which is intended unless already specified; target its serial. |

The CLI has no `--device` flag. Set `ANDROID_SERIAL` to the chosen serial in the environment of **every** ADB, Thumbwork run, and resume command. On macOS/Linux use `export ANDROID_SERIAL='SERIAL'`; on PowerShell use `$env:ANDROID_SERIAL = 'SERIAL'`. With a single phone, check for a stale value before proceeding. Record the selection without claiming that checkpoints preserve it.

Verify `adb get-state` returns `device` and `adb shell wm size` returns dimensions. These checks do not prove screenshots are readable; verify that in the CLI trial below. A black or protected screen requires a usable app/screen, not repeated blind actions.

## 5. Prepare text entry

Check `adb shell ime list -a` for `com.android.adbkeyboard/.AdbIME`. If absent, download a compatible APK from the upstream [ADBKeyBoard releases](https://github.com/senzhk/ADBKeyBoard#download-apk-from-release-page), then run these commands with the selected serial environment:

```sh
adb install '/absolute/path/to/downloaded-keyboard.apk'
adb shell ime enable com.android.adbkeyboard/.AdbIME
adb shell ime list -s
```

The final enabled-method list must include the component. Skip installation when already present; enable it if necessary. If Android requests installation or keyboard approval, guide the user through that specific prompt. Inspect installation errors before changing anything; do not uninstall an existing keyboard or bypass device security restrictions to force a trial through.

Keep the user's normal keyboard selected. Thumbwork temporarily selects ADB Keyboard for typing and restores the previous input method afterward. Do not permanently set ADB Keyboard as the default as part of setup.

Use the user's intended app if it has a harmless search field, otherwise use an available local search field. Write a short CLI trial that opens the app, focuses the field, types a distinctive string such as `Thumbwork test 你好 123`, verifies the exact visible text, clears the test text, and stops. Specify no submission. Adapt the text if the field filters characters. For this trial, a budget such as `--max-steps 20` is sufficient to start; inspect evidence before extending it.

Inspect the final result and screenshot evidence in the checkpoint or debug artifacts. The trial must demonstrate screenshot reading, navigation, and actual text entry, including the user's required language. `status=sent` only confirms command delivery. If setup pauses, fix the reported issue and follow the resume rules in [task iteration](task-iteration.md). When screenshots were pruned and the evidence is insufficient, use a safe new diagnostic run with `--debug` rather than assert that typing worked.
