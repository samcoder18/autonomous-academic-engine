# OpenRouter Evidence Report

Workflow ID: 20260712-221805-915576-default-article-repair
Work ID: openrouter-live-smoke
OpenRouter model: deepseek/deepseek-v4-flash

Controlled smoke: PASS
Route policy: PASS
Secret scan: PASS

## Artifact Counts

- Workflow artifacts: 269
- Role artifacts: 27

## Route Table

| Role | Route | Executor | Status |
| --- | --- | --- | --- |
| academic-repair-orchestrator | default | codex-cli | succeeded |
| academic-source-verifier | verifier | openrouter | succeeded |
| academic-citation-checker | default | codex-cli | succeeded |
| academic-submission-evaluator | evaluator | openrouter | succeeded |
| academic-repair-orchestrator | default | codex-cli | succeeded |
| academic-source-verifier | verifier | openrouter | succeeded |
| academic-citation-checker | default | codex-cli | succeeded |
| academic-submission-evaluator | evaluator | openrouter | succeeded |
| academic-finalizer | default | codex-cli | succeeded |

## Findings

- No controlled-smoke, route-policy, or secret-scan failures detected.
