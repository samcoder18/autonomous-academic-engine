# OpenRouter Full-Transition Cutover Evidence

Date: 2026-07-13

## Decision

**Production cutover: NOT APPROVED.**

The repository now has the strict write-plan bridge, role-aware policy
selection, and guarded-default mechanism required to perform later
qualification safely. It does not yet have the provider configuration or live
evidence needed to authorize the full transition.

## Evidence Status

| Control | Status | Evidence / boundary |
| --- | --- | --- |
| Provider availability | BLOCKED | No configured production secret or model was available in this environment; no live call was made. |
| Route policy | PASS offline; BLOCKED live | Unit coverage proves unqualified and malformed OpenRouter selections fail with `provider-route-forbidden` and do not call Codex. Current policy has only the two read-only article RC entries. |
| Write-plan validation | PASS offline | Strict schema, scope, hash, size, symlink, duplicate, direct-write, and exact evidence-envelope tests pass. |
| Promotion behavior | PASS offline | Sandbox plans retain existing conflict/no-delete/gate/promotion checks; failed provider calls do not promote canonical files. |
| Rollback | PASS offline; BLOCKED production drill | Removing or omitting a policy entry rejects the next requested OpenRouter route before executor invocation. No production-like live drill was run. |
| Secret scan | PASS offline; BLOCKED production scan | Evidence-report tests scan exact and patterned leaks. No production logs or secret-store telemetry existed to scan. |
| Full article workflow with guarded default | BLOCKED | Requires complete qualified article policy and live model/key. |
| Full thesis workflow with guarded default | BLOCKED | Requires complete qualified thesis policy and live model/key. |

## Required Production Preconditions

1. Configure a secret-store entry, model identifier, timeout, rate limit,
   spend ceiling, and redacted telemetry outside the repository.
2. Perform the serial role qualification gate one role at a time: synthetic
   tests, bounded non-submission live workflow, sanitized evidence, and
   policy-removal rollback drill.
3. Record all 18 qualified roles in the matrix and run one full article plus
   one full thesis workflow with the guarded default.
4. Run a production secret scan against the selected artifacts and redacted
   logs; commit only this kind of sanitized evidence, never raw runs.

## Codex CLI Decision

`codex-cli` remains a manually selectable emergency executor during the
rollback period. It is not an automatic fallback from OpenRouter. Removal, if
desired, requires a separate reviewed decommission change after the production
rollback period and full-transition evidence are complete.
