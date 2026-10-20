# Security model

## Trust boundaries

Catalog search responses, skill descriptions, skill files, external audit results, and optional LLM responses are untrusted input. Pydantic validation rejects malformed required fields; catalog response bodies and inspected file counts/content sizes are bounded. Skill files are treated as text only. SkillScout never executes scripts, hooks, shell commands, candidate instructions, or install commands.

## Evidence and policy

Task relevance and security confidence are separate. The deterministic ranker measures task concept overlap and applies a small penalty to generic candidate names. It does not add install popularity or audit status to relevance.

`AVOID` is reserved for critical local findings in the retrieved text or a failing/high/critical external audit associated with the exact detail content hash. Stale or unbound audit failures are shown but do not claim to describe the current revision; they produce `REVIEW`. A content-hash change makes prior audit results ineligible for the `USE` gate. Revision-bound audits older than 90 days are treated as stale; this is a conservative initial policy threshold, not a validated standard.

`USE` currently requires all of the following: task relevance above the initial threshold, a complete `SKILL.md` file snapshot, a content hash from the detail response, framework compatibility supported by detected project metadata, no medium/high local findings, and all available external audit findings passing with an exact revision association and timestamps within 90 days. Missing audit results are unknown, not pass. This policy is intentionally strict and may produce many `REVIEW` results.

Blank content hashes or audit revisions are treated as missing. An empty `SKILL.md` is incomplete evidence, and a timezone-naive audit timestamp cannot establish freshness. A listing hash never substitutes for the hash on the detail response that was actually inspected.

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

## MCP boundary

The MCP adapter exposes read-only recommendation, inspection, and local diagnostic tools over stdio. It does not start a network listener, modify agent configuration, install skills, or execute retrieved content. MCP project paths are validated before metadata reads; symlink components in the requested root path are rejected, and the existing project scanner skips symlinked metadata files. Only allowlisted direct-root metadata is read.

MCP recommendation calls disable the optional LLM adapter even when its environment configuration is present. The task query still goes to the configured catalog after heuristic redaction, so that redaction is not a guarantee that arbitrary sensitive task text can never leave the machine. Project source files are not sent. A configured catalog token is used only as the catalog API bearer credential and is not included in MCP results. Local absolute project paths and credential values are omitted from tool results. Catalog skill and audit content is returned only as bounded evidence and remains untrusted; clients must not treat it as instructions or as a reason to bypass their own safety controls.

