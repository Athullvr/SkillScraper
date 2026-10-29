# Held-out exclusion validation

- Dataset SHA-256: `bf306a0d41978821836d923ce5023553eba66ca819588385300dc2e0a185021e`. Split metadata counts: 32 development and 8 held-out (40 total).
- Each reviewer evidence/form packet contains exactly `dev-001` through `dev-032` (32 cases). A/B packet bodies are identical except for reviewer token.
- Each separate post-submission proposal appendix contains one SkillScraper output for each of the 32 development IDs. Both appendices are in `coordinator-only/` and must be withheld until both reviewers have submitted all 32 labels.
- No held-out ID, task text, candidate excerpt, or audit evidence was serialized into either packet or appendix. No `review show --include-held-out` call was used.
- The prior AI assessment content is kept in the coordinator-only directory and is absent from reviewer packets; the original reviewer-visible path contains only a notice to wait until both submissions are complete.
- Production run `e4b69255bc51d5e0a567` contains 32 development rows only and records `include_held_out: false`; it generated no held-out predictions or metrics. The current product source fingerprint matches `e3c20b62f5a938b11cacf3e8be40759053cc42c3d1f4942adf4f872ef5016590`.
- Review events at preparation: 0. Reviewer forms are blank.
- Boundary: the dataset was parsed to confirm schema and split counts; no held-out task/evidence was rendered, evaluated, or included in a packet.

## Reviewer artifact SHA-256

- `reviewer-a/packet.md`: `73bab7d875e083f4a1316132e316744ad91f1da17c1e96e7bc35482906a15422`
- `coordinator-only/reviewer-a-proposals-after-submission.md`: `ecd7effede7c1ff1bdf959309e347113848b14969f9cc86a4050b485525c584f`
- `reviewer-b/packet.md`: `41eb103e235d40ae87a889ffbb313ad5877e860326870578d928d5efc4bd28d3`
- `coordinator-only/reviewer-b-proposals-after-submission.md`: `ecd7effede7c1ff1bdf959309e347113848b14969f9cc86a4050b485525c584f`
- `completeness-checklist.md`: `c42698b4e30475eeb35938bb6f6d3ed14a4ad8339574b549ca382c934ede20fa`
