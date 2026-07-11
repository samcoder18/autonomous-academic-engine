# OpenRouter Evidence Report

Workflow ID: openrouter-direct-smoke-20260711-162021
Work ID: openrouter-live-smoke
OpenRouter model: deepseek/deepseek-v4-flash
Readiness: strong-draft-with-blockers

Scope note: direct WorkflowEngine smoke with real OpenRouter verifier/evaluator routes and a deterministic local default executor labeled `codex-cli`, used because the normal controlled workflow path hit the local Codex CLI usage limit. This does not authorize OpenRouter for default, writer, or finalizer routes.

Route policy: PASS
Secret scan: PASS

## Artifact Counts

- Workflow artifacts: 216
- Role artifacts: 15

## Route Table

| Role | Route | Executor | Status |
| --- | --- | --- | --- |
| academic-repair-orchestrator | default | codex-cli | succeeded |
| academic-source-verifier | verifier | openrouter | succeeded |
| academic-citation-checker | default | codex-cli | succeeded |
| academic-submission-evaluator | evaluator | openrouter | succeeded |
| academic-finalizer | default | codex-cli | succeeded |

## Findings

- No policy or secret-scan failures detected.
