# SkillScout calibration and human review

The offline calibration runner executes the production ranking and decision pipeline over bounded local fixtures. It disables optional LLM assessment, uses an in-memory catalog adapter, and makes no network requests. It does not tune thresholds or affect MCP requests.

The checked-in [`v1/dataset.json`](v1/dataset.json) is explicitly `synthetic_smoke_test_only`. It is a tooling smoke test, not evidence of recommendation quality. Its labels are immutable through the review workflow. No genuine human-reviewed examples are currently included.

## Create a review dataset

Start from the empty [v2 dataset template](v2/dataset-template.json). Dataset v2 has an [enforced schema](v2/schema.json), is reserved for human review, and keeps every example's label fields pending. Do not put labels or reviewer names into the dataset. The separate [review-event schema](v2/review-event.schema.json) describes the append-only label ledger.

```sh
cp docs/calibration/v2/dataset-template.json path/to/review-dataset.json
```

Add prepared examples to that copy by hand, then run `calibrate validate` before review. Keep a new dataset version and ledger for each evidence change.

Add deliberately prepared, realistic examples using the existing v1 example structure: stable ID, task, development/held-out split, challenge category, project facts, search completeness and coverage, exact candidate listing metadata, exact bounded inspected excerpts, revision hash, audit status/findings, and compatibility facts represented in project metadata. Excerpts must be sufficient to reproduce the judgment without storing whole repositories or archives. Keep credentials, private repository content, personal information, and developer conversations out of both files. Never copy live catalog material as part of this workflow.

Use IDs that remain stable as the dataset grows. Freeze a dataset version and evaluation clock before reviews begin. Review events include the exact dataset SHA-256; after any dataset edit, create a new dataset version and a new ledger instead of silently carrying old labels forward.

Dataset v2 rejects duplicate IDs, malformed candidate evidence, inline judgments, unsupported decisions, and the same normalized task in both development and held-out splits. The synthetic v1 smoke set retains its existing fixtures, including repeated task wording, and is not eligible for review events.

## Review workflow

Run the commands from the source checkout with its project environment installed. The review ledger is local JSONL, created with owner-only permissions on supported platforms. It is append-only: revisions and adjudications add events and retain the prior labels.

```sh
python -m skillscout.evaluation calibrate review show path/to/review-dataset.json EXAMPLE_ID
```

`show` displays the task, candidate listing, exact evidence excerpts, audits, current production proposal, and proposal rationale. For held-out examples, it refuses to reveal the packet unless explicitly requested:

```sh
python -m skillscout.evaluation calibrate review show path/to/review-dataset.json EXAMPLE_ID --include-held-out
```

Record a judgeable decision with concise rationale and specific evidence citations. Use non-identifying reviewer tokens and have two reviewers label independently:

```sh
python -m skillscout.evaluation calibrate review submit path/to/review-dataset.json EXAMPLE_ID \
  --reviews path/to/reviews.jsonl --reviewer reviewer-a --status judgeable \
  --decision REVIEW --rationale "The operation is plausible, but compatibility evidence is incomplete." \
  --evidence-basis "SKILL.md excerpt documents the requested operation." \
  --evidence-basis "Project metadata does not establish the required runtime."

python -m skillscout.evaluation calibrate review submit path/to/review-dataset.json EXAMPLE_ID \
  --reviews path/to/reviews.jsonl --reviewer reviewer-b --status judgeable \
  --decision REVIEW --rationale "The relevant operation is documented; project compatibility remains unknown." \
  --evidence-basis "SKILL.md excerpt documents the requested operation." \
  --evidence-basis "No project fact establishes compatibility."
```

For genuinely indeterminate cases, do not force a decision:

```sh
python -m skillscout.evaluation calibrate review submit path/to/review-dataset.json EXAMPLE_ID \
  --reviews path/to/reviews.jsonl --reviewer reviewer-a --status ambiguous \
  --reason "Two reasonable decision labels remain possible from the recorded evidence."
```

`unjudgeable` means essential evidence is absent or the search is incomplete; `ambiguous` means evidence is present but the rubric does not resolve the outcome. Both require a reason and remain outside accuracy metrics. A wrong but defensible expected label is a misclassification, not an ambiguous example. A missing capability excerpt does not prove that no useful skill exists.

Inspect the ledger state and adjudicate only after two different reviewers disagree:

```sh
python -m skillscout.evaluation calibrate review status path/to/review-dataset.json --reviews path/to/reviews.jsonl
python -m skillscout.evaluation calibrate review adjudicate path/to/review-dataset.json EXAMPLE_ID \
  --reviews path/to/reviews.jsonl --reviewer adjudicator --status judgeable \
  --decision REVIEW --rationale "The documented capability is relevant, but audit evidence is unknown." \
  --evidence-basis "SKILL.md excerpt documents the requested operation." \
  --evidence-basis "No current revision-bound audit is present." \
  --adjudicates EVENT_ID_A EVENT_ID_B
```

Adjudicators must use a distinct reviewer token and cite every current conflicting review event. A consensus requires matching status, decision, and candidate order. A disagreement stays unresolved until adjudicated. A reviewer who changes their own label uses `review submit --revise`; the earlier event is preserved. No review event can be added to a synthetic example.

Validate both the dataset structure and the entire event history at any time. The command reports conflicts and reviews awaiting a second independent label without converting them into resolved labels:

```sh
python -m skillscout.evaluation calibrate validate path/to/review-dataset.json --reviews path/to/reviews.jsonl
```

For `NO_SKILL_NEEDED`, reviewers must establish either the local routine-task fast path or complete, adequate search coverage with complete evidence for returned candidates. For `USE`, the inspected revision must document the requested operation, current revision-bound audit evidence must pass, and compatibility must be supported. A topical or title-only match is insufficient. `REVIEW` is appropriate when relevance is plausible but material evidence remains unknown; it is not a substitute for an incorrect `USE` label. `AVOID` requires a confirmed critical local finding or a current, revision-bound high/critical or failing audit. Missing or stale safety evidence is unknown, never proof of safety or danger.

## Offline evaluation and held-out protection

Run a review ledger against the dataset. By default, held-out examples are omitted from evaluated example rows and all metrics; the report gives their excluded count. Human development results, human held-out results, and synthetic smoke-test results remain separate:

```sh
python -m skillscout.evaluation calibrate run path/to/review-dataset.json \
  --reviews path/to/reviews.jsonl --output /tmp/skillscout-dev-run.json \
  --report /tmp/skillscout-dev-run.md
```

The default report includes human-reviewed development metrics and synthetic smoke-test metrics. It does not report held-out labels or predictions. False USE, false `NO_SKILL_NEEDED`, and unnecessary `REVIEW` rates use predictions of that decision as their denominators; false USE share of all judgeable examples is shown separately. Per-decision precision/recall and candidate-order metrics also include their denominators. Only for an explicit final evaluation, request:

```sh
python -m skillscout.evaluation calibrate run path/to/review-dataset.json \
  --reviews path/to/reviews.jsonl --include-held-out \
  --output /tmp/skillscout-final-run.json --report /tmp/skillscout-final-run.md
```

Do not use held-out results to choose thresholds, ranking weights, vocabulary, or other behavior. Do not combine synthetic, development, and held-out accuracies into one score. Each group reports sample counts, explicit denominators, per-decision precision/recall, false USE, false `NO_SKILL_NEEDED`, unnecessary `REVIEW`, and candidate-order metrics. Groups with fewer than 30 judgeable examples receive a small-sample warning; this warning is not a statistical guarantee.

Compare two runs on exactly the same dataset and held-out inclusion setting:

```sh
python -m skillscout.evaluation calibrate compare /tmp/skillscout-dev-run.json /tmp/skillscout-dev-run-2.json \
  --output /tmp/skillscout-comparison.json --report /tmp/skillscout-comparison.md
```

The comparison identifies changed decisions, ranking-only changes, per-group metric changes, category changes, and safety/evidence regressions. A higher score in one group is never summarized as a cross-group aggregate improvement. Output paths must be new files.

## Reports and limitations

Run artifacts record the dataset hash/schema, source fingerprint, configuration, observed decisions, bounded evidence trace, review status and event references, diagnostics, and metrics. The runner uses the production pipeline with optional LLM assessment disabled and performs no catalog requests.

The checked-in synthetic examples exercise selected wiring and edge cases only. No genuine human judgments have been added, so there are no real-world recommendation-quality measurements and no threshold has been tuned from these fixtures. The next human-review task is to prepare a diverse, non-sensitive development set, collect two independent labels, resolve disagreements without deleting history, and reserve a separate untouched held-out set before evaluating quality.

Calibration run artifacts now use schema v2 for separated metric groups and review provenance. Regenerate any older v1 run artifacts before comparing them with current runs. The review interface is intentionally a local CLI; assembling task fixtures and choosing non-sensitive evidence excerpts remain reviewer-preparation work.
