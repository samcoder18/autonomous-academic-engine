# System + Project Master Backlog (2026-04-20)

> Historical note (2026-04-21): backlog items that mention `biometrics-vkr`
> describe the former active work before migration to `starter-work`. They are
> preserved as historical evidence, not current workspace instructions.

Companion remediation matrix for
[system-project-master-audit-2026-04-20.md](system-project-master-audit-2026-04-20.md).

Legend:

- `severity`: `critical`, `high`, `medium`, `low`
- `confidence`: `confirmed`, `strong-suspect`
- `blocking impact`: what claim or workflow this item blocks

## Must-Fix Before Release-Quality Claim

| Finding ID | Subsystem | Severity | Confidence | Evidence source | Suggested owner | Recommended next action | Blocking impact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `DOC-001` | docs / tests truth | `medium` | `confirmed` | `tests/README.md`, split test modules, runtime reliability backlog | docs owner | Rewrite `tests/README.md` around the current split regression architecture and current deterministic test entrypoints. | blocks honest docs-truth claim for test architecture |
| `DOC-002` | docs / CLI artifact truth | `medium` | `confirmed` | `README.md`, live `one-shot-dissertation` smoke output | docs owner | Update README report-path wording so dissertation runs mention `*-one-shot-dissertation-report.*` instead of the generic thesis stem. | blocks honest public artifact-path documentation |
| `BASELINE-001` | release process | `high` | `confirmed` | `git status --short`, `git diff --stat` | repo maintainer | Re-run final release-quality audit on a clean commit or tagged snapshot instead of a dirty in-flight tree. | blocks any strong release-quality claim for the repo |
| `STATE-001` | active work / standards | `high` | `confirmed` | `python3 -m telegram_console.work_cli work-status --json` | work owner | Resolve or consciously waive visible status blockers for `biometrics-vkr`: missing article raw bundle, thesis conflict review, absent advisories. | blocks project-level formal-compliance / readiness claim |

## High-Value Next Wave

| Finding ID | Subsystem | Severity | Confidence | Evidence source | Suggested owner | Recommended next action | Blocking impact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `CI-001` | CI / formatting | `medium` | `strong-suspect` | `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, recent format drift noted in final quality audit | repo maintainer | Add `ruff format --check telegram_console/ tests/` to CI to close the formatting blind spot. | does not block runtime correctness, but weakens CI completeness |
| `CLI-001` | CLI architecture | `medium` | `strong-suspect` | runtime reliability backlog | runtime maintainer | Split the remaining `work_cli.py` parser shell into smaller command groups only if the next wave touches multiple command families. | raises maintenance cost and review complexity |
| `PROJ-001` | project observability | `medium` | `confirmed` | active `work-status --json` output | work owner | Fill thesis/article advisory surfaces deliberately or document why they remain absent for the current phase. | blocks stronger project-state clarity |

## Safe Defer

| Finding ID | Subsystem | Severity | Confidence | Evidence source | Suggested owner | Recommended next action | Blocking impact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `OPS-001` | launchd / platform ops | `medium` | `strong-suspect` | runtime reliability backlog | runtime maintainer | Add a real macOS LaunchAgent-capable smoke runner when operational confidence becomes more important than deterministic portability. | does not block current local/CI confidence |
| `RUNTIME-001` | runtime JSON hardening | `medium` | `strong-suspect` | runtime reliability backlog | runtime maintainer | Introduce explicit schema validation for autonomous runtime JSON only if fallback normalization starts hiding malformed-state bugs. | does not block current status surfaces |
| `CAND-001` | dissertation contour | `low` | `strong-suspect` | candidate polish audit residual risks | thesis/dissertation owner | Run one pilot candidate dissertation bundle through the current contour and capture operator friction. | does not block framework readiness claim today |

## Watchlist / Monitor Only

| Finding ID | Subsystem | Severity | Confidence | Evidence source | Suggested owner | Recommended next action | Blocking impact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `OPS-002` | daemon containment | `low` | `strong-suspect` | engineering audit, runtime reliability audit | runtime maintainer | Keep intentional broad containment catches under review; do not narrow them without replacing the reliability guarantees they currently provide. | monitor-only unless failure diagnostics degrade |
| `STD-001` | standards lifecycle | `low` | `confirmed` | active work status blockers | work owner | Monitor raw article standards freshness and thesis conflict visibility as recurring operational signals, not one-off cleanup. | monitor-only unless export/submission claim is being made |
| `DOC-003` | audit sprawl | `low` | `confirmed` | multiple specialized audit docs in `meta/` | repo maintainer | Treat the master audit as the canonical final verdict and keep older audit docs as supporting history instead of parallel truths. | monitor-only; mainly governance risk |
