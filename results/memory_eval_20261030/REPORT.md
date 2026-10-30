# Memory MVP evaluation (12 deterministic cases)

2026-10-30. Author-created synthetic scenarios, frozen before the run.

- cases: 12
- expected hit: 11/11 = 100%
- forbidden hit (PII/expired/cross-user): 0
- fact extraction failures: 0
- model calls: 0
- LLM-as-judge calls: 0
- cost: 0

Scoring is string hit + forbidden-substring check over the memory context; no model call
and no judge. This validates the MVP mechanics, not online recall.

Raw rows and context: `scorecard.json`. See `docs/MEMORY.md` for the schema and limits.
