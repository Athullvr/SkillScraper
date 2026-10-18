# Limitations

- The relevance weights and `0.34` threshold are initial hypotheses. A 20-task evaluation set is now documented, but live candidate outcomes and baseline comparisons have not been measured because catalog access was unavailable. The set is exploratory and too small to support general claims.
- Search results come from skills.sh; authentication, rate limits, unavailable results, and catalog changes can affect discovery. SkillScout does not crawl the full catalog.
- Only up to five candidates can be configured for detail inspection; the default is three. A relevant result outside that inspected set may be missed.
- Project context reads only direct-root allowlisted metadata. It cannot verify compatibility from source code, scripts, runtime environments, or transitive dependency behavior.
- Local security checks are heuristic. Obfuscated, indirect, or novel malicious behavior may be missed, and legitimate content may trigger warnings.
- `USE` requires a passing revision-bound audit younger than 90 days. Many skills have no audit, no content hash, or audit results without revision association; those cases remain `REVIEW`.
- The optional LLM can add task-fit perspective, but its language and score are model judgments. Deterministic policy and local/external security evidence remain authoritative.
- The install command is generated text only. SkillScout does not install, activate, cache, or execute a candidate.

