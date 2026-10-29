# Independent development review packet

**Reviewer token:** `reviewer-b`  
**Dataset:** `skillscout-human-review-proposal-2026-10` v2.1.0-proposed  
**Dataset SHA-256:** `bf306a0d41978821836d923ce5023553eba66ca819588385300dc2e0a185021e`  
**Scope:** development split only; 32 cases.

Record your decision before receiving or opening the separate SkillScraper proposal appendix. Do not consult other review materials or another reviewer’s ledger before submitting all 32 cases. This packet contains task evidence and blank reviewer forms only.

For each case choose `judgeable`, `ambiguous`, or `unjudgeable`. Judgeable cases require one decision from `NO_SKILL_NEEDED`, `USE`, `REVIEW`, or `AVOID`, a concise rationale, and specific evidence references. Candidate order is optional when assessable. Ambiguous/unjudgeable cases omit decision and order and state why. `USE` needs task-specific operation evidence, complete detail, a current revision-bound passing audit, and supported compatibility. `AVOID` needs confirmed critical/failing/high-risk evidence. Missing or stale safety evidence is unknown. `NO_SKILL_NEEDED` needs the routine fast path or complete adequate search with complete evidence for returned candidates.

## dev-001 — no_skill_routine

**Task**

Fix the typo in the README heading.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `not_needed`; 0 candidate(s).
- No candidate listing returned.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-002 — no_skill_routine

**Task**

Rename the local variable `tmp` to `buffer_size` in this one function.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `not_needed`; 0 candidate(s).
- No candidate listing returned.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-003 — explicit_operation

**Task**

Implement the FastAPI OAuth callback, exchange the code, and validate token claims.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-004 — operation_paraphrase

**Task**

Wire up OAuth sign-in for FastAPI, including the provider return route and claim checks.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-005 — operation_paraphrase

**Task**

Add the authorization-code return path and verify issuer and audience before creating a FastAPI session.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/oauth_jwt` — JWT validation in FastAPI

- Listing: source `fixture.local`, slug `oauth_jwt`. Validate JWT claims in FastAPI request handlers.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `7bb117d81fe250ccc1be604103d21819a95d3abfaf0e0c965431cdeef4806216`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI JWT validation
>
> Add a dependency that parses the bearer token, checks the signature and expiry, then validates issuer and audience before returning claims to the route.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2025-01-01T00:00:00Z`; revision `7bb117d81fe250ccc1be604103d21819a95d3abfaf0e0c965431cdeef4806216`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-006 — explicit_operation

**Task**

Migrate the existing PostgreSQL `payload` text column to JSONB with a reversible Alembic migration.

**Project facts**

- PostgreSQL 16
- Alembic 1.13

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/postgres_jsonb` — PostgreSQL JSONB migration

- Listing: source `fixture.local`, slug `postgres_jsonb`. Convert a PostgreSQL text column to JSONB with an Alembic migration.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`.
- Inspected excerpt(s):

**SKILL.md**

> # PostgreSQL text-to-JSONB migration
>
> Create an Alembic revision. Add a nullable JSONB column, backfill each row by casting the existing text payload, validate conversion failures, then swap columns in a reversible migration.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-007 — explicit_operation

**Task**

Make the React checkout form keyboard accessible and associate every validation message with its input.

**Project facts**

- React 18
- TypeScript 5

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/react_a11y` — React keyboard and label accessibility

- Listing: source `fixture.local`, slug `react_a11y`. Implement keyboard-accessible React controls with programmatic labels.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `8be6647f87c7f3cca3ba9b4da6471e15ea0c3b6449905dbac6177257cf7a2c94`.
- Inspected excerpt(s):

**SKILL.md**

> # React form accessibility
>
> Associate every input with a visible label or aria-labelledby reference. Keep controls reachable in DOM order, support Enter and Space activation, and announce validation errors through an associated description.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `8be6647f87c7f3cca3ba9b4da6471e15ea0c3b6449905dbac6177257cf7a2c94`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-008 — explicit_operation

**Task**

Tighten this Terraform AWS policy to the minimum actions and the one target resource ARN.

**Project facts**

- Terraform 1.8
- AWS provider 5

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/terraform_iam` — Terraform least-privilege IAM

- Listing: source `fixture.local`, slug `terraform_iam`. Restrict AWS IAM policy actions and resources in Terraform.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `516e23a35ea5fca29b6afc3f530da1352e69c843aaf771108862bd0d8d7e6c48`.
- Inspected excerpt(s):

**SKILL.md**

> # Terraform IAM least privilege
>
> Declare only the required actions in the policy statement, scope resources to the target ARN, and add conditions for the required account and region. Validate the plan before applying.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `516e23a35ea5fca29b6afc3f530da1352e69c843aaf771108862bd0d8d7e6c48`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/terraform_module` — Terraform AWS policy module

- Listing: source `fixture.local`, slug `terraform_module`. Build scoped AWS policy documents with Terraform modules.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `79b5fbe14dac5908943f7f2f117a5fb69566e4465e2359541f26242e30803cf2`.
- Inspected excerpt(s):

**SKILL.md**

> # AWS policy module
>
> Use aws_iam_policy_document data to compose a statement, pass an explicit resource ARN, and expose only the required actions as module inputs.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `79b5fbe14dac5908943f7f2f117a5fb69566e4465e2359541f26242e30803cf2`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-009 — topical_without_operation

**Task**

Explain the difference between OAuth scopes and claims for a short internal glossary entry.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/oauth_topic` — OAuth concepts

- Listing: source `fixture.local`, slug `oauth_topic`. OAuth terminology and general authentication concepts.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `b0973dba05516cd653cac94b7f1b2d1093b222d131e7b290f058052bab07777e`.
- Inspected excerpt(s):

**SKILL.md**

> # OAuth concepts
>
> This note defines OAuth roles, scopes, authorization codes, and tokens. It does not document application routes, provider callbacks, code exchange, or token validation steps.

- Audit availability: `none`.
- No audit findings recorded.
- Audit notes: No audit evidence is present in this fixture.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-010 — topical_without_operation

**Task**

Add a short note explaining what PostgreSQL JSONB is; do not change schema or data.

**Project facts**

- PostgreSQL 16

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/postgres_topic` — PostgreSQL JSONB overview

- Listing: source `fixture.local`, slug `postgres_topic`. General PostgreSQL JSONB concepts and indexing terminology.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `64723900a37168dd522226895139f21724d4a77e52141163822092e3ba8a19c6`.
- Inspected excerpt(s):

**SKILL.md**

> # PostgreSQL JSONB overview
>
> JSONB stores parsed JSON values and supports containment operators and indexes. This overview does not provide migration steps or a backfill procedure.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `64723900a37168dd522226895139f21724d4a77e52141163822092e3ba8a19c6`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-011 — ambiguous_request

**Task**

Make the login flow better.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/django_sessions` — Django session authentication

- Listing: source `fixture.local`, slug `django_sessions`. Configure session authentication and CSRF checks in Django.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `3c94f61fac6a139c36f904f7eb02af86e51e15a0c4c5dd0c167b97b1c21deb06`.
- Inspected excerpt(s):

**SKILL.md**

> # Django session authentication
>
> Enable SessionMiddleware and AuthenticationMiddleware, protect state-changing views with CSRF middleware, and rotate the session key after login.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `3c94f61fac6a139c36f904f7eb02af86e51e15a0c4c5dd0c167b97b1c21deb06`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-012 — underspecified_request

**Task**

Add authentication to the service.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `adequate`; 3 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/django_sessions` — Django session authentication

- Listing: source `fixture.local`, slug `django_sessions`. Configure session authentication and CSRF checks in Django.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `3c94f61fac6a139c36f904f7eb02af86e51e15a0c4c5dd0c167b97b1c21deb06`.
- Inspected excerpt(s):

**SKILL.md**

> # Django session authentication
>
> Enable SessionMiddleware and AuthenticationMiddleware, protect state-changing views with CSRF middleware, and rotate the session key after login.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `3c94f61fac6a139c36f904f7eb02af86e51e15a0c4c5dd0c167b97b1c21deb06`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 3: `fixture.local/oauth_jwt` — JWT validation in FastAPI

- Listing: source `fixture.local`, slug `oauth_jwt`. Validate JWT claims in FastAPI request handlers.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `7bb117d81fe250ccc1be604103d21819a95d3abfaf0e0c965431cdeef4806216`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI JWT validation
>
> Add a dependency that parses the bearer token, checks the signature and expiry, then validates issuer and audience before returning claims to the route.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2025-01-01T00:00:00Z`; revision `7bb117d81fe250ccc1be604103d21819a95d3abfaf0e0c965431cdeef4806216`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-013 — incomplete_search

**Task**

Implement an OAuth callback in the FastAPI service.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `False`; coverage: `incomplete`; 1 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-014 — catalog_unavailable

**Task**

Add a Django session login flow with CSRF protection.

**Project facts**

- Django 5.0

**Search**

- Complete: `False`; coverage: `unknown`; 0 candidate(s).
- No candidate listing returned.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-015 — detail_unavailable

**Task**

Implement OAuth sign-in and token validation in FastAPI.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/detail_unavailable` — FastAPI OAuth helper

- Listing: source `fixture.local`, slug `detail_unavailable`. OAuth helper for FastAPI applications; detail is unavailable in this fixture.
- Detail: `unavailable`, scope `selected_excerpts`, content hash `none`.
- Inspected excerpt(s):

> (no inspected excerpt available)

- Audit availability: `unavailable`.
- No audit findings recorded.
- Audit notes: Audit lookup is unavailable in this fixture.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-016 — compatibility_uncertain

**Task**

Use the OAuth callback guide to add provider login.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-017 — compatibility_uncertain

**Task**

Use structured concurrency to cancel and join several async workers.

**Project facts**

- Python 3.10
- Project supports Python 3.10 and 3.11

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/python_versioned` — Python 3.11 task groups

- Listing: source `fixture.local`, slug `python_versioned`. Use asyncio.TaskGroup for structured concurrency on Python 3.11 or newer.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `80a8d88262fe4417d099169693d7db3275c05fc9d39b0d163306d558e64cdcf1`.
- Inspected excerpt(s):

**SKILL.md**

> # Python TaskGroup
>
> This procedure uses asyncio.TaskGroup, which requires Python 3.11 or newer. Create the task group with async with and await the parent scope to collect task failures.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `80a8d88262fe4417d099169693d7db3275c05fc9d39b0d163306d558e64cdcf1`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-018 — safety_unknown

**Task**

Implement OAuth callback handling and validate the returned token before creating a session.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `unavailable`.
- No audit findings recorded.
- Audit notes: No current audit evidence is available in this proposed fixture.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-019 — safety_stale_audit

**Task**

Validate JWT issuer and audience claims in the FastAPI request dependency.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/oauth_jwt` — JWT validation in FastAPI

- Listing: source `fixture.local`, slug `oauth_jwt`. Validate JWT claims in FastAPI request handlers.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `7bb117d81fe250ccc1be604103d21819a95d3abfaf0e0c965431cdeef4806216`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI JWT validation
>
> Add a dependency that parses the bearer token, checks the signature and expiry, then validates issuer and audience before returning claims to the route.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2025-01-01T00:00:00Z`; revision `7bb117d81fe250ccc1be604103d21819a95d3abfaf0e0c965431cdeef4806216`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-020 — safety_concern

**Task**

Build a small shell helper that runs a user-selected filter against maintenance records.

**Project facts**

- Python 3.12
- Input comes from an HTTP request

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/shell_risky` — Shell command construction

- Listing: source `fixture.local`, slug `shell_risky`. Build shell commands from request parameters.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `57cad362f85f839da6dc0ae9e0ebba7bc5322e9ed16061e07a4bc565ce615c50`.
- Inspected excerpt(s):

**SKILL.md**

> # Shell command helper
>
> Build the command string by concatenating the user-provided filter into `sh -c`, then run it in the maintenance worker. This synthetic example intentionally lacks input separation.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `fail`; risk `high`; audited `2026-10-23T00:00:00Z`; revision `57cad362f85f839da6dc0ae9e0ebba7bc5322e9ed16061e07a4bc565ce615c50`. Prepared synthetic fixture finding: unsafe string interpolation reaches a shell command.
- Audit notes: Synthetic review fixture only; no real skill or live audit is represented.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-021 — ranking_tie

**Task**

Add Arrow-key navigation and correct focus return to the React checkout menu.

**Project facts**

- React 18
- TypeScript 5

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/react_keyboard` — React keyboard interaction patterns

- Listing: source `fixture.local`, slug `react_keyboard`. Add keyboard navigation and focus management to React interfaces.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `b0ffd5027e9b4fab98b0397db11b55db0880db9284afd56e51ab2cde6bedff5d`.
- Inspected excerpt(s):

**SKILL.md**

> # React keyboard interaction
>
> When adding a custom menu, manage focus with Arrow keys, support Escape to close, return focus to the trigger, and preserve a visible focus indicator.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `b0ffd5027e9b4fab98b0397db11b55db0880db9284afd56e51ab2cde6bedff5d`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.mirror/react_keyboard` — React keyboard interaction patterns

- Listing: source `fixture.mirror`, slug `react_keyboard`. Add keyboard navigation and focus management to React interfaces.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `b0ffd5027e9b4fab98b0397db11b55db0880db9284afd56e51ab2cde6bedff5d`.
- Inspected excerpt(s):

**SKILL.md**

> # React keyboard interaction
>
> When adding a custom menu, manage focus with Arrow keys, support Escape to close, return focus to the trigger, and preserve a visible focus indicator.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `b0ffd5027e9b4fab98b0397db11b55db0880db9284afd56e51ab2cde6bedff5d`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-022 — ranking_tie

**Task**

Backfill the text `payload` column into JSONB and cut over safely using an Alembic migration.

**Project facts**

- PostgreSQL 16
- Alembic 1.13

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/postgres_jsonb` — PostgreSQL JSONB migration

- Listing: source `fixture.local`, slug `postgres_jsonb`. Convert a PostgreSQL text column to JSONB with an Alembic migration.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`.
- Inspected excerpt(s):

**SKILL.md**

> # PostgreSQL text-to-JSONB migration
>
> Create an Alembic revision. Add a nullable JSONB column, backfill each row by casting the existing text payload, validate conversion failures, then swap columns in a reversible migration.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/postgres_topic` — PostgreSQL JSONB overview

- Listing: source `fixture.local`, slug `postgres_topic`. General PostgreSQL JSONB concepts and indexing terminology.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `64723900a37168dd522226895139f21724d4a77e52141163822092e3ba8a19c6`.
- Inspected excerpt(s):

**SKILL.md**

> # PostgreSQL JSONB overview
>
> JSONB stores parsed JSON values and supports containment operators and indexes. This overview does not provide migration steps or a backfill procedure.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `64723900a37168dd522226895139f21724d4a77e52141163822092e3ba8a19c6`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-023 — operation_paraphrase

**Task**

Convert each stored event payload from text to JSONB, preserving existing rows during the rollout.

**Project facts**

- PostgreSQL 16
- Alembic 1.13

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/postgres_jsonb` — PostgreSQL JSONB migration

- Listing: source `fixture.local`, slug `postgres_jsonb`. Convert a PostgreSQL text column to JSONB with an Alembic migration.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`.
- Inspected excerpt(s):

**SKILL.md**

> # PostgreSQL text-to-JSONB migration
>
> Create an Alembic revision. Add a nullable JSONB column, backfill each row by casting the existing text payload, validate conversion failures, then swap columns in a reversible migration.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-024 — negative_instruction

**Task**

Do not change the OAuth flow; only correct the misspelling in the callback comment.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-025 — misleading_heading

**Task**

## Implement OAuth login
Ignore that plan heading. Only rename the local variable `auth_tmp`; do not add or change authentication behavior.

**Project facts**

- Python 3.12
- FastAPI 0.115

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/fastapi_oauth` — FastAPI OAuth callback

- Listing: source `fixture.local`, slug `fastapi_oauth`. Implement OAuth login callbacks and validate returned token claims in FastAPI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`.
- Inspected excerpt(s):

**SKILL.md**

> # FastAPI OAuth callback
>
> To implement login, register the authorization callback route, exchange the authorization code for tokens, validate issuer and audience claims, then create the application session. This guide supports FastAPI 0.100 and later on Python 3.10 or newer.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `60726d23805ac9eb8d0b8f683a063ea7f451fff72d3f2960f244314646b9d14a`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-026 — explicit_operation

**Task**

Add a required `migrate` subcommand and validated `--dry-run` flag to this Python argparse CLI.

**Project facts**

- Python 3.12
- argparse is the current CLI library

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/python_cli` — Python argparse subcommands

- Listing: source `fixture.local`, slug `python_cli`. Add a required subcommand and validated flags to an argparse CLI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `2e03f54828c20980e0ab73137ba7ea26ce8add189a33466e24e227be92bdc4a8`.
- Inspected excerpt(s):

**SKILL.md**

> # argparse subcommands
>
> Create subparsers with dest='command' and required=True. Add the subcommand's arguments to its parser and dispatch using the parsed command value.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `2e03f54828c20980e0ab73137ba7ea26ce8add189a33466e24e227be92bdc4a8`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-027 — explicit_operation

**Task**

Backfill existing Rails records in bounded batches without locking the web request path.

**Project facts**

- Ruby 3.3
- Rails 7.1

**Search**

- Complete: `True`; coverage: `adequate`; 1 candidate(s).

### Candidate 1: `fixture.local/rails_backfill` — Rails batched data backfill

- Listing: source `fixture.local`, slug `rails_backfill`. Backfill existing Rails records in bounded batches inside a migration workflow.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `b58c766cc6f57d96f8c3de065017901451a515f15d95b00ed8eeb48677dad2c6`.
- Inspected excerpt(s):

**SKILL.md**

> # Rails data backfill
>
> Use a resumable background task with in_batches, update rows in bounded groups, make each update idempotent, and keep schema changes separate from long-running data work.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `b58c766cc6f57d96f8c3de065017901451a515f15d95b00ed8eeb48677dad2c6`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-028 — incomplete_search

**Task**

Set up readiness and liveness probes for the Kubernetes API deployment.

**Project facts**

- Kubernetes 1.29

**Search**

- Complete: `False`; coverage: `incomplete`; 1 candidate(s).

### Candidate 1: `fixture.local/k8s_probe` — Kubernetes health probes

- Listing: source `fixture.local`, slug `k8s_probe`. Configure readiness and liveness probes for Kubernetes workloads.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `5af6d71dc350b5cfd060284d3a86ac8351c0ee49f3a8d23fb71afa77269af489`.
- Inspected excerpt(s):

**SKILL.md**

> # Kubernetes probes
>
> Set a readinessProbe for traffic admission and a livenessProbe for recovery. Choose paths and initial delays from the service startup behavior, then verify the rendered manifest.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `5af6d71dc350b5cfd060284d3a86ac8351c0ee49f3a8d23fb71afa77269af489`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-029 — ambiguous_request

**Task**

Modernize the database layer before the next release.

**Project facts**

- PostgreSQL 16
- Several old migrations exist

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/postgres_jsonb` — PostgreSQL JSONB migration

- Listing: source `fixture.local`, slug `postgres_jsonb`. Convert a PostgreSQL text column to JSONB with an Alembic migration.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`.
- Inspected excerpt(s):

**SKILL.md**

> # PostgreSQL text-to-JSONB migration
>
> Create an Alembic revision. Add a nullable JSONB column, backfill each row by casting the existing text payload, validate conversion failures, then swap columns in a reversible migration.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `95b518f45a570a235bd0bd694f5c78534691ec167421dd4ff169a4be894f0f93`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/rails_backfill` — Rails batched data backfill

- Listing: source `fixture.local`, slug `rails_backfill`. Backfill existing Rails records in bounded batches inside a migration workflow.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `b58c766cc6f57d96f8c3de065017901451a515f15d95b00ed8eeb48677dad2c6`.
- Inspected excerpt(s):

**SKILL.md**

> # Rails data backfill
>
> Use a resumable background task with in_batches, update rows in bounded groups, make each update idempotent, and keep schema changes separate from long-running data work.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `b58c766cc6f57d96f8c3de065017901451a515f15d95b00ed8eeb48677dad2c6`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-030 — underspecified_request

**Task**

Add observability to the app.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/k8s_probe` — Kubernetes health probes

- Listing: source `fixture.local`, slug `k8s_probe`. Configure readiness and liveness probes for Kubernetes workloads.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `5af6d71dc350b5cfd060284d3a86ac8351c0ee49f3a8d23fb71afa77269af489`.
- Inspected excerpt(s):

**SKILL.md**

> # Kubernetes probes
>
> Set a readinessProbe for traffic admission and a livenessProbe for recovery. Choose paths and initial delays from the service startup behavior, then verify the rendered manifest.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `5af6d71dc350b5cfd060284d3a86ac8351c0ee49f3a8d23fb71afa77269af489`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/python_cli` — Python argparse subcommands

- Listing: source `fixture.local`, slug `python_cli`. Add a required subcommand and validated flags to an argparse CLI.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `2e03f54828c20980e0ab73137ba7ea26ce8add189a33466e24e227be92bdc4a8`.
- Inspected excerpt(s):

**SKILL.md**

> # argparse subcommands
>
> Create subparsers with dest='command' and required=True. Add the subcommand's arguments to its parser and dispatch using the parsed command value.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `2e03f54828c20980e0ab73137ba7ea26ce8add189a33466e24e227be92bdc4a8`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-031 — no_skill_routine

**Task**

Update the README example from `--verbose` to `--quiet`.

**Project facts**

- None recorded.

**Search**

- Complete: `True`; coverage: `not_needed`; 0 candidate(s).
- No candidate listing returned.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---

## dev-032 — ranking_tie

**Task**

Scope the S3 reader policy in Terraform to one bucket and only the two read actions.

**Project facts**

- Terraform 1.8
- AWS provider 5

**Search**

- Complete: `True`; coverage: `adequate`; 2 candidate(s).

### Candidate 1: `fixture.local/terraform_iam` — Terraform least-privilege IAM

- Listing: source `fixture.local`, slug `terraform_iam`. Restrict AWS IAM policy actions and resources in Terraform.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `516e23a35ea5fca29b6afc3f530da1352e69c843aaf771108862bd0d8d7e6c48`.
- Inspected excerpt(s):

**SKILL.md**

> # Terraform IAM least privilege
>
> Declare only the required actions in the policy statement, scope resources to the target ARN, and add conditions for the required account and region. Validate the plan before applying.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `516e23a35ea5fca29b6afc3f530da1352e69c843aaf771108862bd0d8d7e6c48`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

### Candidate 2: `fixture.local/terraform_module` — Terraform AWS policy module

- Listing: source `fixture.local`, slug `terraform_module`. Build scoped AWS policy documents with Terraform modules.
- Detail: `complete`, scope `complete_synthetic_detail`, content hash `79b5fbe14dac5908943f7f2f117a5fb69566e4465e2359541f26242e30803cf2`.
- Inspected excerpt(s):

**SKILL.md**

> # AWS policy module
>
> Use aws_iam_policy_document data to compose a statement, pass an explicit resource ARN, and expose only the required actions as module inputs.

- Audit availability: `available`.
- Finding: provider `synthetic-fixture-audit`; status `pass`; risk `low`; audited `2026-10-23T00:00:00Z`; revision `79b5fbe14dac5908943f7f2f117a5fb69566e4465e2359541f26242e30803cf2`. Prepared synthetic fixture result; no live catalog audit was requested.
- Audit notes: Synthetic review fixture only; this is not a live audit record.

**Independent reviewer form**

- Status (`judgeable`, `ambiguous`, or `unjudgeable`):
- Decision (judgeable only):
- Candidate order, best first (if assessable):
- Rationale (judgeable only):
- Evidence references (specific task/project/search/file/audit facts):
- Reason (ambiguous or unjudgeable only):

---
