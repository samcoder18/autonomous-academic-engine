# Runtime Reliability Audit (2026-04-20)

Scope: `academic_engine/work_cli.py`, `academic_engine/work_cli_autonomous.py`,
`academic_engine/autonomous_daemon.py`,
`academic_engine/autonomous_scheduler.py`,
`academic_engine/autonomous_runtime_store.py`,
runtime JSON files under `output/telegram/runtime/autonomous/`,
daemon/launchd CLI surfaces, and targeted tests/docs around them.

## Summary

Результат волны: `daemon + CLI I/O` доведены до более стабильного и
предсказуемого состояния без смены публичных команд.

Что закрыто этой волной:

- общий runtime persistence слой вынесен в
  `academic_engine/autonomous_runtime_store.py`;
- `work_cli.py` разгружен за счёт выноса autonomous/daemon/launchd
  handlers в `academic_engine/work_cli_autonomous.py`;
- JSON-first contract для runtime surfaces нормализован через
  `academic_engine/work_cli_output.py`;
- single-work и multi-work daemon теперь детерминированно
  потребляют stop request и освобождают lock на terminal branches;
- corrupted runtime JSON больше не валит CLI status surfaces:
  вместо traceback возвращается стабильный fallback payload;
- CLI/runtime тесты выделены в отдельные модули вместо удержания
  всего покрытия в `tests/test_academic_engine.py`.

## Findings

### 1. Runtime persistence had avoidable duplication

До волны single-work daemon, multi-work daemon и autonomous run хранили
runtime JSON через локальные helpers и дублировали path/read/write
логику. Это повышало риск рассинхронизации формата и edge cases.

Текущее состояние:

- единые helpers для runtime dir, file paths, atomic JSON writes,
  lock payload и stop payload теперь живут в
  `academic_engine/autonomous_runtime_store.py`;
- публичные filenames и расположение файлов не изменены.

### 2. CLI autonomous surfaces were too monolithic

`academic_engine/work_cli.py` оставался основным parser + handler модулем
одновременно. Это затрудняло локальную правку JSON/error contracts и
расширение тестов.

Текущее состояние:

- parser и верхнеуровневый dispatch сохранены в `work_cli.py`;
- autonomous/daemon/launchd logic вынесена в
  `work_cli_autonomous.py`;
- общие JSON/error render helpers вынесены в
  `work_cli_output.py`.

### 3. Stop-request lifecycle was not fully deterministic

Риск: stop request мог оставаться на диске дольше одного terminal cycle и
создавать неоднозначность при повторном status/tick.

Текущее состояние:

- `run_daemon_tick()` очищает single-work stop request до записи
  terminal stopped state;
- `run_multi_work_daemon_tick()` делает то же для multi-work daemon;
- поведение покрыто отдельными reliability tests.

### 4. Corrupted runtime JSON needed stable fallback semantics

Риск: битый `*.json` в runtime должен приводить к статусу
`not-started`/fallback payload, а не к падению CLI.

Текущее состояние:

- autonomous status использует explicit fallback через
  `autonomous_status_payload()`;
- daemon и multi-daemon status surfaces уже нормализуют missing/corrupt
  state в deterministic default payload;
- JSON error paths для workspace/config ошибок стандартизованы как
  `kind + status + stop_reason + readiness_claim`.

### 5. Test architecture around daemon/CLI was too concentrated

Большой `tests/test_academic_engine.py` оставался перегруженным именно
CLI/runtime кейсами, что усложняло сопровождение.

Текущее состояние:

- autonomous CLI regression cases вынесены в
  `tests/test_work_cli_autonomous.py`;
- launchd cases вынесены в `tests/test_work_cli_launchd.py`;
- runtime/work-status/doc-truth cases вынесены в
  `tests/test_work_cli_runtime.py`;
- daemon reliability scenarios расширены в
  `tests/test_daemon_ops_integration.py`.

## Reliability outcomes

Проверенные инварианты после волны:

- single-work daemon освобождает lock при guard-triggered stop;
- single-work daemon освобождает lock при unhandled exception path;
- multi-work scheduler не рушит весь schedule pass, если `get_work_state`
  падает только для одной work;
- stop request single/multi daemon потребляется детерминированно;
- corrupted runtime JSON даёт стабильный JSON fallback вместо crash;
- launchd/autonomous/daemon JSON responses удерживают predictable
  machine-readable payloads.
- `WorkflowOrchestrator` больше не является крупным single-file bottleneck:
  shell slimmed to thin compatibility layer, а launch/workspace/status
  responsibilities вынесены в отдельные mixin-модули.
- pilot smoke покрывает detached background daemon path и lifecycle
  `AutonomousDaemonLaunchdManager` без привязки к системному launchd.

## Residual risks

- `work_cli.py` всё ещё остаётся большим parser-shell модулем; волна
  сняла только autonomous/daemon/launchd handler debt.
- multi-work scheduler всё ещё держит один outer containment catch на
  уровне `build_multi_work_schedule()`, но ожидаемые per-work failures
  теперь проходят через explicit runtime taxonomy в
  `autonomous_runtime_errors.py`.
- launchd coverage остаётся unit-style; реальный macOS service-manager
  smoke по-прежнему не входит в CI.

## Verification

Целевые проверки этой волны:

```bash
python3 -m unittest tests.test_daemon_smoke tests.test_daemon_ops_integration tests.test_work_cli_autonomous tests.test_work_cli_launchd tests.test_work_cli_runtime -q
python3 -m unittest discover -s tests -q
python3 -m ruff check academic_engine tests
python3 -m academic_engine.work_cli skill-source-map audit --json
```
