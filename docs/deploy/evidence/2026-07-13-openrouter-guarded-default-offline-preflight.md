# OpenRouter Guarded-Default Offline Preflight

Date: 2026-07-13

## Result

**PASS — guard behavior; BLOCKED — qualification and live default workflow.**

The implementation can select `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter`
only when the selected model is configured and the runtime policy has valid
OpenRouter entries for every supported role. Tests exercise that positive
mechanism with a synthetic complete policy and verify that a missing model or
an incomplete policy returns `provider-route-forbidden` before any selected
executor is invoked.

## Current RC State

- Supported roles: 18.
- Current OpenRouter runtime-policy entries: 2 read-only article RC baselines.
- Current policy coverage: incomplete.
- Current default: `codex-cli`.
- Result for `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` in this RC:
  fail closed with `provider-route-forbidden`.

## Offline Verification

```text
python3 -m unittest tests.test_executors tests.test_workflow_engine \
  tests.test_openrouter_evidence_report tests.test_provider_write_contract \
  tests.test_role_result_contract -q
Ran 121 tests
OK
```

## Live Gate Not Run

No configured OpenRouter API key or model was available in the current shell.
No provider call, thesis workflow, article workflow, default switch, or
rollback drill was attempted. This record does not qualify any thesis role,
does not mark article write-plan roles qualified, and does not authorize a
production default change.
