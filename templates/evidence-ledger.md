# Evidence Ledger

## 1. Identity

- ledger_id:
- chapter_or_section:
- related_source_package:
- related_verification_log:
- date_opened:
- date_last_updated:
- verification_window:

## 2. Usage Rules

- This ledger is a claim-level handoff between `source package` and `draft`.
- Every strong factual or legal claim should appear here before it is treated as safe to draft as fact.
- Every strong claim should carry a claim passport with auditable primary-source verification.
- `analytical` claims may remain in the ledger, but must stay marked as analytical rather than verified fact.
- For every non-analytical claim, `pinpoint_locator` and `support_excerpt` are mandatory.
- If `statement_precision` is `qualified`, `support_scope` is `partial`, or the claim needs narrowing, `caveat_note` is mandatory.
- `draft_use = safe` is forbidden when `verification_status = needs-recheck` / `unsafe-for-draft` or when `support_scope` is not `direct`.

## 3. Claim Register

| claim_id | section_target | claim_text | basis_type | source_package_item_ids | primary_identifier | official_primary_link | jurisdiction | statement_precision | knowledge_date | verification_result | verification_status | support_scope | pinpoint_locator | support_excerpt | caveat_note | draft_use | false_attribution_check | period | territory | method | provider | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CL-001 | `thesis/manuscript/sections/...` | ... | `primary-normative` / `official-guidance` / `court-decision` / `empirical` / `secondary-doctrine` / `analytical` | `S1`, `S3` | ... | ... | `RU` / `EU` / `foreign` | `exact` / `qualified` / `context-only` | 2026-04-19 | `supported in official text` / `partial support only` / `not found in primary` | `verified` / `needs-recheck` / `analytical-conclusion` / `unsafe-for-draft` | `direct` / `partial` / `context-only` | `Art. 10 para. 2` / `Table 3` | concise support excerpt / holding summary | explicit limit or caveat | `safe` / `narrow` / `hold` | `passed` / `needs-review` | `2025` | `RU` | `official statistics` | `Rosstat` | ... |
| CL-002 | `thesis/manuscript/sections/...` | ... | `court-decision` | `S2` | ... | ... | `RU` | `qualified` | 2026-04-19 | `partial support only` | `needs-recheck` | `partial` | `p. 7, para. 3` | partial support only; narrow the holding | preserve limit in draft | `narrow` | `needs-review` |  |  |  |  | ... |

Legacy aliases for older ledgers that still remain readable by tooling:

- `claim_type` -> `basis_type`
- `primary_source_reference` -> `primary_identifier`
- `primary_verification_date` -> `knowledge_date`
- `support_locator` -> `pinpoint_locator`
- `holding_summary` -> `support_excerpt`
- `limit_note` -> `caveat_note`

## 4. Summary

- Claims safe for drafting:
- Claims that require re-check:
- Claims that must stay analytical:
- Claims blocked from drafting:
- Next verification or drafting step:
