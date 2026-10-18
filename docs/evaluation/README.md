# SkillScout task evaluation

`tasks.jsonl` contains 20 human-authored task cases spanning Python, JavaScript/TypeScript, Rust, Go, Ruby, PHP, Dart, Java, Swift, Terraform, Kubernetes, PostgreSQL, routine edits, unknown compatibility, missing audit evidence, and a synthetic critical-risk fixture. The expected outcomes and candidate names are hypotheses for evaluation; candidate IDs have not been verified against a live catalog.

## Current measurement status

No live SkillScout-versus-catalog comparison was measured on 2026-10-18. The attempted development dependency sync failed because DNS could not resolve `pypi.org`; this environment therefore could not reach the catalog. Two routine tasks were run through the installed wheel's no-catalog fast path: both returned `NO_SKILL_NEEDED`, with one process wall-time sample of 214.69 ms and 214.60 ms, respectively, and zero optional LLM cost because no provider call was made. These two local cases do not establish broad no-skill accuracy. Their ordinary search baselines and comparative decisions remain unmeasured. The synthetic critical case is covered by deterministic policy tests, but it is not a live-catalog result.

There are no measured top-three relevance, false-recommendation, general no-skill accuracy, catalog-backed end-to-end latency, or nonzero provider-cost scores. No performance or quality target is proposed here. Do not treat the task hypotheses or unit tests as evidence of real-world selection quality.

## Comparison protocol for a network-enabled run

For each task, use the identical task query and catalog snapshot for both paths:

1. Record raw skills.sh search results in returned order. Treat the first three results as the ordinary catalog-search baseline.
2. Run SkillScout with the same task and project metadata. Record its top three, overall decision, candidate decisions, evidence states, and inspection time.
3. Compare candidate relevance against the task's predeclared candidate hypotheses. Report precision@3 and recall@3 separately; do not count unverified candidate names as matches.
4. Count correct `NO_SKILL_NEEDED` decisions only for cases whose expected outcome is no skill. Count a skill recommendation on those tasks as an incorrect recommendation. Report `REVIEW` for missing evidence as correct only where the expected outcome calls for review.
5. Run the synthetic critical fixture through scanner/policy tests and confirm it never receives `USE`.
6. Measure end-to-end command wall time, separately from catalog request time. Run with LLM disabled for the deterministic baseline. If the LLM is explicitly enabled, record provider, model, reported usage, and actual cost separately; otherwise cost is `$0` for that run.

Keep fixture-only results, live results, and proposed targets in separate sections. A 20-task set is exploratory and too small to support general claims even after it is measured.
