# OpenRouter Evidence Report

Workflow ID: 20260711-205638-247414-default-article-repair
Work ID: openrouter-live-smoke
OpenRouter model: deepseek/deepseek-v4-flash

Controlled smoke: FAIL
Route policy: FAIL
Secret scan: PASS

## Artifact Counts

- Workflow artifacts: 203
- Role artifacts: 2

## Route Table

| Role | Route | Executor | Status |
| --- | --- | --- | --- |
| academic-repair-orchestrator | default | codex-cli | failed |

## Findings

- Controlled smoke violation: workflow status is 'failed'; expected 'completed'
- Controlled smoke violation: workflow execution_status is 'failed'; expected 'succeeded'
- Route policy violation: required role academic-source-verifier did not run on verifier/openrouter
- Route policy violation: required role academic-submission-evaluator did not run on evaluator/openrouter
