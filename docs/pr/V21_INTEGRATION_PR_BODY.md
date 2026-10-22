## Problem and behavior

V2 could route to an empty tool set and claim a write completed without an executed success receipt. This draft integrates V2.1 routing fallback and object/field-bound write verification, then preserves narrowly supported policy/product consultation separately from the verified operation summary. Permissions, explicit submission confirmation, transactions, idempotency, and all default experimental switches remain unchanged.

This is the combined tree, including the external adapter, frozen evaluation evidence and interruption recovery ancestry. It is wider than the original V2-only PR. No main merge or production deployment is requested.

## Evidence and limitations

- Full combined offline regression: 909 passed, 4 skipped. Three additional paid-entry proof tests pass; final commit CI must also pass. Ruff and secret checks are required on the combined commit.
- Real Gradio scripted acceptance: draft correction, refresh restoration, preview/confirmation, authenticated review/query, negative cancellation and aggregate output; new mixed answer preserves policy/product and restores from SQLite. Scripted page checks are not natural model success rates.
- Frozen 24-case consultation experiment: 0→17 fact-list completion, 0→14 frozen safety-contract passes (including expected rejection, not user task success), no increase in covered false-completion frames; 45→45 model calls, next context +582.25 characters. Complex orders/selective scopes remain unsupported; mixed cancellation authorization still conservatively rejects.
- Original official Retail experiment: 119/120 scored after authorized recovery, Official35/40, V1 36/40, V2 35/39 with one budget-stopped episode. Same39 V1/V2 rewards are identical, V2 input +2.49%, no valid tool narrowing or natural retry. Do not report a complete V2 pass^1 or new pass^3.
- Separately authorized real V2/V2.1 pairs: 8 tasks each, 4/8→3/8, 46 requests, conservative0.839852/1CNY. One policy preservation trigger, but omitted order drafts dominate. No claim of overall quality or cost improvement; visible-answer p50 is later with buffering.

[Stage acceptance](https://github.com/qingtian-4/ecommerce-agent/blob/feat/runtime-v2/docs/TAU_V21_STAGE_ACCEPTANCE_20261022.md) describes all failures, original traces, costs and preflight hardening. Final paid driver source is archived separately; no paid retry after review corrections.

## Review and rollback

Keep this PR draft: default activation and production readiness are No-Go. Review `routing.py`, `write_evidence.py`, `service_agent.py`, `chat_service.py`, `consultation.py`, session/progress persistence and the adapter boundary. The combined tree, not separate branch green checks, is the review subject.

No new migration beyond the original V2.1 tree. Disable the existing Router/Recovery/progress/write-receipt flags to revert experimental behavior, or revert the complete integration merge. Code rollback does not undo committed business records, reset evaluation budgets, or authorize replay of uncertain writes. Original benchmark reproduction must still use its frozen V1/V2 sources, not this integrated runtime.

No automatic main merge, visibility change, default activation, extra benchmark group, repeated paid trials, or ECom-Bench adapter is included.
