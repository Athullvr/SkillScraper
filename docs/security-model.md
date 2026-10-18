# Security model

## Trust boundaries

Catalog search responses, skill descriptions, skill files, external audit results, and optional LLM responses are untrusted input. Pydantic validation rejects malformed required fields; catalog response bodies and inspected file counts/content sizes are bounded. Skill files are treated as text only. SkillScout never executes scripts, hooks, shell commands, candidate instructions, or install commands.

## Evidence and policy

Task relevance and security confidence are separate. The deterministic ranker measures task concept overlap and applies a small penalty to generic candidate names. It does not add install popularity or audit status to relevance.

`AVOID` is reserved for critical local findings in the retrieved text or a failing/high/critical external audit associated with the exact detail content hash. Stale or unbound audit failures are shown but do not claim to describe the current revision; they produce `REVIEW`. A content-hash change makes prior audit results ineligible for the `USE` gate. Revision-bound audits older than 90 days are treated as stale; this is a conservative initial policy threshold, not a validated standard.

`USE` currently requires all of the following: task relevance above the initial threshold, a complete `SKILL.md` file snapshot, a content hash from the detail response, framework compatibility supported by detected project metadata, no medium/high local findings, and all available external audit findings passing with an exact revision association and timestamps within 90 days. Missing audit results are unknown, not pass. This policy is intentionally strict and may produce many `REVIEW` results.

## Local checks

Text heuristics inspect for:

- Attempts to override agent instructions.
- Credential access near outbound transfer indicators.
- Obfuscated/encoded execution and direct `curl`/`wget` shell pipelines.
- Destructive system or device commands.
- Executable-looking files not referenced by `SKILL.md`.
- Package manager lifecycle hooks that may run during installation.

These checks are not a comprehensive security scanner. They can miss novel or indirect behavior and can produce false positives. They never establish that a skill is safe.

## Network and privacy

Catalog calls use HTTPS, explicit timeouts, bounded retries, `Retry-After`, a 2 MB response limit, and optional bearer authentication. Audit lookup is best-effort and unavailable audit data does not interrupt search. Project metadata is local and bounded; source files are never read. Search and optional LLM requests receive task text after heuristic redaction. The optional LLM is disabled without configuration and requires a separate opt-in before task text is sent. Redaction can miss secrets, so inspect task text before using remote services. When enabled, the LLM contributes 25% of task-fit ranking and cannot provide scanner results, compatibility facts, provenance, or security decisions.

