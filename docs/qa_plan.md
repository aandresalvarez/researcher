# QA Expansion Plan (PRD §17)

## Integration targets (I-001..011)
- I-001 Stream + refinement delta: use `tests/test_streaming_sse.py` as baseline; expand with CP-enabled runs and approval pause/resume cases.
- I-002 Memory/RAG recall: add multi-document ingest, assert hit@K by checking `pack_used`.
- I-004 Abstain with missing evidence: craft eval cases where RAG returns empty packs and ensure stop_reason is `abstain`.
- I-007 Deferred tool approval: simulate approval flow in tests/test_approvals.py and assert resume behaviour.

## Load & performance (L-001..004)
- Extend `scripts/load_smoke.py` to parameterise question sets; add CI smoke that asserts p95 < 6s.
- Introduce nightly job using the same script with higher concurrency.

## Security (S-001..S-003)
- Add explicit tests for WEB_FETCH denylist, TLS enforcement, and prompt-injection blocking.
- Verify SQL guard denies write attempts and unsupported tables.

## Acceptance set
- Populate `evals/cp_b1_extended.json` with analytics/governance cases.
- Capture expected outputs to build the 30–50 item gold set referenced in PRD §17.5.
