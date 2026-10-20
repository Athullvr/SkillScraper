# SkillScout evaluation v1

This version defines a fixed 15-task evaluation set in [`tasks.jsonl`](tasks.jsonl): five routine tasks expected to need no skill, five tasks where specialized guidance may help, and five weak-match, incomplete-evidence, or suspicious-candidate cases. Human relevance criteria are recorded per task before any catalog-backed comparison. They are based on task requirements, not SkillScout scores or rank order.

The task set is a small, authored benchmark. None of these prompts are represented as actual developer requests. The critical-risk case is explicitly a synthetic policy fixture, not a catalog candidate. The earlier 20-task exploratory set remains at [`../tasks.jsonl`](../tasks.jsonl) and is not silently replaced by this version.

## Judgment and comparison protocol

For each ordinary skills.sh search result and each SkillScout candidate, inspect the detail record and label task relevance against that task's `human_relevance_judgment.criteria`. A name, description, install count, search snippet, SkillScout score, or rank alone is not evidence of relevance. Record the real stable ID and detail content hash only after receiving them. If content is unavailable or cannot be tied to a returned identity, label relevance `unknown`, not relevant or irrelevant. Keep compatibility separate from task relevance.

Use a paired comparison for each of the 14 live-catalog tasks:

1. Run ordinary catalog search with the exact task text and record the first three stable IDs in returned order, request time, response status, and elapsed time.
2. Run SkillScout for the same task and project context with the optional LLM disabled. Record its decision, first three candidate IDs, compatibility, evidence states, content hashes, audit revision associations, warnings, and elapsed time.
3. Fetch and inspect candidate details through the documented API. Apply the predeclared human relevance criteria while hiding which system produced each candidate and its position when feasible. Never install or execute a candidate.
4. Keep search and SkillScout measurements paired to a catalog time window. Repeat the pair three times and record observed IDs/hashes because the catalog can change between requests. Preserve raw API response metadata only as needed for reproducibility; do not check in skill documents, credentials, or project source.
5. Run the synthetic critical-risk case only against the deterministic local policy fixture. It is not part of live search precision/recall or host results.

The 14 live tasks are the ten routine/useful tasks plus four weak or incomplete-evidence tasks. The synthetic case is excluded from catalog queries. For the routine and weak-match tasks, an irrelevant returned candidate is an unnecessary recommendation. For useful tasks, count a useful selection only when the inspected content meets the human criteria. A relevant skill absent from the top three is a miss only when the relevant content and identity could actually be established. Report unresolved candidates as unknown.

## Measures

- **Useful-skill selection:** relevant inspected candidates among the top three, reported for each system and task.
- **Unnecessary recommendations:** irrelevant candidate slots among the top three for the five expected no-skill tasks and two weak-match tasks. Empty slots are not recommendations.
- **Relevant skills missed:** predeclared task concepts not represented by an inspected relevant candidate in the top three. Unknown mappings are reported separately.
- **Decision outcomes:** counts of `NO_SKILL_NEEDED`, `USE`, `REVIEW`, and `AVOID`, judged against the per-task expected behavior. Ordinary search has no policy decision and is not credited for these categories.
- **Evidence completeness and provenance:** stable identity/source, inspected detail completeness, content hash, audit availability/freshness/revision match, compatibility status, and scanner findings. A hash identifies returned content; it does not authenticate its publisher. A clean heuristic scan is not proof of safety.
- **Time to useful decision:** wall-clock time from request start through a result that a human can judge, measured separately for ordinary search and SkillScout. Record errors and incomplete results as failures with time, not as fast decisions.

Report task-level values and aggregate counts; do not infer general accuracy or statistical significance from 15 tasks. The security fixture is simulated. Fixture-based unit tests are offline. A live API run requires authorized credentials. Actual developer-task performance is not measured by this dataset.

## Reproduction and authentication

Pin the SkillScout checkout/revision, Python version, catalog date, project context, candidate limit, and whether the optional LLM is disabled. Run `skillscout doctor` first. For SkillScout's normal recommendation, use the same task text and project context for each task; the MCP `prepare_task` path always disables the optional LLM. For ordinary search, use the documented `GET /api/v1/skills/search?q=...&limit=...` API and preserve the first three IDs.

The current [skills.sh API reference](https://www.skills.sh/docs/api) documents Vercel OIDC bearer authentication and says the audit endpoint requires authentication. Local setup requires a linked Vercel project and a fresh `VERCEL_OIDC_TOKEN` made available to the SkillScout/search process. SkillScout reads `VERCEL_OIDC_TOKEN` or the `SKILLSCOUT_CATALOG_TOKEN` bearer-token override from its process environment; it does not load `.env.local` or refresh tokens. Never include token values in commands saved to history, result files, MCP configuration, or chat. The docs do not describe an unauthenticated search/detail route or a separate skills.sh API key. See [Vercel OIDC](https://vercel.com/docs/oidc) for the supported local setup.

## Current run status

The 2026-10-20 snapshot is in [`results.json`](results.json). Neither `SKILLSCOUT_CATALOG_TOKEN` nor `VERCEL_OIDC_TOKEN` was present, and `skillscout doctor` reported no catalog credential. Consequently no authorized live search, details, audit, content hash, revision match, or ordinary-search comparison was collected. The prior recorded unauthenticated live request on 2026-10-18 returned HTTP 401; it is historical evidence, not a new successful live check.

The initial local routine fast-path probe ran each of the five routine tasks three times, passing the SkillScout repository itself as `--project`; this checks task classification and error handling, not compatibility for the task-specific project contexts in the dataset. Four tasks returned `NO_SKILL_NEEDED` on all three runs. The local-variable rename task did not match the routine classifier and attempted catalog access; all three runs returned the operational `catalog_error` after bounded retries (exit 3), without a candidate or a valid no-skill decision. This pre-fix failure is preserved in [`results.json`](results.json), not relabeled after seeing the outcome. A follow-up regression probe after the narrow classifier fix returned `NO_SKILL_NEEDED` on all three exact-task CLI runs without contacting the catalog. The ordinary-search baseline was not run. No comparison-quality claim is supported.

The supported Codex CLI host was exercised on 2026-10-20 using a temporary inline MCP registration and `--ephemeral --sandbox read-only`. It initialized and parsed successful `doctor` and routine `prepare_task` results, plus a structured `invalid_input` result from `inspect_skill`. A paired routine-task invocation check produced no SkillScout calls without project-local instructions and a successful `prepare_task` call with the project-local `AGENTS.md` instruction. No catalog-backed host call was attempted. These are single-prompt invocation checks, not a general guarantee or an evaluation of recommendation quality.
