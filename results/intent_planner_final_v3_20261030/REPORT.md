# RAG planner final acceptance (30 held-out cases)

2026-10-30. Final semantic validation frozen before its first run; no prompt changes after
the run.

- cases: 30
- strict exact: 26/30 = 86.7%
- acceptable exact (required ⊆ predicted ⊆ required ∪ optional): 28/30 = **93.3%**
- optional predictions: 2 (H02/H06 product); unnecessary labels: 3
- required coverage: 30/30 = 100%
- multi-intent exact/coverage: 16/16 = 100%
- unclarified return false route: 0
- unnecessary clarification: 1
- clarification accuracy: 28/30 = 93.3%
- extra labels: 5

Rule baseline on the same cases:

- exact 17/30 = 56.7%
- coverage 21/30 = 70.0%
- multi-intent coverage 10/16 = 62.5%
- unclarified return false route 2

Cost: 30 planner calls, 32,459 prompt / 1,216 completion tokens, 31.7 s,
**0.023989 CNY** (usage estimate).

The set is author-created synthetic semantic data, not real customer traffic. Exact is not
an online accuracy estimate. Write false is a route-level metric; actual writes still go
through ReturnService draft/preview/confirmation/review.

Grading used deterministic set comparison; **no LLM-as-judge**. The planner was the
system under test, not the judge. Optional-aware rescore (no new model calls):
`rescore.json`.
