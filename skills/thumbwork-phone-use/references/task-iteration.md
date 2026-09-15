# From intent to a tested prompt

## Prepare the task

Use the user's request as the starting point. Ask only for missing details that affect execution: target app, desired result, selection/filter rules, output fields or count, and actions that are allowed. Draft the prompt yourself; the user need not learn Thumbwork syntax.

Save a reusable prompt at `~/thumbwork_projects/NAME/NAME.md` (or the selected projects root). The folder and Markdown filename must share a unique name. Put referenced images beside it, for example under `assets/`. Other notes should use a non-Markdown format such as `validation.json`, since Markdown files can be discovered as task prompts. Do not overwrite an existing task without checking it.

A useful prompt describes:

- **Goal and scope:** app, query/entity, account if relevant, and allowed operations.
- **Selection:** filters, ordering, how many items, and what makes an item distinct.
- **Output:** fields to extract from the visible screen; use `null` for unavailable fields rather than inventing values.
- **Completion:** measurable conditions and the final screen/state, if needed.
- **Blockers:** when to ask for human help or report a shortfall; do not loop indefinitely.

Example for a user who requested collecting search results:

```markdown
# Collect search results

Open Google and search for "Li Auto". Collect the first 5 distinct organic
web results in their displayed order. Skip ads and repeated destinations.
Record each result with extract: rank, title, displayed_domain, and snippet.
Read only what is visible; use null if a requested field is unavailable.
Scroll as needed. Do not open links or sign in.
Finish successfully when 5 qualifying records have been saved. If two
successive scrolls reveal no new qualifying results, stop and report the
shortfall. If login, CAPTCHA, or another blocking dialog appears, ask for
human help. Do not claim success with fewer than 5 records.
```

Adapt the example to the real task. Avoid hardcoded coordinates and long speculative click sequences; the phone agent observes screenshots. Typing replaces the focused field and requires a separate submit action. Data collection must use `extract`, since a final summary alone does not save records to `output.jsonl`.

## Run and inspect

Use the selected model, device environment, and a finite budget appropriate to task size:

```sh
thumbwork run NAME --model PROFILE --max-steps 40 --json
```

For a quick one-off test, `thumbwork run --prompt 'TASK_TEXT' --model PROFILE --max-steps 20 --json` is also available. Use a saved file for complex text to avoid shell quoting issues. `--projects-dir PATH` belongs after `run` and is needed when using a nondefault project root.

Read the returned result and artifacts even when the exit code is nonzero:

| Status / exit | Meaning and next step |
| --- | --- |
| `success` / 0 | The phone model declared completion. Verify output and final state against the requested criteria. |
| `needs_input` / 3 | Read `required_action`, guide the user through the blocker, then resume after it is resolved. |
| `incomplete` / 1 | Step budget exhausted. Check progress before extending the cumulative budget. |
| `failure` / 1 | Inspect the reason and evidence; correct the cause before a new run. |
| `error` / 2 | Diagnose environment, endpoint, or runtime failure before changing the prompt. |
| `interrupted` / 130 | Inspect partial effects and outputs before considering a new run. |

`result.json` contains `status`, `reason`, `run_directory`, `output_path`, `step_count`, `extraction_count`, and `required_action`. Each `output.jsonl` line wraps extracted content in `data`; verify the actual records, required fields, relevance, count, and duplicates. `extraction_count` alone is insufficient.

For visible actions, inspect the screenshot named by `checkpoint.json`'s `last_screenshot` when available; resolve it relative to the run directory. Use debug screenshots/traces when intermediate evidence matters. A model's own success statement is not independent verification. Do not silently substitute a different result for the user's requested one.

## Correct the cause and try again

1. Identify the failed acceptance condition and the evidence explaining it.
2. Repair setup, authentication, app state, or model issues first when they caused the failure. Clarify the prompt only when its instructions were missing, ambiguous, or led to the wrong behavior.
3. Preserve previous run snapshots. Edit the source `NAME.md` for the next attempt and record the reason for the revision in a short `validation.json` beside it, with run paths and observed outcomes. Keep secrets out.
4. Run again within the user's authorized scope when the change can plausibly resolve the failure. Stop automatic retries if the same failure recurs after a targeted correction without new evidence, or the task needs a user decision. Report the concrete blocker. Do not keep spending model calls on identical attempts.

Resume only `needs_input` or step-limited `incomplete` runs:

```sh
thumbwork resume '/absolute/run/directory' --json
thumbwork resume '/absolute/run/directory' --max-steps 80 --json
```

The new limit is a cumulative total, not 80 additional steps, and must exceed completed steps without reducing the original budget. Resume uses the saved prompt and settings; editing `NAME.md` does not change an existing run. A changed prompt needs a new `run`. Resume retains debug settings and cannot switch models. Ensure the same intended phone is selected again.

Before restarting tasks that send, create, delete, buy, or otherwise change external state, inspect what already happened. Never replay completed consequential actions simply to validate a prompt. Use a preview/read-only portion where possible, or ask for the specific missing authorization when a repeated action would exceed the original request. Read-only collection trials can usually be repeated automatically.

## Deliver a usable result

Mark the prompt tested only when the acceptance criteria match the saved output or observed final state. For safely repeatable tasks, a second successful run from a known starting state can strengthen evidence when reliability matters; never imply one or several trials guarantee future success.

Give the user the saved prompt, result/output paths, exact command and required PATH/device environment for rerunning, what passed, and any limitations. If the task remains blocked, deliver the current prompt and failed-run evidence with the one next action needed. Do not report the task complete merely because installation or a model check passed.
