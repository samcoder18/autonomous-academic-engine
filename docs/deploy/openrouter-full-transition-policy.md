# OpenRouter Full-Transition Policy

## Status and Scope

This is the versioned policy for moving every supported article and thesis role
to OpenRouter. It does not change the current runtime route or default
executor. Until a role has a qualification record and the corresponding
runtime change has landed, `codex-cli` remains the default executor and an
unlisted OpenRouter role remains forbidden.

The current RC has only two article read-only baselines:

- `academic-source-verifier`;
- `academic-submission-evaluator`.

Both are recorded as PASS baselines in the [role qualification
matrix](openrouter-role-qualification.md), using the sanitized [2026-07-13
controlled live workflow smoke evidence](evidence/2026-07-13-openrouter-controlled-live-workflow-smoke.md).
That evidence does not authorize any other role, any thesis route, or an
OpenRouter default.

## Non-Negotiable Authority Boundaries

- `WorkflowEngine` remains the sole authority for sandbox creation, allowed
  write scopes, manifests and hashes, `role-result/v1` validation, gates,
  repair limits, readiness, and atomic promotion.
- OpenRouter is transport for a role response. It receives only the
  engine-authored provider context; it has no filesystem, shell, Git,
  promotion, gate, or secret-store access.
- Provider output remains fail-closed. The engine must not normalize malformed
  provider output or silently substitute `codex-cli`.
- All provider-originated changes stay inside existing allowed write scopes.
  Deletion, rename, symlink, path traversal, stale base hashes, duplicate
  paths, and oversized payloads are rejected before a canonical change is
  possible.
- The policy is an explicit per-role allowlist. It is never an implicit
  fallback rule or a second workflow control plane.

## Execution Modes

| Mode | Provider may do | Engine must do |
| --- | --- | --- |
| `read-only` | Return one strict, fenced `role-result/v1` from the supplied immutable context. | Build the context and evidence envelope; validate identity, verdict, hashes, checkpoints, gates, and the result contract. No provider-originated workspace write is accepted. |
| `write-plan` | Return a fenced `provider-write-plan/v1` containing only full-file replacements, then return a strict manifest-backed `role-result/v1`. | Validate every operation; apply only validated replacements inside the role sandbox; build the post-write manifest; run the existing conflict, gate, and promotion checks. |

`write-plan` is a target protocol, not a current capability. It must be
implemented and qualified before any write-capable role can use OpenRouter.

## Qualification Policy

A role can move from `not-qualified` to `qualified` only after all of the
following are recorded in
[openrouter-role-qualification.md](openrouter-role-qualification.md):

1. The role has an explicit execution mode, approved model, and rollback
   action.
2. Offline tests prove strict identity and schema handling. For a
   `write-plan` role, they also prove write-scope, base-hash, duplicate-path,
   and failure-without-promotion behavior.
3. One dedicated non-submission live workflow provides sanitized evidence for
   the route, strict contract, allowed write scope where applicable, gates,
   and secret scan.
4. The record links that evidence and names the exact role policy that was
   enabled.
5. A failure removes or blocks the role from the allowlist; it does not lower
   the `role-result/v1` contract.

The write-plan qualification order is intentionally sorted by promotion risk,
from bounded handoff artifacts through review artifacts and canonical drafts
to finalization. The exact order is the matrix's `Write risk order` column.

## Production Rollback

For a qualified production role, rollback is a policy operation:

1. Remove the affected `role_id` from the OpenRouter policy allowlist.
2. Record the reason, model, evidence reference, and time of removal in the
   qualification record or production evidence.
3. Make the next OpenRouter selection for that role fail closed with
   `provider-route-forbidden` before invoking any executor.
4. Preserve the failed result and existing canonical files for diagnosis; do
   not promote a partial provider result.

Rollback must never automatically reroute the affected role to `codex-cli`.
An operator may choose a separate, explicit recovery workflow only under its
own reviewed policy; it is not a hidden consequence of OpenRouter rollback.

The current RC's route-specific environment unsets are documented separately
in the [runbook](openrouter-runbook.md). They describe existing RC behavior,
not the full-transition production rollback contract above.

## Guarded Default-Switch Gate

`ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` stays forbidden until every gate
below passes and the resulting evidence is committed in sanitized form:

1. **100% role coverage:** every supported role in the article and thesis
   lanes is marked `qualified` in the role matrix, with an approved model,
   execution mode, live-evidence link, and rollback action.
2. **Complete lane workflows:** one full article workflow and one full thesis
   workflow pass with the guarded default enabled. Every role must have valid
   `role-result/v1` evidence; write-plan roles must also prove no unauthorized
   write and no promotion conflict.
3. **Secret safety:** a secret scan passes for each workflow's selected
   artifacts, logs, and committed evidence. No raw provider output or
   `output/runs/` directory enters Git.
4. **Cost and timeout controls:** configured request timeout, rate limit, and
   spend ceiling are enforced and the measured lane workflows remain within
   their approved limits.
5. **Rollback drill:** removing a qualified role from the policy is exercised
   in the production-like environment and the next selection fails closed with
   `provider-route-forbidden`, without an automatic Codex reroute.
6. **Authority preservation:** the evidence shows that `WorkflowEngine`, not
   the provider, controlled sandbox writes, validation, gates, repair limits,
   readiness, and promotion.

Passing an earlier role-level qualification or the current two-role RC baseline
is not a substitute for this gate.
