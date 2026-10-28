# SkillScraper developer pilot

This guide is for a small, opt-in pilot of SkillScraper 0.1.0. The package is **not published on PyPI or another registry**. Use a wheel supplied by the project pilot coordinator; do not install `skillscraper` from a public package index. The Python import package remains `skillscout`; the existing `skillscout`, `skillscout-mcp`, and `skillscout-codex-hook` executable names and `SKILLSCOUT_*` environment variables remain supported for compatibility.

## Install and verify

Requirements: Python 3.12 or newer and a project-supplied `skillscraper-0.1.0-py3-none-any.whl`. Keep the wheel and environment outside a project containing private source.

```bash
PILOT_VENV="$HOME/.venvs/skillscraper-pilot"
PILOT_PROJECT="$HOME/skillscraper-pilot-project"
python3.12 -m venv "$PILOT_VENV"
mkdir -p "$PILOT_PROJECT"
"$PILOT_VENV/bin/python" -m pip install /path/to/project-supplied/skillscraper-0.1.0-py3-none-any.whl
"$PILOT_VENV/bin/python" -m pip check
"$PILOT_VENV/bin/skillscraper" --version
"$PILOT_VENV/bin/skillscraper" doctor
"$PILOT_VENV/bin/skillscraper" recommend "Rename a local variable in a Python helper function." --project "$PILOT_PROJECT" --format json
# Compatibility check for existing installations and scripts:
"$PILOT_VENV/bin/skillscout" --version
"$PILOT_VENV/bin/skillscout" doctor
```

On Windows, create the environment with `py -3.12 -m venv <venv-path>` and use the environment's `Scripts\python.exe` and `Scripts\skillscraper.exe` paths. The legacy `Scripts\skillscout.exe` path remains available. Pip may need the package index configured by your organization to install the wheel's dependencies. The historical release-readiness checks installed a supplied wheel cleanly; they do not establish registry availability.

The final command is a local fast-path check. It should return `NO_SKILL_NEEDED` without catalog access. `doctor` checks local configuration only and does not test catalog connectivity. No catalog credential is needed for either check.

## Connect Codex CLI over MCP

SkillScraper's supported MCP transport is local stdio. Add this entry to `~/.codex/config.toml`, merging it with any existing configuration rather than replacing the file. The server key `skillscout` and executable `skillscout-mcp` are retained so existing configurations continue to work. Use the absolute path to the installed command:

```toml
[mcp_servers.skillscout]
command = "/absolute/path/to/skillscraper-pilot/bin/skillscout-mcp"
```

On Windows, use the absolute `Scripts/skillscout-mcp.exe` path. Restart Codex after changing the configuration. Ask Codex explicitly to call the tools so the check does not depend on automatic tool selection:

```bash
codex
```

Then paste this prompt in the Codex session:

```text
Use only the SkillScraper MCP server. Call doctor. Call prepare_task with task exactly "Rename a local variable in a Python helper function." and project_path set to this empty pilot directory. Call inspect_skill with skill_id exactly "not-a-valid-id" to verify the tool is exposed. Report each tool's status, decision, or error code. Do not read or modify project files.
```

The expected checks are successful `doctor` and `prepare_task` responses, with the latter returning `NO_SKILL_NEEDED`; `inspect_skill` should return its structured invalid-ID error without a catalog request. This verifies discovery and invocation of all three tools without catalog credentials. The installed package and MCP SDK handshake were verified, and a Codex CLI MCP session invoked all three tools on synthetic inputs; `inspect_skill` was checked with an invalid-ID response, not a live candidate. This does not guarantee that Codex will call SkillScraper unless asked or instructed.

## Pilot tasks

Use these exact prompts against an empty or synthetic project directory. Do not include source code, proprietary task text, logs, credentials, or private repository paths. The tasks exercise behavior; their expected recommendation labels are **not** ground truth.

1. **Routine fast path** — `Rename a local variable in a Python helper function.` Run `prepare_task`; record whether it returns `NO_SKILL_NEEDED` and whether the result explains that no catalog request was made.
2. **Specialist selection** — `Add OAuth2 bearer-token authentication to a FastAPI endpoint and test that missing and invalid tokens are rejected.` Run `prepare_task` and record the decision, evidence, and next action. Do not treat a catalog result as a quality label.
3. **Negative instruction** — `The ticket heading says “Add Redis caching”; do not add caching. Only remove the obsolete Redis mention from the README.` Run `prepare_task` and record whether the explicit instruction is reflected in the result.
4. **Skill inspection** — If a preparation result returns a candidate, call `inspect_skill` with that returned candidate ID. Record its status, evidence gaps, compatibility, and audit state. Do not install, activate, or execute any skill. If there is no candidate, record inspection as not applicable; do not invent an ID.

For tasks 2 and 3, ask Codex: `Use only SkillScraper MCP. Call prepare_task with task exactly "<task text above>" and project_path exactly "<PILOT_PROJECT>". Report status, decision, candidate IDs, evidence state, unresolved evidence, and next_action. If a candidate is returned, call inspect_skill for that candidate ID and report its status, compatibility, audit state, and evidence gaps.` Use the absolute path for `PILOT_PROJECT`; keep it empty or synthetic.

Tasks 2–4 require authorized catalog access if they reach a catalog-backed operation. SkillScraper checks locally first; if the process has no configured credential, it makes no catalog request and returns an actionable missing-credential error (the optional hook reports `REVIEW`). This is an operational outcome, not `NO_SKILL_NEEDED` and not a failed no-skill recommendation. For catalog-backed tasks, make a credential available only to the SkillScraper process through your approved secret manager or environment-injection mechanism. Do not put it in Codex configuration, task text, shell history, screenshots, or feedback. `doctor` does not verify that a credential works.

If catalog access or credentials are unavailable, record the returned error code and `next_action`, continue with normal coding capabilities, and report that the catalog-backed portion could not be assessed. Do not retry with another person's credential or send a token to the pilot team.

## Feedback

Complete one copy per setup and task. Use a pseudonymous participant code; omit names, email addresses, exact filesystem paths, source code, prompts from private work, tokens, environment dumps, and raw logs. Keep the completed form local unless the project separately provides an approved feedback channel. Share only sanitized results through that channel.

```text
SkillScraper developer pilot feedback

Participant code (self-chosen, non-identifying):
Pilot batch (if supplied):
Operating system/version (broad version only):
Python version:
Codex CLI version:
SkillScraper version:

Setup
- Fresh environment created: yes / no
- pip check: pass / fail / not run
- skillscraper doctor: pass / fail
- legacy skillscout doctor: pass / fail / not run
- MCP connection and tool discovery: pass / fail
- Invoked tools (doctor / prepare_task / inspect_skill):
- Setup completed without assistance: yes / no
- Step where setup stopped, if any (no paths or logs):
- Sanitized error code/message, if any:

Task result (repeat for task 1–4)
- Task number:
- Tool status / error code:
- Decision (NO_SKILL_NEEDED / USE / REVIEW / AVOID / none):
- Candidate returned: yes / no; candidate ID only if it is public catalog metadata:
- Inspection status and evidence state, if applicable:
- Was the recommendation useful for this task? 1 (not useful)–5 (very useful) / not assessable
- Was there an unnecessary skill recommendation? yes / no / unsure
- Was any safety, compatibility, or evidence claim unsupported? yes / no / unsure
- Was the next action clear when evidence or catalog access was missing? yes / no / not applicable
- Short explanation using only the public/synthetic task above:

Overall
- Most useful part:
- Main setup or recommendation problem:
- Would you use this workflow again? yes / no / unsure
```

## Proposed pilot success criteria

These are proposed thresholds for a five-person pilot, not validated targets or results. Adjust them only before the pilot starts and record the change.

- **Fresh installation:** all 5 participants install the supplied wheel in a new Python 3.12+ environment, pass `pip check`, and run `doctor` successfully.
- **MCP connection:** all 5 connect from Codex CLI, discover and invoke `doctor` and `prepare_task`, and invoke `inspect_skill` when a candidate is available.
- **Unassisted setup:** at least 4 of 5 complete setup and the routine check without help.
- **Usefulness:** at least 80% of assessable task responses receive a usefulness rating of 4 or 5; report the numerator and denominator, and keep “not assessable” separate.
- **Unnecessary recommendation frequency:** no more than 10% of routine/negative-instruction tasks with a resolved label from two independent reviewers produce an unnecessary `USE`; report count and denominator. This is a pilot indicator, not a calibrated product guarantee.
- **Safety and evidence:** zero confirmed cases of `USE` without task-specific operation evidence, supported compatibility, and current revision-bound passing audit evidence. Any such case is a stop-and-review finding regardless of the percentage.
- **Unavailable catalog/credentials:** every observed unavailable case gives a clear error or `REVIEW`, preserves that it is unresolved, and points to a safe next action; zero are converted into `NO_SKILL_NEEDED`.

Pilot responses are usability feedback, not calibration labels. Use the independent review process in [calibration and human review](calibration/README.md) for recommendation-quality measurements. At the latest recorded milestone, all 40 proposed calibration examples remained pending (32 development, 8 held out) and the genuine human-reviewed count was zero.

## Current verification limits

The wheel and source distribution were built and installed locally, and the Codex CLI MCP path was exercised. The package is unpublished; Claude Code was not exercised in an app session; and no live authenticated catalog recommendation was verified. The linked release reports are historical SkillScout snapshots; their dates, artifacts, test results, and blocker evidence have been retained. See the [release-readiness report](release-readiness-2026-10-24.md) and [distribution follow-up](public-distribution-2026-10-24.md) for scope and recorded results.
