# OpenRouter Role Qualification Matrix

## Purpose

This matrix is the versioned record for every supported role that may
eventually use OpenRouter. It is a policy and evidence index, not a runtime
route table. Until the runtime policy for a row is implemented and its
qualification status is `qualified`, selecting OpenRouter for that row must
remain forbidden.

`read-only` means the provider cannot originate a workspace write. `write-plan`
means the future provider protocol is limited to a validated
`provider-write-plan/v1` in a `WorkflowEngine` sandbox, followed by a strict,
manifest-backed `role-result/v1`. It never grants provider filesystem, shell,
Git, gate, promotion, or secret-store access.

## Status Vocabulary

- **RC baseline PASS**: current sanitized live evidence proves the existing
  narrow route. It is a qualification seed for the full transition, not an
  authorization for other roles or a default switch.
- **Not qualified**: the role has no approved OpenRouter route or model. Any
  attempted OpenRouter route remains fail-closed.
- **Qualified**: reserved for a later row that has all required synthetic,
  live, gate, secret-scan, and rollback evidence under the
  [full-transition policy](openrouter-full-transition-policy.md).

For each `not qualified` row, `not approved` means no model has been approved
for that role. It does not imply that the model used by a different row is
approved for it.

## Runtime Policy Enforcement

`academic_engine.executors.OPENROUTER_ROLE_POLICY` is the code-owned
allowlist used at selection time. Every entry has exactly an `executor_id` and
an `execution_mode`; the router persists that mode in the workflow role trace
and rejects an OpenRouter selection whose policy is absent or malformed with
`provider-route-forbidden` before invoking the provider.

For a later qualified role, an operator may select its explicit route with:

```bash
export ACADEMIC_ENGINE_ROLE_EXECUTOR_<ROLE_ID_UPPER_WITH_UNDERSCORES>=openrouter
```

For example, `academic-intake` would use
`ACADEMIC_ENGINE_ROLE_EXECUTOR_ACADEMIC_INTAKE`. That environment variable is
only a request to the router; it grants no capability. Until the exact role
has a `qualified` matrix row and a matching code policy entry, the request
fails closed with `provider-route-forbidden` and never falls back to
`codex-cli`.

As of this matrix revision, the runtime map still contains only the two
read-only RC baselines below. No write-plan row has been added, because their
dedicated live qualification and rollback evidence has not yet been recorded.

## Current Read-Only PASS Baselines

The [2026-07-13 controlled live workflow smoke](evidence/2026-07-13-openrouter-controlled-live-workflow-smoke.md)
records all of the following for the dedicated non-critical article work:

- `Controlled smoke: PASS`;
- `Route policy: PASS`;
- `Secret scan: PASS`;
- `academic-source-verifier` succeeded on `verifier/openrouter`;
- `academic-submission-evaluator` succeeded on `evaluator/openrouter`;
- model: `deepseek/deepseek-v4-flash`.

The two rows below preserve that exact RC baseline. The evidence does not
qualify thesis read-only roles, write-plan roles, or the default executor.

## Role Matrix

`Write risk order` is a global qualification sequence for `write-plan` roles:
`1` is the lowest promotion risk and `14` is the highest. A dash denotes a
read-only role. For every newly enabled role after the current two-role RC
baseline, this is a binding serial sequence: only one previously forbidden row
may be enabled for a bounded non-submission live-evidence workflow at a time.
Its offline checks, sanitized qualification record, and rollback exercise must
all pass before the next matrix entry is enabled.

| Write risk order | Role ID | Lane | Execution mode | Approved model | Live-evidence link | Rollback action | Migration status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| — | `academic-source-verifier` | article | `read-only` | `deepseek/deepseek-v4-flash` | [2026-07-13 controlled smoke](evidence/2026-07-13-openrouter-controlled-live-workflow-smoke.md) | Remove policy; fail closed; no automatic Codex reroute. | RC baseline PASS; current narrow route only. |
| — | `academic-submission-evaluator` | article | `read-only` | `deepseek/deepseek-v4-flash` | [2026-07-13 controlled smoke](evidence/2026-07-13-openrouter-controlled-live-workflow-smoke.md) | Remove policy; fail closed; no automatic Codex reroute. | RC baseline PASS; current narrow route only. |
| — | `thesis-source-verifier` | thesis | `read-only` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; current RC forbids thesis routing. |
| — | `thesis-submission-evaluator` | thesis | `read-only` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; current RC forbids thesis routing. |
| 1 | `academic-intake` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Qualification harness ready; Not qualified pending live PASS and rollback record; brief/publication-contract handoff. |
| 2 | `academic-source-acquirer` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; evidence-pack handoff. |
| 3 | `academic-evidence-cartographer` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; claim/coverage-map handoff. |
| 4 | `thesis-structure-architect` | thesis | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; chapter-contract handoff. |
| 5 | `thesis-research-synthesizer` | thesis | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; source/evidence-ledger handoff. |
| 6 | `academic-citation-checker` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; citation-review artifacts. |
| 7 | `academic-counterargument-critic` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; counterargument-review artifacts. |
| 8 | `thesis-citation-checker` | thesis | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; citation-review artifacts. |
| 9 | `thesis-argument-critic` | thesis | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; argument-review artifacts. |
| 10 | `academic-repair-orchestrator` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; bounded repair-plan influence. |
| 11 | `academic-draft-writer` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; canonical article-draft changes. |
| 12 | `thesis-draft-writer` | thesis | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; canonical thesis-section changes. |
| 13 | `thesis-style-editor` | thesis | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; canonical manuscript style changes. |
| 14 | `academic-finalizer` | article | `write-plan` | Not approved | — | Remove policy; fail closed; no automatic Codex reroute. | Not qualified; final bundle and promotion-facing artifacts. |

## Qualification Update Rules

For a newly enabled role after the current two-role RC baseline, update one
row at a time. Do not enable the next matrix entry until the current row has
passed all required evidence and its sanitized qualification record has been
updated.

Update that row only after all required evidence has passed:

1. Add the explicit model identifier and immutable live-evidence link.
2. Confirm the route selected the intended `role_id` and execution mode.
3. For `write-plan`, record synthetic tests for path scope, stale hash,
   duplicate paths, forbidden operation types, payload limit, a failed second
   provider call, and no unintended promotion.
4. Record the dedicated non-submission live workflow's strict
   `role-result/v1`, gates, write-scope result where applicable, and secret
   scan.
5. Exercise or update the exact policy-removal rollback action.
6. Change the row to `qualified` only when all items are present. Otherwise
   leave it `not qualified` and keep the runtime route fail-closed.

No row may be marked `qualified` merely because another role uses the same
model, because a provider smoke succeeds, or because a raw runtime artifact
exists outside the committed evidence directory.
