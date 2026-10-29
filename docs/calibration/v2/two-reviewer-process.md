# Two-reviewer development calibration process

## Prepared artifacts and scope

Use the frozen v2 proposal at `docs/calibration/v2/proposed-dataset.json` (SHA-256 `bf306a0d41978821836d923ce5023553eba66ca819588385300dc2e0a185021e`). Do not edit it after review begins; every event is bound to this exact hash. The current proposal has 32 development and 8 held-out cases. No human review events exist yet.

The reviewer-facing packets are:

- Reviewer A: [`reviewer-a/packet.md`](step3-review-packets/reviewer-a/packet.md)
- Reviewer B: [`reviewer-b/packet.md`](step3-review-packets/reviewer-b/packet.md)
- Reviewer A's withheld proposal appendix: [`reviewer-a proposals`](step3-review-packets/coordinator-only/reviewer-a-proposals-after-submission.md)
- Reviewer B's withheld proposal appendix: [`reviewer-b proposals`](step3-review-packets/coordinator-only/reviewer-b-proposals-after-submission.md)
- The [32-case completeness checklist](step3-review-packets/completeness-checklist.md) tracks submissions and resolution without recording labels.
- The [held-out exclusion validation report](step3-review-packets/held-out-exclusion-validation.md) records the split and packet checks.

Each has the same development evidence and blank fields, with no labels from the other reviewer and no prior assessment hypotheses. Give each reviewer only their own packet. The separate files under `coordinator-only/` contain SkillScraper’s system decisions, not human labels. The coordinator must withhold both appendices until both reviewers have submitted all 32 cases. The prior AI workpaper is stored in `coordinator-only/` with owner-only file permissions and is not copied into either packet or appendix; do not consult or distribute it before both reviews are submitted.

The packet SHA-256 values are recorded in `held-out-exclusion-validation.md`. Confirm the dataset hash printed in each packet before starting. The coordinator-only directory is mode `0700`, and proposal appendix files are mode `0600`; if both reviewers use the same OS account, these permissions do not isolate them, so do not distribute the appendices early.

## Independent submission

Run from the SkillScraper source checkout with its project environment installed. Each reviewer uses a separate private ledger path. The coordinator must not merge or show either ledger until both reviewers have submitted all development cases. Do not use `--include-held-out`.

Choose non-identifying reviewer tokens distinct from each other and any adjudicator, for example `reviewer-a` and `reviewer-b`. Each reviewer submits one command per case ID from `dev-001` through `dev-032`.

For a judgeable case, replace the placeholders with the reviewer's own label and evidence-backed text:

```sh
python -m skillscout.evaluation calibrate review submit "$DATASET" dev-001 \
  --reviews "$A_LEDGER" --reviewer reviewer-a --status judgeable \
  --decision DECISION \
  --rationale "REVIEWER'S CONCISE RATIONALE" \
  --evidence-basis "SPECIFIC TASK, PROJECT, SEARCH, SKILL.md, OR AUDIT FACT" \
  --evidence-basis "ANOTHER SPECIFIC FACT, IF NEEDED"
```

Allowed decisions are `NO_SKILL_NEEDED`, `USE`, `REVIEW`, and `AVOID`. Repeat `--evidence-basis` for each cited fact. Add `--candidate-order CANDIDATE_ID ...` only when a supported order can be established; ties do not require a forced order.

For an example where the evidence is present but the rubric cannot resolve a single decision, submit `ambiguous` without a decision or candidate order:

```sh
python -m skillscout.evaluation calibrate review submit "$DATASET" dev-001 \
  --reviews "$A_LEDGER" --reviewer reviewer-a --status ambiguous \
  --reason "REVIEWER'S REASON THE PRESENT EVIDENCE DOES NOT RESOLVE THE RUBRIC"
```

For essential missing evidence or an incomplete search, submit `unjudgeable` without a decision or candidate order:

```sh
python -m skillscout.evaluation calibrate review submit "$DATASET" dev-001 \
  --reviews "$A_LEDGER" --reviewer reviewer-a --status unjudgeable \
  --reason "REVIEWER'S REASON ESSENTIAL EVIDENCE IS MISSING"
```

Reviewer B uses the same commands with `--reviews "$B_LEDGER" --reviewer reviewer-b`. Use the actual example ID for each packet section. Do not use the sample `dev-001` command as a label for that case; it only illustrates syntax.

Each reviewer creates a private directory in their own account or on their own machine. The following is run separately by each reviewer; Reviewer A sets `A_LEDGER`, and Reviewer B sets `B_LEDGER` to their resulting path:

```sh
REVIEW_ROOT="$(mktemp -d /tmp/skillscraper-two-review.XXXXXX)"
chmod 700 "$REVIEW_ROOT"
DATASET="$PWD/docs/calibration/v2/proposed-dataset.json"
A_LEDGER="$REVIEW_ROOT/reviews.jsonl"  # Reviewer A only
# Reviewer B uses B_LEDGER="$REVIEW_ROOT/reviews.jsonl" in their own private directory.
```

The CLI creates each ledger on its first submission with owner-only permissions. Keep these paths private until both independent reviews are complete, then transfer the two completed files to the coordinator for merging. If the reviewers use the same operating-system account, filesystem permissions do not isolate them from one another; use separate OS accounts/devices or rely on a coordinator to enforce the separation operationally.

## Completeness checks and merge

Each reviewer validates their own ledger before handing it to the coordinator:

```sh
python -m skillscout.evaluation calibrate validate "$DATASET" --reviews "$A_LEDGER"
python -m skillscout.evaluation calibrate review status "$DATASET" --reviews "$A_LEDGER"
```

For each of the 32 development IDs, Reviewer A's state should show one independent review awaiting the second review. Reviewer B performs the same checks against `$B_LEDGER`. The eight held-out examples should remain unreviewed. `review status` reports identifiers and states, not task evidence; do not request held-out packets.

After both reviewers finish, the coordinator creates a new master ledger without replacing an existing path. First validate both source ledgers individually. Then set `MASTER_LEDGER` to a new path and merge the immutable event lines:

```sh
python -m skillscout.evaluation calibrate validate "$DATASET" --reviews "$A_LEDGER"
python -m skillscout.evaluation calibrate validate "$DATASET" --reviews "$B_LEDGER"
MASTER_ROOT="$(mktemp -d /tmp/skillscraper-calibration-master.XXXXXX)"
chmod 700 "$MASTER_ROOT"
MASTER_LEDGER="$MASTER_ROOT/reviews.jsonl"
if [ -e "$MASTER_LEDGER" ] || [ -L "$MASTER_LEDGER" ]; then
  echo "Refusing to replace an existing master ledger" >&2
  exit 1
fi
( umask 077; set -o noclobber; cat "$A_LEDGER" "$B_LEDGER" > "$MASTER_LEDGER" )
chmod 600 "$MASTER_LEDGER"
python -m skillscout.evaluation calibrate validate "$DATASET" --reviews "$MASTER_LEDGER"
```

This manual concatenation creates a new combined event history; it does not edit either reviewer ledger. The CLI has no built-in merge command. Keep all three files. The master validation must report 64 independent review events, no reviewer-token collision, no malformed history, and no held-out review events. If either source ledger is incomplete, do not merge yet.

## Compare labels and adjudicate

Compare the two current events for each development case only after both independent reviews are finished. The event JSONL contains the reviewers' statuses, decisions, candidate orders, rationales, reasons, evidence references, and event IDs. `review status` marks each development case `consensus` when the current signatures agree or `conflict` when they differ.

Consensus is derived, not a separate ledger event. The implementation compares status, decision, and candidate order; differing rationales alone do not create a conflict. A conflict requires an adjudication event from a third, distinct reviewer. The adjudicator must inspect both current events and cite both event IDs.

After both reviewers have submitted, the coordinator can print only the current development review events with this read-only comparison. It accounts for any explicit revisions by selecting the latest review/revision per reviewer; earlier events remain in the ledger.

```sh
python3 - "$MASTER_LEDGER" <<'PY'
import json, sys
events = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
current = {}
for event in events:
    if event["example_id"].startswith("dev-") and event["event_type"] in {"review", "revision"}:
        current[(event["example_id"], event["reviewer_id"])] = event
for number in range(1, 33):
    example_id = f"dev-{number:03d}"
    rows = [event for (case, _), event in current.items() if case == example_id]
    rows.sort(key=lambda event: event["reviewer_id"])
    print(example_id)
    for event in rows:
        print(json.dumps({key: event[key] for key in (
            "reviewer_id", "event_id", "status", "decision", "candidate_order",
            "rationale", "reason", "evidence_basis"
        )}, ensure_ascii=False, sort_keys=True))
PY
```

For a judgeable adjudication:

```sh
python -m skillscout.evaluation calibrate review adjudicate "$DATASET" EXAMPLE_ID \
  --reviews "$MASTER_LEDGER" --reviewer adjudicator \
  --status judgeable --decision DECISION \
  --rationale "ADJUDICATOR'S EVIDENCE-BASED RESOLUTION" \
  --evidence-basis "SPECIFIC EVIDENCE FACT" \
  --evidence-basis "ANOTHER SPECIFIC FACT, IF NEEDED" \
  --adjudicates EVENT_ID_A EVENT_ID_B
```

If the evidence supports no resolved decision, use `--status ambiguous` or `--status unjudgeable`, omit `--decision` and candidate order, provide `--reason`, and still pass `--adjudicates EVENT_ID_A EVENT_ID_B`. Then validate the full ledger and check that the example is `adjudicated`. The adjudication is appended; the two original events remain in the master history.

A reviewer who must correct their own submitted label uses `review submit --revise`; this appends a revision linked to the prior event ID. Do not manually edit JSONL. The CLI rejects duplicate event IDs, duplicate current labels from one reviewer, held-out submissions by default, revisions after adjudication, and adjudications that omit current conflicting event IDs.

After completeness, compare, and adjudication checks, the master ledger is ready for the later development evaluation. Do not evaluate held-out examples as part of reviewer preparation.

## Tooling diagnosis and blinding limit

The enabled tool registry for the previous session contained no `prepare_task` or SkillScraper MCP tool, so no MCP call occurred and no recommendation from that tool was received. I checked `codex mcp list`: it contains no `skillscout` server entry. The repository does define `prepare_task` in `src/skillscout/mcp_server.py`, and `src/skillscout/mcp_entrypoint.py` starts the stdio server. The cause was host configuration: SkillScraper is not registered in this Codex MCP environment. This does not indicate a failed handler call. To enable it, add the documented `mcp_servers.skillscout` entry pointing to the installed `skillscout-mcp` executable, provide only the required environment variable names through the approved secret mechanism, restart Codex, and verify with `codex mcp list` and `/mcp`. No MCP configuration was changed for this preparation.

The ledger implementation supports append-only independent events and adjudication. Its CLI does not mechanically blind reviewers: `review show` includes the current SkillScraper proposal and review state, and a shared-account user can read any ledger they can access. The prepared process works around this by distributing evidence-only packets first, keeping separate ledgers, and withholding both proposal appendices until both submissions are complete. This also keeps the prior AI assessment hypotheses out of the independent packets. No policy or ledger-schema change is required to begin using this controlled process. For a fully tool-enforced blind workflow and merge, the smallest safe code change is a reviewer-specific blind packet/show mode plus a `review merge` command that validates two source ledgers and creates a new output ledger without overwriting any existing file; the decision policy and event schema need no change.

## Ledger behavior checked in source

`append_review_event` validates the proposed event and the complete prior history before writing. `_append_private_jsonl` uses `O_APPEND`, creates a missing ledger with mode `0600`, rejects symlink/non-regular targets, and does not truncate an existing file. A revision is a new `revision` event with `supersedes_event_id`; the prior event stays in the JSONL history. Adjudication is a new event that references both current conflicting review event IDs; it does not replace them. `review_state` derives `unreviewed`, `awaiting_independent_review`, `consensus`, `conflict`, or `adjudicated` from history. Consensus is not a separate event. The consensus signature includes status, decision, and candidate order; rationale and evidence text are retained but do not affect consensus equality.

This is a source inspection, not a write-path test: no synthetic or human review event was added during packet preparation.
