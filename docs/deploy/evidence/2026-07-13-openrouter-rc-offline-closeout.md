# OpenRouter RC Offline Closeout

Date: 2026-07-13

## Scope Decision

The constrained OpenRouter implementation is complete as offline engineering hardening. This closeout does not assert a successful live RC workflow.

OpenRouter remains allowed only for `academic-source-verifier` and `academic-submission-evaluator`. Default, repair, citation, writer, finalizer, and all thesis roles remain `codex-cli`. No fallback, default-provider change, safe-write bridge, or role-result normalization was added.

## Contract Hardening

Read-only provider context now includes `provider_result_evidence_envelope`. It supplies one machine-authored manifest path/SHA-256 pair and maps every required checkpoint to that path. The provider prompt requires copying this envelope exactly into `artifacts` and `checkpoint_evidence`.

`role-result/v1` validation remains strict and independently verifies the reported hashes. Historical blocked evidence reports remain unchanged.

## Offline Verification

- `python3 -m unittest discover -s tests -q`: 608 tests passed.
- Scoped Ruff check passed for the engine, evidence script, and related tests.
- `git diff --check` passed.
- No new live provider smoke or normal workflow run was made after this hardening change.

## Live Acceptance

Live RC acceptance is deliberately deferred. A future operator may run one controlled workflow under stable Codex capacity, but this closeout does not claim `Controlled smoke: PASS` or `Route policy: PASS`.
