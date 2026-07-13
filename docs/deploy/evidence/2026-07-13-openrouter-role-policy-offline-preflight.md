# OpenRouter Role-Policy Offline Preflight

Date: 2026-07-13

## Scope

This is sanitized offline evidence for the role-policy implementation only. It
does not qualify a new provider role and does not change the current two-role
read-only RC allowlist.

## PASS — Offline Policy and Contract Checks

- `OPENROUTER_ROLE_POLICY` is an explicit allowlist with exactly
  `executor_id` and `execution_mode` for every enabled provider role.
- The current entries are only `academic-source-verifier` and
  `academic-submission-evaluator`, both in `read-only` mode.
- A per-role OpenRouter request for an unqualified role fails with
  `provider-route-forbidden` before an executor is invoked; it does not fall
  back to `codex-cli`.
- An invalid execution mode is rejected by the same fail-closed route path.
- The workflow trace persists `execution_mode`, a read-only verifier rejects a
  write plan even when the workflow contract otherwise has a writable scope,
  and an explicitly approved test write-plan role uses the sandbox two-call
  path.
- The evidence-report script consumes the expected role-policy matrix and
  checks executor, route, mode, status, and secret scan.

Verification command:

```text
python3 -m unittest tests.test_executors tests.test_workflow_engine \
  tests.test_openrouter_evidence_report tests.test_provider_write_contract \
  tests.test_role_result_contract -q
Ran 119 tests
OK
```

## BLOCKED — Live Qualification

The current shell had no nonempty `OPENROUTER_API_KEY` or
`ACADEMIC_ENGINE_OPENROUTER_MODEL`. Their values were not read or printed.
Consequently no provider smoke, no dedicated non-submission live workflow, and
no rollback drill was run. No article write-plan role was added to the runtime
allowlist or marked `qualified` in the matrix.

The next permitted step remains the serial gate in
[the qualification matrix](../openrouter-role-qualification.md): set the
secret and model outside Git, qualify one previously forbidden role through
offline checks plus a dedicated bounded live workflow, publish sanitized
evidence, exercise policy-removal rollback, and only then add that one role to
the policy.
