# FLAG-003: TransactionItemVariant Referential Integrity Deferred

## Risk
`TransactionItemVariant.group_name` and `option_name` are raw `CharField`s rather than foreign keys to `VariantOption`. If a variant is renamed or deleted, historical transaction data diverges silently — no database-level constraint prevents inconsistency.

## Current Behavior
- `group_name` and `option_name` are stored as plain strings at transaction time.
- No cascade, no constraint, no lookup back to `VariantOption`.
- Historical records are stable but may not match current variant names.

## Recommended Fix (Variants 2.0)
- Add a nullable `FK` to `VariantOption` on `TransactionItemVariant`.
- Backfill FK from existing string data where possible.
- Retain string fields for display fallback on orphaned records.
- Add `on_delete=PROTECT` or `SET_NULL` depending on desired behavior.

## Status
Deferred. No model changes in this iteration.
