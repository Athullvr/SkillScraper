# SkillScout release-candidate verification — 2026-10-25

This report records the current checkout verification for a controlled developer pilot. It does not authorize publication or public reliability claims.

## Verified snapshot and completed milestone

- Checkout: `main` at `aca71236af6088d24a3d0b053dbb67480047b917`; the worktree was clean before this report was added.
- Latest commit: `docs: add developer pilot documentation and update README with pilot link`. The preceding implementation milestone added the optional Codex prompt-hook entry point. The latest committed milestone completed the pilot and calibration documentation; it did not verify authenticated catalog access, Codex hook trust/invocation, Claude Code app integration, licensing, or human recommendation reviews.
- Full test suite: `./.venv/bin/python -m pytest -q` — **288 passed**.
- A wheel built from the current source in `/tmp` was installed into a fresh Python 3.12.3 environment using the locked dependencies and passed `pip check`. The wheel's 38 Python/typed source files matched the checkout; it declares `skillscout`, `skillscout-mcp`, and `skillscout-codex-hook` console scripts. The standard offline `uv build` attempt could not find Hatchling in its cache; the build was completed with an already-installed Hatchling environment instead. No artifact was published.
- Fresh-install checks: `skillscout --version` returned `SkillScout 0.1.0`; `skillscout doctor` succeeded and reported no catalog credential. Doctor is local-only and makes no network request.
- Installed-package MCP smoke: **passed** with the real SDK client, local fixture catalog, malformed JSON-RPC recovery, three tool invocations, and graceful EOF. The normal run exposed `prepare_task`, `inspect_skill`, and `doctor`; fixture results covered `USE`, `REVIEW`, missing credentials, and sanitized malformed upstream content. All final smoke subprocesses wrote zero stderr bytes. An initial in-sandbox run timed out during MCP initialization; the same local fixture-only command passed outside the nested-process sandbox.
- Installed hook entry point, tested directly with a synthetic routine event, returned valid `UserPromptSubmit` JSON with `NO_SKILL_NEEDED` and zero stderr. This was an entry-point check, not a Codex-host hook invocation.

## Release-blocker checklist

| Area | Status | Evidence |
| --- | --- | --- |
| Source tests and local wheel install | **Pass** | 288 tests passed; fresh offline install and `pip check` passed. |
| MCP stdio distribution path | **Pass for local fixture checks** | Current wheel smoke passed with tool discovery, normal routine call, structured invalid input, fixture failures, and raw-protocol recovery. |
| Authenticated live catalog search and inspection | **Blocked** | `doctor` reports `Catalog credential not configured`; no authorized credential is available, so no authenticated live result exists. |
| Codex hook registration, trust, and host invocation | **Blocked** | Codex CLI `0.162.1` reports hooks as stable, but no SkillScout hook registration was found in the active user/project config layers and no hook was trusted through `/hooks`. Direct entry-point and unit tests do not establish host routing. |
| Claude Code client integration | **Unverified** | Existing release documentation records no Claude Code app session. Do not claim verified Claude Code compatibility until a host session is run. |
| Recommendation-quality evidence | **Blocked for quality claims** | There are no genuine human-reviewed labels. The offline workflow is ready, but no accuracy or safety rate is measured. |
| Project license and package metadata | **Release blocker** | No `LICENSE`, `NOTICE`, `COPYING`, or equivalent file was found. `pyproject.toml` and the built wheel metadata have no project license field. Owner decision is required before granting reuse or public distribution rights. |
| Public package release | **Not published** | No PyPI or other registry publication was performed or verified. The pilot guide correctly requires a project-supplied artifact. |

### Authenticated catalog status and verification deviation

The exact blocker is the absence of an authorized `SKILLSCOUT_CATALOG_TOKEN` or `VERCEL_OIDC_TOKEN` in the SkillScout process environment. No authenticated search, detail inspection, or audit result was observed. Mocked tests in the full suite cover successful fixtures, malformed/incomplete responses, authentication errors, timeouts, rate limits, and transport failures; those are not live catalog results.

During a direct hook smoke, I mistakenly submitted one synthetic specialist task after `doctor` had shown no credential. The hook attempted an unauthenticated catalog operation and returned a sanitized `catalog unavailable` context. This crossed the live-test gate. The prompt contained only a synthetic task and no credential or user data. I cannot verify whether the request reached the service, and made no further catalog-backed calls. This outcome is not evidence about authenticated catalog behavior. Before external use, the owner should decide whether catalog-backed calls without a configured credential are permitted; if not, add a local preflight that prevents them.

To run the live check later, have the approved secret manager inject an authorized credential into the SkillScout process only; do not put the value in a shell command, client config, prompt, or report. Use an empty or synthetic project directory and this known test query:

```sh
PILOT_VENV="/path/to/fresh-venv"
PILOT_PROJECT="/tmp/skillscout-catalog-check"
mkdir -p "$PILOT_PROJECT"

"$PILOT_VENV/bin/skillscout" recommend \
  "Implement OAuth2 bearer-token authentication for a FastAPI endpoint." \
  --project "$PILOT_PROJECT" --format json
```

If the result returns a candidate, inspect only its returned stable ID:

```sh
"$PILOT_VENV/bin/skillscout" inspect "<candidate-id-from-result>" \
  --project "$PILOT_PROJECT" --format json
```

Record response status, completeness, detail/audit availability, and sanitized error codes. Do not copy candidate excerpts, secrets, or private task text into diagnostics. A failed connection or missing/rejected credential is an operational failure, not `NO_SKILL_NEEDED`.

### Codex hook host check

Codex's current hooks documentation lists user and project `hooks.json` / `config.toml` locations, requires review and trust of non-managed hook definitions, and documents `UserPromptSubmit` JSON `hookSpecificOutput.additionalContext`. See the [Codex Hooks guide](https://developers.openai.com/codex/hooks). The repository's README uses the documented event and response shape. No global Codex configuration was modified.

After a developer chooses to test the opt-in hook, use a disposable, empty project and a project-local `.codex/hooks.json`; merge the entry if that file already exists:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/venv/bin/skillscout-codex-hook",
            "timeout": 60,
            "statusMessage": "Checking task with SkillScout"
          }
        ]
      }
    ]
  }
}
```

Start Codex in that project, run `/hooks`, inspect the displayed hook source and command, then trust that exact entry. Test the routine prompt `Rename a local variable in a Python helper function.` and verify the hook event runs and returns `NO_SKILL_NEEDED`. To test the unavailable-catalog path without contacting the catalog, launch Codex with `SKILLSCOUT_CATALOG_URL=https://127.0.0.1:1` and both credential variables removed, then submit only the synthetic OAuth task above; expect `REVIEW` and an operational next step. Keep this local failure simulation distinct from the authenticated live check. Do not enable the all-prompts hook for private task text.

For the local failure simulation, launch Codex from the disposable project with:

```sh
env -u SKILLSCOUT_CATALOG_TOKEN -u VERCEL_OIDC_TOKEN \
  SKILLSCOUT_CATALOG_URL=https://127.0.0.1:1 codex
```

## Evaluation and pilot workflow

The v2 proposal validated with 32 development and 8 held-out examples, zero review events, and zero conflicts. All labels remain pending. I viewed only a development review packet (`dev-003`); no review submission, adjudication, dataset edit, or held-out packet inspection occurred.

The default run produced schema `skillscout-calibration-run-v3`, 32 development rows, and excluded all 8 held-out examples from predictions and metrics. Human-review disposition: **0 reviewed, 0 judgeable, 0 ambiguous, 0 unjudgeable, 0 unresolved, 32 pending**. Synthetic smoke results were reported in a separate group, with zero judgeable outcomes and explicit `n/a` denominators. No quality measurement or tuning conclusion can be drawn from this run.

Use the commands below to start a review copy and run development-only reports. Keep the proposed source dataset unchanged and keep reviewers' non-identifying IDs in the append-only ledger, not in the dataset:

```sh
REVIEW_DIR="$(mktemp -d /tmp/skillscout-review.XXXXXX)"
DATASET="$REVIEW_DIR/dataset.json"
REVIEWS="$REVIEW_DIR/reviews.jsonl"
cp docs/calibration/v2/proposed-dataset.json "$DATASET"
python -m skillscout.evaluation calibrate validate "$DATASET"
python -m skillscout.evaluation calibrate review status "$DATASET" --reviews "$REVIEWS"
python -m skillscout.evaluation calibrate review show "$DATASET" dev-003 --reviews "$REVIEWS"
```

Two reviewers must submit independent evidence-backed labels using distinct reviewer IDs. Preserve revisions. A third, distinct adjudicator is required only for disagreement and must cite both current review event IDs. Then validate and run the development report:

```sh
# Run independently once per reviewer; replace placeholders with that reviewer's
# evidence-backed judgment. Do not copy a proposed decision as a label.
python -m skillscout.evaluation calibrate review submit "$DATASET" EXAMPLE_ID \
  --reviews "$REVIEWS" --reviewer REVIEWER_ID --status judgeable \
  --decision '<NO_SKILL_NEEDED|USE|REVIEW|AVOID>' \
  --rationale 'Reviewer rationale' --evidence-basis 'Specific evidence citation'

python -m skillscout.evaluation calibrate validate "$DATASET" --reviews "$REVIEWS"
python -m skillscout.evaluation calibrate run "$DATASET" \
  --reviews "$REVIEWS" --output "$REVIEW_DIR/dev-run.json" \
  --report "$REVIEW_DIR/dev-run.md"
```

Full `review submit` and `review adjudicate` argument examples and metric definitions are in [calibration and human review](calibration/README.md). Do not pass `--include-held-out` during development. Only after tuning and the analysis plan are frozen may a separate evaluator request a final held-out run. Decision correctness, false-positive `USE`, per-decision false negatives, safety regressions, and evidence sufficiency must retain their explicit denominators and remain separated from synthetic smoke results.

The pilot guide's thresholds are all labeled **proposed**, and there are no external pilot results. It documents wheel installation, Codex MCP setup, connectivity/tool checks, representative synthetic tasks, sanitized feedback, and setup/recommendation success criteria. The package is explicitly described as unpublished. See [developer pilot guide](developer-pilot.md).

## Licensing investigation

The project currently declares runtime dependencies `httpx`, `mcp`, `pydantic`, `rich`, and `typer`; the wheel declares dependencies and packages only `src/skillscout`, without vendoring third-party libraries or other bundled assets. The local installed metadata identifies the direct dependencies as BSD-3-Clause (`httpx`) or MIT (`mcp`, `pydantic`, `rich`, `typer`). The 46-package lock does not carry a license inventory.

Transitive review points:

- `certifi` is MPL-2.0. If redistributing a complete environment or a modified copy, check its notice and file-level source obligations.
- `cryptography` declares Apache-2.0 OR BSD-3-Clause; `packaging` declares Apache-2.0 OR BSD-2-Clause; `typing-extensions` declares PSF-2.0. Preserve the chosen upstream license and notice material when those packages are redistributed.
- `markdown-it-py` and `mdurl` lack license identifiers in their installed metadata but ship license files. Platform/conditional lock entries such as `pywin32`, `colorama`, and `httpx2-jsfetch` were not installed in this Linux environment and need review for the platforms or extras that use them.
- No project `LICENSE` or `NOTICE` is present, and package `METADATA` has no project license field. Complete an owner-approved dependency/license and notice inventory before public distribution, especially if distributing a bundled environment.

License options have different practical outcomes; this report does not choose one:

- **MIT:** a short permissive grant suitable for reuse and redistribution, with copyright/permission notice retention ([OSI text](https://opensource.org/license/mit)).
- **Apache-2.0:** permissive reuse with an express contributor patent grant and additional license, modification-notice, and attribution requirements ([license text](https://www.apache.org/licenses/LICENSE-2.0), [ASF application guidance](https://www.apache.org/foundation/license-faq.html)).
- **GPL-3.0-or-later:** copyleft terms for conveyed modified/combined software, including corresponding-source obligations when distributing covered binaries ([GNU guide](https://www.gnu.org/licenses/quick-guide-gplv3.html)).
- **Proprietary or no public grant:** can preserve restricted pilot distribution, but downstream users and contributors receive no broad reuse grant. With no license, do not assume others may copy, modify, or redistribute the project ([no-license guidance](https://choosealicense.com/no-permission/)).

For a restricted pilot, the owner still needs to grant pilot users explicit permission; simply omitting a license does not grant pilot rights. These are practical summaries, not a legal determination. The owner must decide: the project license and SPDX metadata; the copyright holder(s); whether pilot users receive a private or public grant; how contributions are accepted and licensed (including whether a DCO/CLA is needed); and whether a full dependency notice bundle is required for the chosen distribution format. No license file or metadata was created.

For contributions, verify that each contributor has the right to submit the work and that the project can redistribute it under the chosen terms. Contributors generally keep copyright unless they assign it; a CLA can grant project rights without transferring ownership, while a copyright assignment transfers ownership. Re-licensing or dual-licensing later may require broader contributor permissions ([OSI contributor-agreement FAQ](https://opensource.org/faq)). Apache-2.0 also includes a contributor patent grant; the owner should decide whether that protection fits the project.

## Prioritized blockers for public release

1. **P0 — Resolve project rights and license metadata.** Owner approval is required; the repository and wheel currently provide no project license grant.
2. **P1 — Decide and verify unauthenticated catalog policy.** No authorized credential was available, authenticated behavior remains unknown, and the verification deviation showed an unconfigured hook can attempt an outbound query. Test the approved policy before inviting external developers to catalog-backed tasks.
3. **P1 — Complete a trusted Codex hook host session before advertising automatic routing.** Registration and `/hooks` trust require the developer's explicit action; direct/unit tests are insufficient evidence.
4. **P1 — Keep quality claims blocked until independent development reviews and final held-out evaluation exist.** No human labels or accuracy/safety results exist. Keep the eight held-out examples sealed until freeze.
5. **P2 — Verify Claude Code in an actual supported client session before claiming that integration.** Current pilot instructions are Codex-focused; Claude Code remains unverified.

The controlled pilot can proceed only as an opt-in, project-supplied-artifact exercise under the owner's chosen distribution terms. Keep the success thresholds labeled proposed, collect only the sanitized feedback fields in the pilot guide, and make no public reliability claim.
