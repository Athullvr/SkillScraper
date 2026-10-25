# SkillScout calibration and human review

The offline calibration runner executes the production ranking and decision pipeline over bounded local fixtures. It disables optional LLM assessment, uses an in-memory catalog adapter, and makes no network requests. It does not tune thresholds or affect MCP requests.

The checked-in [`v1/dataset.json`](v1/dataset.json) is explicitly `synthetic_smoke_test_only`. It is a tooling smoke test, not evidence of recommendation quality. Its labels are immutable through the review workflow. No genuine human-reviewed examples are currently included.

The proposed [`v2/proposed-dataset.json`](v2/proposed-dataset.json) contains 40 deliberately prepared, non-sensitive synthetic examples: 32 development and 8 held-out. It includes 46 synthetic candidate listings with bounded `SKILL.md` excerpts and synthetic audit states, including one identical listing pair that produces a ranking tie. It contains no live catalog material, credentials, or private developer conversations. Every example remains `assessment_status: pending`, `label_provenance: pending`, `reviewer: null`, and has null expected labels. The v2 schema's `data_kind: human_reviewed` value identifies the review workflow; it does not mean any example has been reviewed. Do not count these proposals as reviewed examples or use them to tune ranking behavior.

Coverage includes routine no-skill tasks, explicit operation evidence, topical-only matches, paraphrases of the same operation, ambiguous requests, incomplete and unavailable searches, missing candidate detail, compatibility uncertainty, unknown/stale/failing safety evidence, multiple plausible candidates, negative instructions, and misleading headings. The checked-in v1 synthetic smoke cases remain a separate immutable dataset.

## Create a review dataset

Use the prepared [v2 proposal](v2/proposed-dataset.json) for the first review, or start a separate dataset from the empty [v2 template](v2/dataset-template.json). Dataset v2 has an [enforced schema](v2/schema.json), is reserved for human review, and keeps every example's label fields pending. Do not put labels or reviewer names into the dataset. The separate [review-event schema](v2/review-event.schema.json) describes the append-only label ledger.

```sh
cp docs/calibration/v2/proposed-dataset.json path/to/review-dataset.json
```

For a new collection, add prepared examples to the template copy by hand, then run `calibrate validate` before review. Keep a new dataset version and ledger for each evidence change.

Add deliberately prepared, realistic examples using the existing v1 example structure: stable ID, task, development/held-out split, challenge category, project facts, search completeness and coverage, exact candidate listing metadata, exact bounded inspected excerpts, revision hash, audit status/findings, and compatibility facts represented in project metadata. Excerpts must be sufficient to reproduce the judgment without storing whole repositories or archives. Keep credentials, private repository content, personal information, and developer conversations out of both files. Never copy live catalog material as part of this workflow.

Use IDs that remain stable as the dataset grows. Freeze a dataset version and evaluation clock before reviews begin. Review events include the exact dataset SHA-256; after any dataset edit, create a new dataset version and a new ledger instead of silently carrying old labels forward.

Dataset v2 rejects duplicate IDs, malformed candidate evidence, inline judgments, unsupported decisions, and the same normalized task in both development and held-out splits. The synthetic v1 smoke set retains its existing fixtures, including repeated task wording, and is not eligible for review events.

## Review workflow

Run the commands from the source checkout with its project environment installed. The review ledger is local JSONL, created with owner-only permissions on supported platforms. It is append-only: revisions and adjudications add events and retain the prior labels.

Freeze a copy of the proposal and start a new ledger. The following commands create a fresh review directory so they do not overwrite existing work. Keep that dataset file unchanged once anyone begins review; edits require a new dataset version and a new ledger because every event is bound to the exact dataset SHA-256:

During development, review only `development` examples. `review show`, `review submit`, and `review adjudicate` all refuse held-out examples unless `--include-held-out` is supplied. Leave those eight examples pending until implementation and analysis are frozen; the explicit flag is reserved for final evaluation.

```sh
REVIEW_DIR="$(mktemp -d /tmp/skillscout-review.XXXXXX)"
DATASET="$REVIEW_DIR/dataset.json"
REVIEWS="$REVIEW_DIR/reviews.jsonl"
cp docs/calibration/v2/proposed-dataset.json "$DATASET"
python -m skillscout.evaluation calibrate validate "$DATASET"
python -m skillscout.evaluation calibrate review status "$DATASET" --reviews "$REVIEWS"
python -m skillscout.evaluation calibrate review show "$DATASET" dev-003 --reviews "$REVIEWS"
```

The `show` packet contains the task, project facts, search completeness, exact candidate listings, inspected excerpts, revision-bound audit evidence, SkillScout's current proposal, and its rationale/limitations. It identifies the label as pending and the review state as unreviewed. Reviewers should inspect the packet independently before recording a judgment.

Each reviewer must supply a non-identifying reviewer token and one of these outcomes:

- `judgeable`: choose `--decision` from `NO_SKILL_NEEDED`, `USE`, `REVIEW`, or `AVOID`; include a concise `--rationale` and one or more specific `--evidence-basis` citations. Add `--candidate-order` with available candidate IDs, best first, when candidate ranking is assessable. It is optional when no supported ordering can be established.
- `ambiguous`: omit decision and candidate order, and provide `--reason` when the evidence is present but the rubric cannot resolve the outcome.
- `unjudgeable`: omit decision and candidate order, and provide `--reason` when essential evidence is absent or search is incomplete.

Use a wrong-but-defensible `USE` or `NO_SKILL_NEEDED` label as a misclassification if the evidence supports a decision; do not mark it ambiguous just to avoid counting an error. In particular, a `USE` label needs task-specific operation evidence, a current revision-bound passing audit, and supported compatibility. A topical/title match, missing audit, or unknown compatibility does not establish `USE`. `NO_SKILL_NEEDED` requires the local routine fast path or complete, adequate search with complete evidence for all returned candidates; an incomplete search or absent candidate detail is not proof that no relevant skill exists.

```sh
python -m skillscout.evaluation calibrate review show "$DATASET" EXAMPLE_ID --reviews "$REVIEWS"
```

`show` displays the task, candidate listing, exact evidence excerpts, audits, current production proposal, and proposal rationale. For held-out examples, it refuses to reveal the packet unless explicitly requested:

```sh
python -m skillscout.evaluation calibrate review show "$DATASET" EXAMPLE_ID \
  --reviews "$REVIEWS" --include-held-out
```

Record a judgeable decision with concise rationale and specific evidence citations. Use non-identifying reviewer tokens and have two reviewers label independently:

```sh
python -m skillscout.evaluation calibrate review submit "$DATASET" EXAMPLE_ID \
  --reviews "$REVIEWS" --reviewer reviewer-a --status judgeable \
  --decision REVIEW --rationale "The operation is plausible, but compatibility evidence is incomplete." \
  --evidence-basis "SKILL.md excerpt documents the requested operation." \
  --evidence-basis "Project metadata does not establish the required runtime."

python -m skillscout.evaluation calibrate review submit "$DATASET" EXAMPLE_ID \
  --reviews "$REVIEWS" --reviewer reviewer-b --status judgeable \
  --decision REVIEW --rationale "The relevant operation is documented; project compatibility remains unknown." \
  --evidence-basis "SKILL.md excerpt documents the requested operation." \
  --evidence-basis "No project fact establishes compatibility."
```

For genuinely indeterminate cases, do not force a decision:

```sh
python -m skillscout.evaluation calibrate review submit "$DATASET" EXAMPLE_ID \
  --reviews "$REVIEWS" --reviewer reviewer-a --status ambiguous \
  --reason "Two reasonable decision labels remain possible from the recorded evidence."
```

`unjudgeable` means essential evidence is absent or the search is incomplete; `ambiguous` means evidence is present but the rubric does not resolve the outcome. Both require a reason and remain outside accuracy metrics. A wrong but defensible expected label is a misclassification, not an ambiguous example. A missing capability excerpt does not prove that no useful skill exists.

Inspect the ledger state and adjudicate only after two different reviewers disagree:

```sh
python -m skillscout.evaluation calibrate review status "$DATASET" --reviews "$REVIEWS"
python -m skillscout.evaluation calibrate review adjudicate "$DATASET" EXAMPLE_ID \
  --reviews "$REVIEWS" --reviewer adjudicator --status judgeable \
  --decision REVIEW --rationale "The documented capability is relevant, but audit evidence is unknown." \
  --evidence-basis "SKILL.md excerpt documents the requested operation." \
  --evidence-basis "No current revision-bound audit is present." \
  --adjudicates EVENT_ID_A EVENT_ID_B
```

Adjudicators must use a distinct reviewer token and cite every current conflicting review event. A consensus requires matching status, decision, and candidate order. A disagreement stays unresolved until adjudicated. A reviewer who changes their own label uses `review submit --revise`; the earlier event is preserved. No review event can be added to a synthetic example.

When two reviewers disagree, an independent third reviewer adjudicates after inspecting the packet and both rationales. The adjudicator uses a distinct token, records a new evidence-backed decision or explicit ambiguous/unjudgeable outcome, and cites both current event IDs. Give special scrutiny to false `USE` recommendations and false `NO_SKILL_NEEDED` decisions: verify operation, safety, and compatibility evidence for the former, and complete search coverage for the latter. Do not invent consensus when a second reviewer is unavailable; one label remains `awaiting_independent_review` and contributes no accuracy result.

Validate both the dataset structure and the entire event history at any time. The command reports conflicts and reviews awaiting a second independent label without converting them into resolved labels:

```sh
python -m skillscout.evaluation calibrate validate "$DATASET" --reviews "$REVIEWS"
```

For `NO_SKILL_NEEDED`, reviewers must establish either the local routine-task fast path or complete, adequate search coverage with complete evidence for returned candidates. For `USE`, the inspected revision must document the requested operation, current revision-bound audit evidence must pass, and compatibility must be supported. A topical or title-only match is insufficient. `REVIEW` is appropriate when relevance is plausible but material evidence remains unknown; it is not a substitute for an incorrect `USE` label. `AVOID` requires a confirmed critical local finding or a current, revision-bound high/critical or failing audit. Missing or stale safety evidence is unknown, never proof of safety or danger.

## Offline evaluation and held-out protection

Run a review ledger against the dataset. By default, held-out examples are omitted from evaluated example rows and all metrics; the report gives their excluded count. Keep the held-out tasks sealed from anyone changing thresholds, ranking weights, wording, or features. Do not inspect or reveal a held-out packet during development. Only after the implementation and analysis plan are frozen should an evaluator run the explicit final command below. Human development results, human held-out results, and synthetic smoke-test results remain separate:

```sh
python -m skillscout.evaluation calibrate run "$DATASET" \
  --reviews "$REVIEWS" --output "$REVIEW_DIR/dev-run.json" \
  --report "$REVIEW_DIR/dev-run.md"
```

The default report includes human-reviewed development metrics and synthetic smoke-test metrics. It does not report held-out labels or predictions. Its review disposition counts report reviewed, judgeable, ambiguous, unjudgeable, unresolved, and still-pending examples within the included development scope; synthetic smoke-test labels are excluded from human-review counts. False `USE`, false `NO_SKILL_NEEDED`, and unnecessary `REVIEW` rates use predictions of that decision as their denominators; false `USE` share of all judgeable examples is shown separately. Per-decision precision/recall and candidate-order metrics also include their denominators. Only for an explicit final evaluation, request:

```sh
python -m skillscout.evaluation calibrate run "$DATASET" \
  --reviews "$REVIEWS" --include-held-out \
  --output "$REVIEW_DIR/final-run.json" --report "$REVIEW_DIR/final-run.md"
```

Do not use held-out results to choose thresholds, ranking weights, vocabulary, or other behavior. The CLI excludes held-out rows by default, and `review show` refuses held-out packets unless explicitly requested; treat that flag as evaluator-only after the development freeze. Do not combine synthetic, development, and held-out accuracies into one score. Each group reports sample counts, explicit denominators, per-decision precision/recall, false positives and false negatives by decision, false `USE`, false `NO_SKILL_NEEDED`, unnecessary `REVIEW`, and candidate-order metrics. Groups with fewer than 30 judgeable examples receive a small-sample warning; this warning is not a statistical guarantee.

Interpret the decision metrics as follows:

- **Decision correctness:** exact agreement with the resolved human label on judgeable examples; report numerator and denominator. Consensus and documented adjudication count; one-review, conflict, ambiguous, unjudgeable, pending, and operationally failed examples do not.
- **False-positive `USE`:** observed `USE` when the resolved expected decision is not `USE`. Report both false `USE` / predicted `USE` and false `USE` / all judgeable examples.
- **False negatives:** for each decision class, an expected decision that the system did not return. The per-decision metrics report false-negative counts and recall denominators. Call out false-negative `USE` (missed helpful selection) and false-negative `AVOID` (missed confirmed critical risk) separately when present.
- **Safety regressions:** compare runs on the same dataset and held-out inclusion setting. Report newly introduced unsupported candidate `USE`, missing complete safety/compatibility evidence, and other safety/evidence violations. Any newly confirmed critical safety regression blocks the change, even if decision accuracy improves.
- **Evidence sufficiency:** report candidate `USE` counts lacking confirmed operation support and counts lacking complete, current audit and compatible project evidence, each over all candidate `USE` decisions. Missing evidence is unknown, not proof of safety or danger. Keep these candidate-level rates separate from task-level decision accuracy.

Compare two runs on exactly the same dataset and held-out inclusion setting:

```sh
python -m skillscout.evaluation calibrate compare "$REVIEW_DIR/dev-run.json" "$REVIEW_DIR/dev-run-2.json" \
  --output "$REVIEW_DIR/comparison.json" --report "$REVIEW_DIR/comparison.md"
```

The comparison identifies changed decisions, ranking-only changes, per-group metric changes, category changes, and safety/evidence regressions. A higher score in one group is never summarized as a cross-group aggregate improvement. Output paths must be new files.

## Reports and limitations

Run artifacts record the dataset hash/schema, source fingerprint, configuration, observed decisions, bounded evidence trace, review status and event references, diagnostics, and metrics. The runner uses the production pipeline with optional LLM assessment disabled and performs no catalog requests.

The checked-in synthetic examples exercise selected wiring and edge cases only. No genuine human judgments have been added, so there are no real-world recommendation-quality measurements and no threshold has been tuned from these fixtures. The next task is to collect two independent labels for the proposed development examples, resolve disagreements without deleting history, and keep the held-out examples sealed until a final evaluation.

The proposed 40-example collection is ready for review. Its current genuine human-reviewed example count is **0**. The v1 smoke set and any generated run/comparison artifacts remain synthetic or unreviewed; neither is a recommendation-quality result. The offline runner uses only supplied fixture listings, disables optional LLM assessment, and makes no catalog requests, so calibration and review do not require catalog credentials or network access.

Calibration run artifacts now use schema v3 for separated metric groups, review provenance, and explicit human-review disposition counts. Regenerate older v1/v2 run artifacts before comparing them with current runs. The review interface is intentionally a local CLI; future additions to the proposed collection should use deliberately prepared non-sensitive fixtures and exact bounded evidence excerpts.
