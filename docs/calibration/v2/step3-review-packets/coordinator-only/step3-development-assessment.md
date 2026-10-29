# Step 3 development assessment — AI workpaper, not human review

**Status:** preparation only. This file contains model-generated assessment hypotheses to help human reviewers inspect the development set. It is not a review event ledger, it does not set expected labels, and it must not be counted as human-reviewed evidence. No human reviewer has labeled any example in this run.

## Dataset and procedure

- Dataset: `skillscout-human-review-proposal-2026-10`, version `2.1.0-proposed`.
- Dataset SHA-256: `bf306a0d41978821836d923ce5023553eba66ca819588385300dc2e0a185021e`.
- Actual counts: 40 total, 32 development, 8 held out. The held-out examples were not opened or evaluated.
- The v2 schema reserves this workflow for human review; its `data_kind: human_reviewed` value does not mean these examples have labels.
- The review ledger had zero events. All 32 development examples remain pending and unreviewed.
- Expected review-event choices are `NO_SKILL_NEEDED`, `USE`, `REVIEW`, and `AVOID`. A judgeable event needs a rationale and specific evidence citations. `ambiguous` and `unjudgeable` events omit the decision and explain why. Two independent human reviews are needed for consensus; conflicting reviews require a distinct adjudicator. The ledger is append-only and bound to the exact dataset hash.
- `USE` requires task-specific operation evidence, complete inspected content, a current revision-bound passing audit, and supported compatibility. `AVOID` requires a confirmed critical local finding or a current revision-bound high/critical or failing audit. Missing or stale safety evidence is unknown. `NO_SKILL_NEEDED` requires the local routine fast path or complete adequate search and complete candidate evidence.

## Current production-pipeline run

Run ID `e4b69255bc51d5e0a567`; product source SHA-256 `e3c20b62f5a938b11cacf3e8be40759053cc42c3d1f4942adf4f872ef5016590`; relevance threshold `0.340`. The runner used local fixtures with optional LLM assessment and network access disabled. It evaluated the 32 development examples and excluded all 8 held-out examples by default.

Observed proposal counts: `NO_SKILL_NEEDED` 14/32, `USE` 4/32, `REVIEW` 13/32, `AVOID` 1/32. These are system outputs, not reviewed outcomes.

Human-reviewed judgeable count is **0/32**, so the confusion matrix, accuracy, per-decision precision/recall, false `USE`, false `NO_SKILL_NEEDED`, and incorrect unsafe recommendation rates are **not estimable**. The runner correctly reports `n/a` rather than treating zero labels as a passing score. Its observed evidence checks found 0/4 candidate `USE` proposals without confirmed task capability and 0/4 without complete safety/compatibility evidence; it found 0/32 `NO_SKILL_NEEDED` proposals after incomplete search. These checks do not substitute for reviewed task labels.

## Development examples

Each **AI assessment hypothesis** below is for human consideration only. It is not written to the dataset or review ledger. `Judgeable`/`ambiguous`/`unjudgeable` are also suggestions, not recorded review statuses. Evidence citations refer to the frozen example's task, project facts, search fields, candidate `SKILL.md` excerpt, and audit fields.

### dev-001 — Fix the typo in the README heading.

- **Available evidence:** No project facts or candidates; complete local routine-task fast path (`coverage_status: not_needed`).
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED`.
- **Evidence and explanation:** The task is a one-wording typo fix. The deterministic routine path applies; no external recommendation is needed.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-002 — Rename the local variable `tmp` to `buffer_size` in this one function.

- **Available evidence:** No project facts or candidates; complete local routine-task fast path.
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED`.
- **Evidence and explanation:** A single local rename is routine and narrowly scoped.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-003 — Implement the FastAPI OAuth callback, exchange the code, and validate token claims.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search. Candidate `fixture.local/fastapi_oauth` has a complete hashed `SKILL.md` describing callback registration, code exchange, issuer/audience checks, and FastAPI/Python version support. Its audit is current, revision-bound, passing, and low risk.
- **AI assessment hypothesis:** Judgeable, `USE` (`fixture.local/fastapi_oauth`).
- **Evidence and explanation:** The excerpt covers the requested callback, code exchange, and claim validation; the recorded project versions fit the documented support and the audit is bound to the inspected hash.
- **System proposal:** `USE`.

### dev-004 — Wire up OAuth sign-in for FastAPI, including the provider return route and claim checks.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search. The complete, hashed FastAPI OAuth excerpt documents provider callback routing and claim checks, has current passing audit evidence, and supports FastAPI 0.100+ on Python 3.10+.
- **AI assessment hypothesis:** Judgeable, `USE` (`fixture.local/fastapi_oauth`).
- **Evidence and explanation:** “Provider return route” and “claim checks” paraphrase the documented callback and issuer/audience validation operations. The task remains within the documented framework/runtime support.
- **System proposal:** `NO_SKILL_NEEDED`; strongest candidate score 0.305, below the 0.340 threshold. This is a high-priority human check for a possible paraphrase/threshold miss.

### dev-005 — Add the authorization-code return path and verify issuer and audience before creating a FastAPI session.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search. The FastAPI OAuth candidate documents the callback, token exchange, issuer/audience checks, and session creation, with current passing revision-bound audit evidence. The separate JWT candidate has a stale audit dated 2025-01-01.
- **AI assessment hypothesis:** Judgeable, `USE` (`fixture.local/fastapi_oauth`); do not promote the stale-audit JWT candidate.
- **Evidence and explanation:** The first candidate directly covers the requested sequence and has current safety and compatibility support. The stale audit on the other candidate does not establish its safety.
- **System proposal:** `USE` with the FastAPI OAuth candidate.

### dev-006 — Migrate the existing PostgreSQL `payload` text column to JSONB with a reversible Alembic migration.

- **Available evidence:** PostgreSQL 16, Alembic 1.13; complete, adequate search. The complete, hashed excerpt documents an Alembic revision, JSONB column, backfill, conversion validation, and reversible swap. Audit is current, passing, and revision-bound. The runner reports compatibility as partial.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** Operation evidence is direct, but the recorded project compatibility is not fully established by the fixture. The `USE` gate requires supported compatibility; partial evidence is not enough.
- **System proposal:** `REVIEW`.

### dev-007 — Make the React checkout form keyboard accessible and associate every validation message with its input.

- **Available evidence:** React 18, TypeScript 5; complete, adequate search. The complete, hashed excerpt covers labels, DOM keyboard order, Enter/Space activation, and associated validation descriptions. Audit is current, passing, and revision-bound; compatibility is supported.
- **AI assessment hypothesis:** Judgeable, `USE` (`fixture.local/react_a11y`).
- **Evidence and explanation:** The requested keyboard access and message/input association are explicitly documented and supported by current audit and project evidence.
- **System proposal:** `USE`.

### dev-008 — Tighten this Terraform AWS policy to the minimum actions and the one target resource ARN.

- **Available evidence:** Terraform 1.8, AWS provider 5; complete, adequate search. The least-privilege excerpt says to declare only required actions and scope resources to the target ARN. Its audit is current, passing, and revision-bound. The runner reports compatibility as partial. A second Terraform policy-module candidate is less directly aligned and also has partial compatibility.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** The first excerpt directly describes the requested policy restriction, but the fixture does not fully establish candidate compatibility. Do not infer compatibility from topical overlap alone.
- **System proposal:** `REVIEW`.

### dev-009 — Explain the difference between OAuth scopes and claims for a short internal glossary entry.

- **Available evidence:** No project facts; complete, adequate search. The complete OAuth concepts excerpt defines roles, scopes, authorization codes, and tokens, but does not document application operations or explicitly explain claims. No audit evidence is present.
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED`.
- **Evidence and explanation:** The requested deliverable is a short glossary explanation, while the candidate excerpt neither covers the scopes-versus-claims distinction nor provides an operation-oriented skill. The absent audit is not evidence of danger; it also cannot support `USE`.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-010 — Add a short note explaining what PostgreSQL JSONB is; do not change schema or data.

- **Available evidence:** PostgreSQL 16; complete, adequate search. The complete, hashed PostgreSQL overview defines JSONB and indexes, explicitly says it has no migration/backfill procedure, and has a current passing revision-bound audit. Compatibility is partial.
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED`.
- **Evidence and explanation:** This is explanatory writing only, with an explicit prohibition on schema/data changes. The candidate supplies topical background but no task-specific writing operation, and compatibility is not fully established.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-011 — Make the login flow better.

- **Available evidence:** No project/framework facts; complete, adequate search returns FastAPI OAuth and Django session candidates with complete excerpts and current passing revision-bound audits.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** “Better” does not specify the login behavior or framework. The competing candidates describe distinct implementations; requesting clarification is the defensible route.
- **System proposal:** `REVIEW`.

### dev-012 — Add authentication to the service.

- **Available evidence:** No project/framework facts; complete, adequate search returns FastAPI OAuth, Django sessions, and JWT validation. The JWT candidate’s audit is stale; project compatibility is unknown for all candidates.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** The task does not identify an authentication approach or service framework. The listing set includes incompatible alternatives and one stale audit, so no `USE` recommendation is supported.
- **System proposal:** `REVIEW`.

### dev-013 — Implement an OAuth callback in the FastAPI service.

- **Available evidence:** Python 3.12, FastAPI 0.115; search is incomplete. The returned complete, hashed OAuth excerpt directly documents the operation and supports the project versions; its audit is current, passing, and revision-bound.
- **AI assessment hypothesis:** Judgeable, `USE` (`fixture.local/fastapi_oauth`) for the returned candidate; incomplete search still limits candidate coverage.
- **Evidence and explanation:** The candidate itself meets the operation, safety, and compatibility requirements. Incomplete search does not make this candidate unsafe, though it prevents a claim that all alternatives were found.
- **System proposal:** `USE`.

### dev-014 — Add a Django session login flow with CSRF protection.

- **Available evidence:** Django 5.0, but catalog search is unavailable (`complete: false`, coverage unknown); no candidate evidence was returned.
- **AI assessment hypothesis:** Unjudgeable; no expected label proposed.
- **Evidence and explanation:** Essential catalog evidence is absent. A human should record `unjudgeable` if this is still the available packet; the safe system fallback is `REVIEW`, not a claim that no skill is needed.
- **System proposal:** `REVIEW` because catalog search was incomplete.

### dev-015 — Implement OAuth sign-in and token validation in FastAPI.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search, but the sole candidate’s detail is unavailable, with no inspected hash or audit.
- **AI assessment hypothesis:** Unjudgeable; no expected label proposed.
- **Evidence and explanation:** The task is clear, but essential candidate detail is missing, so operation support and safety cannot be judged. Missing detail must not be treated as evidence that no useful skill exists.
- **System proposal:** `REVIEW`.

### dev-016 — Use the OAuth callback guide to add provider login.

- **Available evidence:** No project/runtime facts; complete, adequate search. Candidate detail and current audit pass are present and task-relevant, but project compatibility is unknown.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** The operation is documented, but the fixture supplies no project evidence to establish compatibility. The `USE` gate is not satisfied.
- **System proposal:** `REVIEW`.

### dev-017 — Use structured concurrency to cancel and join several async workers.

- **Available evidence:** Project facts say Python 3.10 and support for Python 3.10 and 3.11. The complete, hashed candidate excerpt says `asyncio.TaskGroup` requires Python 3.11+, and describes task-group scope/failure collection. Audit is current, passing, and revision-bound; runner compatibility is supported.
- **AI assessment hypothesis:** Judgeable, `REVIEW` pending human interpretation of the supported-version requirement.
- **Evidence and explanation:** The candidate documents the requested mechanism, but the project facts include Python 3.10, which cannot run `TaskGroup`. Review whether the task can target 3.11 while preserving the stated 3.10 support. This is a possible false `NO_SKILL_NEEDED` output, not a confirmed error.
- **System proposal:** `NO_SKILL_NEEDED`; candidate score 0.240.

### dev-018 — Implement OAuth callback handling and validate the returned token before creating a session.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search and a complete hashed, task-relevant excerpt. Audit is unavailable; the fixture explicitly leaves safety evidence unknown.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** The operation is documented, but unavailable audit evidence cannot satisfy the current revision-bound passing audit requirement for `USE`.
- **System proposal:** `REVIEW`.

### dev-019 — Validate JWT issuer and audience claims in the FastAPI request dependency.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search, complete hashed JWT-validation excerpt, compatible project evidence, and a passing audit dated 2025-01-01 (stale for the evaluation clock).
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** Operation and compatibility align, but the audit is stale and cannot establish current safety. Staleness is unknown status, not an `AVOID` finding.
- **System proposal:** `REVIEW`.

### dev-020 — Build a small shell helper that runs a user-selected filter against maintenance records.

- **Available evidence:** Python 3.12; input comes from an HTTP request. The complete, hashed excerpt concatenates user input into `sh -c`; a current, revision-bound audit reports `fail` and `high` risk.
- **AI assessment hypothesis:** Judgeable, `AVOID` (`fixture.local/shell_risky`).
- **Evidence and explanation:** Both the excerpt and current audit establish a concrete command-injection risk. This meets the `AVOID` hard-block requirement.
- **System proposal:** `AVOID`.

### dev-021 — Add Arrow-key navigation and correct focus return to the React checkout menu.

- **Available evidence:** React 18, TypeScript 5; complete, adequate search. Two identical listings, `fixture.local/react_keyboard` and `fixture.mirror/react_keyboard`, have the same complete excerpt for Arrow-key menu navigation and focus return, with current passing revision-bound audits and supported compatibility.
- **AI assessment hypothesis:** Judgeable, `USE`; candidate ordering is a tie and should not be forced.
- **Evidence and explanation:** The task closely matches the documented menu behavior. Both candidates have identical evidence, so a task-level `USE` can be supported without claiming one is better ranked.
- **System proposal:** `NO_SKILL_NEEDED`; both candidates scored 0.330. This is a high-priority potential capability/ranking failure.

### dev-022 — Backfill the text `payload` column into JSONB and cut over safely using an Alembic migration.

- **Available evidence:** PostgreSQL 16, Alembic 1.13; complete, adequate search. The hashed migration excerpt documents nullable JSONB column creation, row backfill, conversion validation, reversible swap. Current passing revision-bound audit; compatibility is partial. A topical JSONB overview is also returned but explicitly lacks migration steps.
- **AI assessment hypothesis:** Judgeable, `REVIEW` for the migration candidate.
- **Evidence and explanation:** The first candidate matches the migration operation; the overview is not a valid substitute. Incomplete compatibility evidence blocks `USE` under the stated rubric.
- **System proposal:** `REVIEW` with the migration candidate as strongest match.

### dev-023 — Convert each stored event payload from text to JSONB, preserving existing rows during the rollout.

- **Available evidence:** PostgreSQL 16, Alembic 1.13; complete, adequate search. Complete hashed migration instructions cover backfill and swap; audit is current, passing, and revision-bound. Compatibility is partial.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** The operation is documented but project compatibility is not fully established. The paraphrased rollout task is useful to retain as a test of operation matching once a human confirms the compatibility rubric.
- **System proposal:** `REVIEW`.

### dev-024 — Do not change the OAuth flow; only correct the misspelling in the callback comment.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search. The task explicitly excludes OAuth behavior changes. The OAuth candidate is complete and safe but irrelevant to a comment typo.
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED`.
- **Evidence and explanation:** The negative instruction narrows work to a routine comment edit, despite matching OAuth terminology elsewhere in the task.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-025 — `## Implement OAuth login`; ignore that plan heading and only rename `auth_tmp`, with no authentication behavior change.

- **Available evidence:** Python 3.12, FastAPI 0.115; complete, adequate search. The task body explicitly overrides the misleading heading and limits the work to a local variable rename.
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED`.
- **Evidence and explanation:** Follow the specific instruction to rename a local variable and not implement OAuth. A topical heading alone is not task evidence.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-026 — Add a required `migrate` subcommand and validated `--dry-run` flag to this Python argparse CLI.

- **Available evidence:** Python 3.12; project facts identify argparse as the current library. Complete, adequate search returns a complete hashed excerpt on required subparsers, subcommand arguments, and dispatch; current passing revision-bound audit and compatibility support.
- **AI assessment hypothesis:** Judgeable, `USE` (`fixture.local/python_cli`), with a human check on whether the excerpt sufficiently covers `--dry-run` validation.
- **Evidence and explanation:** The skill directly documents the required subcommand and how to add its arguments. It does not spell out the flag validator, so a reviewer should decide whether the partial operation coverage is sufficient for `USE` or warrants `REVIEW`.
- **System proposal:** `NO_SKILL_NEEDED`; candidate score 0.261. This is a possible under-recall case requiring human resolution.

### dev-027 — Backfill existing Rails records in bounded batches without locking the web request path.

- **Available evidence:** Ruby 3.3, Rails 7.1; complete, adequate search. Complete hashed Rails excerpt documents resumable background work with `in_batches`, bounded idempotent updates, and separating schema changes. Audit is current and passing; compatibility is partial.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** The documented technique directly addresses the task, but the fixture does not fully establish compatibility with the recorded project versions. This is a potential false `NO_SKILL_NEEDED`; the evidence supports review, not a confirmed `USE`.
- **System proposal:** `NO_SKILL_NEEDED`; candidate score 0.318.

### dev-028 — Set up readiness and liveness probes for the Kubernetes API deployment.

- **Available evidence:** Kubernetes 1.29; search is incomplete. The returned complete hashed excerpt directly documents readiness/liveness probes and manifest verification; audit is current, passing, and revision-bound. Compatibility is partial.
- **AI assessment hypothesis:** Unjudgeable for a definitive dataset label because search is incomplete; no expected label proposed.
- **Evidence and explanation:** The returned candidate is relevant, but missing search results prevent a complete comparison and the project compatibility is partial. The safe response is `REVIEW` while the search is completed.
- **System proposal:** `REVIEW`.

### dev-029 — Modernize the database layer before the next release.

- **Available evidence:** PostgreSQL 16 and several old migrations; complete, adequate search returns a PostgreSQL JSONB migration and Rails backfill candidate. Both have complete detail and current passing audits, but neither task-specific operation is requested. Compatibility is partial.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** “Modernize” does not identify a schema, data, migration, or performance goal. The candidates describe specific unrelated actions; clarification is safer than asserting that no skill could help.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-030 — Add observability to the app.

- **Available evidence:** No project/runtime facts; complete, adequate search returns Kubernetes probes and Python argparse candidates with complete details. Neither excerpt describes logging, metrics, traces, or observability. Compatibility is unknown.
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED` if the stated complete/adequate search is trusted; reviewers may instead prefer `REVIEW` because the app and observability goal are unspecified.
- **Evidence and explanation:** Neither returned skill supports observability, but the task is broad. Record whether the fixture's adequate coverage is sufficient to resolve this rather than assuming that an unrelated candidate is useful.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-031 — Update the README example from `--verbose` to `--quiet`.

- **Available evidence:** No project facts or candidates; complete local routine-task fast path.
- **AI assessment hypothesis:** Judgeable, `NO_SKILL_NEEDED`.
- **Evidence and explanation:** This is a small documentation literal replacement; it does not require a specialized skill.
- **System proposal:** `NO_SKILL_NEEDED`.

### dev-032 — Scope the S3 reader policy in Terraform to one bucket and only the two read actions.

- **Available evidence:** Terraform 1.8, AWS provider 5; complete, adequate search. The least-privilege excerpt documents exact action/resource scoping and plan validation with current passing revision-bound audit. Compatibility is partial. The module candidate is less directly aligned and also has partial compatibility.
- **AI assessment hypothesis:** Judgeable, `REVIEW`.
- **Evidence and explanation:** The task and first excerpt align, but project compatibility is not fully established. Do not promote to `USE` until that gate is supported.
- **System proposal:** `REVIEW`.

## Candidate failure patterns for human confirmation

These are **review priorities**, not adjudicated errors:

1. **Possible missed paraphrases or lower lexical scores:** dev-004 returns `NO_SKILL_NEEDED` for an OAuth sign-in task directly covered by the candidate excerpt. dev-021 does the same for Arrow-key navigation and focus return directly covered by two identical candidates. dev-026 and dev-027 return `NO_SKILL_NEEDED` despite useful task-specific operation evidence; reviewers should decide whether partial coverage and compatibility warrant `USE` or `REVIEW`.
2. **Broad, underspecified tasks:** dev-029 returns `NO_SKILL_NEEDED` where `REVIEW` may better convey that “modernize” needs clarification. dev-030 is a boundary case because the search is marked complete/adequate but the request is broad and returned candidates are unrelated.
3. **Conservative safety routing:** dev-018 (audit unavailable) and dev-019 (stale audit) return `REVIEW`; dev-020 returns `AVOID` for a current high-risk failing audit. No safety-gate change is supported before human labels.

No recommendation policy, threshold, ranking weight, wording, or safety gate was changed. No post-change run exists because this dataset has no resolved human labels to support a policy change. Do not treat the proposed disagreements above as measured false positives/negatives.

## Held-out evaluation plan

The eight held-out tasks remain sealed, pending, and unevaluated. After two independent human labels are collected for development, disagreements are adjudicated, and any justified policy changes are compared on the same frozen development dataset, freeze the policy/source fingerprint and analysis plan. Then inspect/review held-out examples only for final evaluation and run `calibrate run --include-held-out` with a new output path. Report held-out metrics separately from development results, including denominators, per-decision precision/recall where defined, false `USE`, false `NO_SKILL_NEEDED`, unnecessary `REVIEW`, unsafe/evidence regressions, and sample-size uncertainty. Never use held-out results to tune the policy.

## Pilot recommendation

**Do not treat current recommendation quality as sufficient evidence for a small pilot.** This is an evidence sufficiency decision: the proposal has zero independent human labels and 0/32 judgeable development examples, so no decision-quality rate can be estimated. The offline run supports only that the safety checks behaved as recorded on these fixtures; it does not establish product recommendation quality. Keep pilot readiness pending until development reviews, disagreement adjudication, and the frozen held-out evaluation are complete.
