# SkillScout offline calibration

The calibration runner checks how the current production ranking and decision pipeline behaves on a versioned set of local examples. It uses `prepare_recommendation()` with an in-memory catalog adapter, local project facts, and optional LLM assessment disabled. It does not access the catalog, require credentials, write to MCP requests, or tune thresholds.

The checked-in `v1/dataset.json` is labeled `synthetic_smoke_test_only`. Its results verify that fixtures and reporting are wired to the product code; they are **not human-reviewed quality measurements** and do not validate real-world matching quality.

## Run and compare

From a source checkout with the project environment installed:

```sh
python -m skillscout.evaluation calibrate run docs/calibration/v1/dataset.json \
  --output /tmp/skillscout-calibration-baseline.json \
  --report /tmp/skillscout-calibration-baseline.md
```

After a proposed implementation change, create a second run in a new file and compare:

```sh
python -m skillscout.evaluation calibrate run docs/calibration/v1/dataset.json \
  --output /tmp/skillscout-calibration-candidate.json
python -m skillscout.evaluation calibrate compare \
  /tmp/skillscout-calibration-baseline.json \
  /tmp/skillscout-calibration-candidate.json \
  --output /tmp/skillscout-calibration-comparison.json \
  --report /tmp/skillscout-calibration-comparison.md
```

Output paths must be new files; the CLI refuses to overwrite artifacts. The run JSON contains the dataset SHA-256, SkillScout version, source fingerprint, configuration, stable example IDs, observed decisions, candidate order, bounded decision/evidence trace, diagnostics, and metrics. The generated timestamp uses the dataset's fixed evaluation clock so identical source, configuration, and fixtures produce reproducible run artifacts. Run integrity hashes detect accidental edits; they are not signatures or proof of who produced a file.

The report separates human-reviewed metrics from synthetic smoke metrics. It includes overall accuracy, per-decision precision/recall, false decision rates and their denominators, false USE share among all judgeable examples, candidate ranking, safety/evidence invariant checks, and challenge-category and split breakdowns. Incomplete and ambiguous examples are reported separately and excluded from accuracy. Comparison requires identical dataset content and ordered example IDs; it reports decision changes, category changes, ranking-only changes, and safety/evidence regressions. Synthetic score improvements are explicitly marked as smoke-test results.

## Dataset format

The versioned JSON format is defined by [`v1/schema.json`](v1/schema.json) and enforced by the runner. v1 examples contain:

- Stable `id`, `split` (`development` or `held_out`), challenge category, and assessment status.
- A realistic task, bounded project facts, local search completeness and candidate fixture metadata.
- Exact bounded evidence excerpts, revision hash, evidence scope, and explicit audit status/findings.
- Expected decision and, where order is meaningful, the full expected candidate order.
- Concise rationale and `evidence_basis` entries that cite the specific fixture facts supporting the expected label.
- Label provenance (`human_reviewed`, `synthetic_fixture`, or `pending`) and a non-identifying reviewer ID only for human labels.

Store excerpts needed for a decision, not whole repositories or full skill archives. Use deliberate synthetic fixtures or public, intentionally selected excerpts with permission. Never include credentials, private repository material, personal information, or copied developer conversations. The runner rejects common credential-like strings, path traversal excerpts, duplicate JSON keys, oversized inputs, duplicate IDs, and structurally invalid fixtures.

`development` and `held_out` are recorded as separate metric groups. Use development examples to discuss a proposed change. Keep held-out examples sealed from threshold, vocabulary, or ranking choices until a human-approved evaluation round; repeatedly inspecting held-out outcomes turns them into development data. The runner never selects or tunes thresholds.

## Human labeling rubric

Review the task, candidate listings, exact excerpts, search coverage, project facts, revision binding, current audit evidence, and compatibility evidence together. Record only facts available in that fixture. A missing fact is unknown; it is not a clean result, a capability, or a risk.

- **`NO_SKILL_NEEDED`**: use for a straightforward task covered by the local fast path, or after a complete, adequately scoped search with enough candidate evidence to judge that no materially useful candidate was found. Do not use it merely because a returned candidate did not document its capability. Incomplete search, unavailable detail, or title-only evidence alone cannot establish this label.
- **`USE`**: the inspected, revision-bound documentation supports the requested operation and task concepts; evidence is sufficient for relevance; compatibility is established; and the existing safety gates pass. This is a recommendation, never permission to install or execute.
- **`REVIEW`**: the candidate could be relevant but a material fact remains unresolved, such as incomplete inspection, absent/stale/unbound audit, uncertain compatibility, or weak capability evidence. Name the unknown and why it matters.
- **`AVOID`**: affirmative inspected evidence supports a material safety concern under the current policy. Do not infer danger from a missing audit, incomplete scan, or absent capability evidence.

Use `assessment_status: "incomplete"` when the evidence packet or search does not contain enough information to make a defensible decision. Use `"ambiguous"` when evidence is present but reasonable reviewers cannot resolve the expected label or candidate order under this rubric. Both require `expected_decision: null` and `label_provenance: "pending"`. These are **indeterminate examples**, not model errors and not successes.

Use `assessment_status: "judgeable"` only when reviewers can state a defensible expected outcome and cite its evidence. If reviewers agree on the outcome but the implementation returns another outcome, that is an incorrect decision. If they cannot resolve the outcome even after review, mark the example ambiguous instead of forcing a label. Candidate order should be null unless the evidence supports an order; when present, list each candidate in that ranking judgment exactly once, using no more IDs than the configured candidate limit.

For `NO_SKILL_NEEDED`, explicitly record adequate coverage or the applicable local fast path. For `USE`, cite the exact operation-and-topic excerpt, revision binding, compatibility fact, and current passing audit evidence. For `AVOID`, cite the affirmative risk signal and whether it is verified. Preserve unknown or conflicting observations as `REVIEW` or indeterminate rather than converting them into confident labels.

## Diagnostics and limits

Misclassified examples include a compact stage hypothesis derived from recorded trace fields: task normalization, retrieval/completeness, listing relevance, operation evidence, inspection, decision threshold, or safety/compatibility. A stage is `unknown` when the trace cannot establish a cause. The `explanation_generation` stage is explicitly marked `unknown` because the calibration format does not yet carry a separate reviewed expected-explanation annotation; explanation quality cannot be scored independently until that annotation is added in a later dataset revision.

This tool does not establish representative quality without a sufficiently broad, human-reviewed dataset. Synthetic fixtures exercise the wiring and selected edge cases only. The next human task is to assemble a diverse development set from deliberately prepared, non-sensitive examples, label it independently under this rubric, adjudicate disagreements, then reserve a genuinely untouched held-out set before proposing threshold or vocabulary changes.
