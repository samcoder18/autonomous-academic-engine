# OpenRouter Academic-Source-Acquirer Qualification Failure Evidence

## Scope

This is the sanitized record of one bounded `--no-search` live qualification
attempt for the article-lane `academic-source-acquirer` role. It records a
fail-closed result only; it does not qualify the role, change production route
policy, switch the default executor, or assert any submission readiness.

## Controlled Qualification Result

- Workflow ID: `openrouter-live-smoke-article-qualify-source-acquirer-20260714-124248-97ab8ad7`
- Role: `academic-source-acquirer`
- Lane: `article`
- Execution mode: `write-plan`
- Controlled smoke: FAIL
- Route policy: FAIL
- Qualification controls: FAIL
- Secret scan: PASS

The one live attempt failed closed before write-plan application. The canonical
qualification fixtures remained unchanged and promotion was skipped.

## Safe Root Diagnosis

The provider role-result identity, checkpoint evidence, and artifact evidence
were valid. Its optional structured verdict, however, failed strict verdict
version validation with `version-mismatch`, yielding
`role-result-role-contract-invalid`.

## Remediation and Boundary

The narrow remediation in `72d7def3` requires a null verdict in the
non-evaluator write-plan result phase and has offline verification. It does not
qualify this role or authorize a retry.

There was no policy, default-executor, or qualification-matrix change. No
automatic retry is permitted; a new separately authorized live attempt is
required after the remediation.
