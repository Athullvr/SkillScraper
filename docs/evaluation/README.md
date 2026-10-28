# SkillScout task evaluation

Historical evaluation protocol and results use the SkillScout name and system identifiers recorded when the study was defined. The current product is SkillScraper. Dataset fields, system-arm labels, dates, and measurement status remain unchanged.

The original 20-case set below is exploratory. A separate fixed 15-task evaluation v1 set with per-task human relevance criteria and current run status is available in [`v1/README.md`](v1/README.md). The v1 dataset does not overwrite or reinterpret these earlier exploratory results.

`tasks.jsonl` contains 20 human-authored cases spanning Python, JavaScript/TypeScript, Rust, Go, Ruby, PHP, Dart, Java, Swift, Terraform, Kubernetes, PostgreSQL, routine edits, unknown compatibility, missing audit evidence, and a synthetic critical-risk fixture. Expected outcomes, rationales, and candidate-topic hypotheses were recorded before the measurements below. Candidate-topic names are hypotheses only; they are not verified skills.sh IDs.

## Relevance and decision criteria

For a catalog-backed task, a returned candidate counts as relevant only after its stable catalog ID and detail content have been inspected. The content must substantively address the task and at least one predeclared topic hypothesis; matching a name or search snippet alone is insufficient. A hypothesis is never converted into a catalog ID without a real response. Compatibility is judged separately from relevance using project facts, prerequisites, and explicit unknowns.

For top-three comparisons, use the first three SkillScout candidates and the first three skills.sh results in ordinary search order. Precision@3 is relevant candidates in those three slots divided by three; an empty slot counts as not relevant. Hypothesis recall@3 is the number of the task's predeclared topic hypotheses covered by at least one inspected top-three candidate divided by the number of hypotheses. A candidate can cover more than one topic. Apply the same relevance labels to both lists. If the live catalog does not provide enough evidence to map a hypothesis, report it as unknown rather than a miss.

Count a correct no-skill decision only when the expected outcome is `no_skill_needed` and SkillScout returns `NO_SKILL_NEEDED`. A `USE` result for such a task is an incorrect recommendation. For unknown compatibility and missing evidence, `REVIEW` is correct only where the expected outcome calls for review. The synthetic critical case is evaluated separately: the fixture must not receive `USE`. The ordinary search baseline only returns candidates and has no no-skill or security policy, so it cannot be credited with those decisions. Report `better_decision` only when the paired outputs support a meaningful comparison; otherwise leave it unmeasured.

Latency is end-to-end CLI wall time. Optional LLM cost is zero only for a measured run with no provider call; unrun cases have no measured cost.

## Results measured on 2026-10-18

The expected outcomes and relevance rules above were established before this verification's recommendation measurements. PyPI access succeeded for lock generation and installation, but the live skills.sh API requires authentication. The SkillScout smoke command exited 3 with `catalog_authentication_error`; a direct search request returned HTTP 401 and `authentication_required`, instructing the caller to provide `VERCEL_OIDC_TOKEN`. The [skills.sh API reference](https://www.skills.sh/docs/api) documents bearer-token authentication for API access. No search result was received, so no real candidate ID, detail record, content hash, audit record, or proposed install command was observed.

The 17 catalog-dependent cases remain unrun: 15 specialist-match cases, one uncertain-compatibility case, and one missing-audit case. Their SkillScout results and the ordinary catalog-search baseline for all 19 nonsynthetic tasks are recorded as `not_run_catalog_authentication_required`, with null runtime and cost where appropriate. No candidate hypotheses were converted into catalog IDs.

The two routine no-skill tasks ran through the installed wheel's deterministic no-catalog fast path three times each. All six executions returned `NO_SKILL_NEEDED`, so **2/2 expected no-skill task cases were correct** in this local subset, with zero incorrect recommendations among those cases. The routine validation-test task had samples of 176.02, 177.25, and 174.03 ms (median 176.02 ms); the README typo task had samples of 173.97, 174.00, and 178.87 ms (median 174.00 ms). There were zero catalog requests and zero optional LLM cost for those runs. The ordinary search baseline was not measured for either task.

The synthetic critical-risk case is covered by deterministic security-policy tests, not by a catalog recommendation run. Top-three precision, hypothesis recall, paired catalog-search comparison, broad no-skill accuracy, and incorrect recommendations across catalog tasks are **not measured**. These results do not establish general recommendation quality or statistical significance.

## Comparison protocol for a network-enabled run

1. Record the actual search response and its first three stable IDs in returned order.
2. Run SkillScout for the same task against the same catalog snapshot and record its first three IDs, decision, compatibility, evidence states, and latency.
3. Fetch candidate details and manually map actual IDs to the predeclared topic hypotheses using the relevance rule above. Do not count unverified candidate-name hypotheses as matches.
4. Report precision@3 and hypothesis recall@3 for both lists, plus correct no-skill decisions and incorrect `USE` recommendations. Keep `REVIEW` results separate.
5. Run the synthetic critical fixture through the deterministic scanner and policy tests; it must never receive `USE`.
6. If optional LLM is explicitly enabled, record provider, model, usage, and actual cost separately. The deterministic comparison keeps the LLM disabled.

The 20-task set is exploratory and too small to support general claims, even after every task has been measured.
