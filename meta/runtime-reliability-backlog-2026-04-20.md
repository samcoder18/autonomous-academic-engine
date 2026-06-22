# Runtime Reliability Backlog (2026-04-20)

Статусы в этом backlog:

- `must-fix this wave`
- `safe defer`
- `out-of-scope`

## Closed in this wave

| Status | Item | Resolution |
| --- | --- | --- |
| `must-fix this wave` | Shared runtime JSON persistence for state/lock/stop files | Closed via `academic_engine/autonomous_runtime_store.py` |
| `must-fix this wave` | JSON-first CLI error contract for autonomous/daemon/launchd | Closed via `academic_engine/work_cli_output.py` and `work_cli_autonomous.py` |
| `must-fix this wave` | Deterministic stop-request consumption in single/multi daemon | Closed in `autonomous_daemon.py` and `autonomous_scheduler.py` |
| `must-fix this wave` | Corrupted runtime JSON fallback coverage | Closed with new CLI/runtime tests |
| `must-fix this wave` | Split overloaded CLI/runtime tests out of `tests/test_academic_engine.py` | Closed via `test_work_cli_autonomous.py`, `test_work_cli_launchd.py`, `test_work_cli_runtime.py` |
| `must-fix this wave` | Further decompose `orchestrator.py` without breaking public imports | Closed via mixin extraction and compatibility re-exports in `academic_engine/orchestrator.py` |
| `must-fix this wave` | Introduce structured runtime error classes for scheduler candidate isolation | Closed via `academic_engine/autonomous_runtime_errors.py` |
| `must-fix this wave` | Add pilot smoke coverage for launchd lifecycle and detached daemon path | Closed via `tests/test_daemon_smoke.py` |

## Open backlog

| Status | Item | Why deferred |
| --- | --- | --- |
| `safe defer` | Further split `work_cli.py` parser shell into smaller command groups beyond autonomous surfaces | Current extraction already removed the riskiest runtime branch; deeper split is useful but not required for reliability parity |
| `safe defer` | Add macOS-specific launchd smoke test on a real LaunchAgent-capable runner | Valuable for operational confidence, but outside deterministic local/CI scope |
| `safe defer` | Add explicit schema validation for autonomous runtime JSON files | Nice hardening layer, but current normalize/read fallback already covers operational failures |
| `out-of-scope` | Repo-wide CLI/parser refactor | This wave is limited to reliability-first low-risk extraction |
| `out-of-scope` | Doctor-specific dissertation runtime/pipeline enhancements | Reserved for later dissertation/doctor work |
