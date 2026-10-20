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

Catalog calls use HTTPS, explicit timeouts, bounded retries, `Retry-After`, a 2 MB response limit, and bearer authentication when configured. The current skills.sh API reference documents bearer authentication for API calls and explicitly requires it for audits. Audit lookup is best-effort and unavailable audit data does not interrupt search. Project metadata is local and bounded; source files are never read. Search and optional LLM requests receive task text after heuristic redaction. The optional LLM is disabled in MCP and requires a separate opt-in for CLI task-fit assistance. Redaction can miss secrets, so inspect task text before using remote services. When enabled in the CLI, the LLM contributes 25% of task-fit ranking and cannot provide scanner results, compatibility facts, provenance, or security decisions.

## MCP boundary

The MCP adapter exposes read-only recommendation, inspection, and local diagnostic tools over stdio. It does not start a network listener, modify agent configuration, install skills, or execute retrieved content. MCP project paths are validated before metadata reads; symlink components in the requested root path are rejected, and the existing project scanner skips symlinked metadata files. Only allowlisted direct-root metadata is read.

MCP recommendation calls disable the optional LLM adapter even when its environment configuration is present. The task query still goes to the configured catalog after heuristic redaction, so that redaction is not a guarantee that arbitrary sensitive task text can never leave the machine. Project source files are not sent. A configured catalog token is used only in the catalog API bearer header and is not included in MCP results or normal error messages. Local absolute project paths and credential values are omitted from tool results.

`prepare_task` forms its evidence packet from the same in-memory `SkillDetail` object scanned by the shared workflow, then releases it after the candidate is processed. It does not fetch or scan a second copy. Excerpts are selected from the `files_inspected` paths only. The packet uses deterministic token overlap with the task, with a smaller preference for technology in project metadata. Each excerpt remains a verbatim substring of its retrieved file; the server does not clean up, paraphrase, or strip suspicious text. Section headings are provided only when parsed from an actual Markdown heading outside a fenced code block; otherwise the reference remains file-level. No line numbers are invented. The packet hash is the detail response's content hash, not a publisher signature. If no detail hash was supplied, the packet labels its revision binding as `unbound`.

The default packet budget is 1200 UTF-8 bytes of excerpt bodies, capped at 2400 bytes and three excerpts of at most 600 bytes each. Packet `used_bytes` counts only encoded excerpt strings, not the JSON metadata or wrapper. Per-file scanner findings and incomplete-coverage notes accompany excerpts as warnings. They may apply to the whole file rather than the exact passage. The packet is evidence from untrusted catalog content, never trusted instructions. Excerpt selection does not affect the candidate decision. A clean heuristic scan is not proof of safety, and a suspicious excerpt can still be returned so the warning and source text remain visible.

The documented skills.sh API credential is a short-lived Vercel OIDC bearer token. Vercel documents enabling OIDC Federation and using `vercel link` plus `vercel env pull` for local development. SkillScout reads `VERCEL_OIDC_TOKEN` (or the `SKILLSCOUT_CATALOG_TOKEN` bearer-token override) from its process environment at startup; it does not parse `.env.local` or refresh the token. Do not put credentials in tool inputs, chat, or checked-in agent configuration. No alternate catalog source has been validated; the configurable base URL still has to implement the skills.sh v1 API contract.

