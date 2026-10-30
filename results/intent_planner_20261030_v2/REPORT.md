# RAG planner development evaluation (170 + 30 challenge)

2026-10-30. 170 existing frozen tasks with expected routes derived from gold tool families;
30 author-created semantic challenge cases. This is development/coverage evidence, not a
fresh acceptance set.

Planner on 170:

- exact 72/170 = 42.4%
- coverage 169/170 = 99.4%
- multi-intent coverage 26/27 = 96.3%
- extra route labels 103
- unclarified return false route (new metric) not recomputed in this old scorecard; the raw
  rows are kept for audit

Planner on 30 challenge (before the final return guard):

- exact 24/30 = 80.0%
- coverage 27/30 = 90.0%
- multi-intent coverage 13/16 = 81.3%
- unclarified return false route 1

Cost: 200 calls, 0.174398 CNY.

The 170 expected labels are minimum required tool families; exact is depressed by extra
soft-hint labels. Coverage is the primary metric. See `docs/INTENT_PLANNER.md`.
