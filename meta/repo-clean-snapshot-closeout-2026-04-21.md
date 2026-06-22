# Repo Clean-Snapshot Closeout (2026-04-21)

Канонический closeout-документ для **repo/platform layer** после финальной
verification-first волны на clean snapshot.

## Итоговый verdict

- Repo/platform layer можно честно считать **finalized on clean snapshot**.
- Этот verdict не означает наличие `submission-ready` thesis/article в default
  work и не заменяет content-level quality evaluation.
- `starter-work` намеренно остаётся пустым clean article-only starter bundle и
  не считается blocker'ом для repo closeout.

## Snapshot

- Базовый commit перед closeout-правками: `7fde8785104bf5a12db0cf114095c88dce2aa5f6`
- Финальный closeout commit: `0d60d9c64cf0406f1c557ce6458804da5df10638`
- Ветка: `master`
- Guardrail из §11.1 [master-protocol.md](master-protocol.md) соблюдён:
  verification wave выполнялась на clean git snapshot.
- Активная работа по умолчанию: `starter-work`
- `work-status --json` для `starter-work`: `known_blocker_count = 0`
- `standards-status journal-jrp`: `raw_status = available`, `conflict_flag = no`

## Что было найдено и исправлено в этой волне

### CLI-ROOT-001 — CLI по умолчанию резолвил workspace root от package path, а не от `cwd`

Симптом:

- `python3 -m academic_engine.work_cli ...` без явного `root_dir` на изолированном
  temp workspace запускался от `/Users/albina/дипломная`, а не от текущей рабочей папки.
- Из-за этого `work init` мог писать `*-smoke` bundle прямо в repo root, а
  `one-shot-*` и `build-dissertation-artifacts` смотрели не в тот workspace.

Repair:

- `academic_engine.work_cli.main()` теперь по умолчанию использует `Path.cwd()`.
- Добавлен regression test, фиксирующий contract: CLI без `root_dir` обязан
  использовать текущую рабочую директорию как workspace root.
- Случайные smoke-следы (`article-smoke`, `thesis-smoke`, `candidate-smoke`) были
  удалены в рамках этой же wave.

Других воспроизводимых repo-level defects в финальной verification wave не найдено.

## Verification Record

### Baseline matrix

Прогонялась многократно на одном и том же snapshot после repair pass:

```bash
python3 -m unittest discover -s tests -q
ruff check academic_engine/ tests/
ruff format --check academic_engine/ tests/
python3 -m academic_engine.work_cli skill-source-map audit --json
```

Результат повторных baseline pass:

- pass 1: `388 tests OK`, `ruff check` OK, `ruff format --check` OK, `skill-source-map ok=true`
- pass 2: `388 tests OK`, `ruff check` OK, `ruff format --check` OK, `skill-source-map ok=true`
- final cleanup pass: `388 tests OK`, `ruff check` OK, `ruff format --check` OK, `skill-source-map ok=true`

### Targeted suites

```bash
python3 -m unittest tests.test_regression_harness -v
python3 -m unittest tests.test_work_state tests.test_work_cli_runtime tests.test_work_cli_autonomous tests.test_daemon_smoke -v
python3 -m unittest tests.test_work_cli_runtime tests.test_work_bootstrap -v
```

Результаты:

- `tests.test_regression_harness`: `6 tests OK`
- `tests.test_work_state + tests.test_work_cli_runtime + tests.test_work_cli_autonomous + tests.test_daemon_smoke`: `30 tests OK`
- `tests.test_work_cli_runtime + tests.test_work_bootstrap`: `30 tests OK`

### Live workspace checks

```bash
python3 -m academic_engine.work_cli work-status --json
python3 -m academic_engine.work_cli standards-status journal-jrp
```

Подтверждено:

- `starter-work` остаётся default work
- `known_blocker_count = 0`
- suggested next action остаётся lane-aware и article-only
- standards profile `journal-jrp` остаётся official / raw-available / conflict-free

### Temp-workspace command-surface smoke

В изолированном temp workspace были подтверждены следующие публичные surfaces:

- `work init` для `article`, `vkr-bachelor`, `dissertation-candidate`
- `work-status --json` для article-only default work
- `one-shot-thesis --work thesis-smoke --skip-docx` возвращает blocker-report, а не crash
- `build-dissertation-artifacts --work candidate-smoke` честно возвращает metadata blockers
- `one-shot-dissertation --work candidate-smoke --skip-docx` пишет dissertation report с blocker'ами
- `one-shot-dissertation --work thesis-smoke --skip-docx` корректно отказывает non-dissertation work без crash

Критически важно:

- после фикса CLI команды в temp smoke больше не пачкают repo root
- все smoke-артефакты остались внутри temp workspace

## Historical Audit Status

- `final-quality-audit-2026-04-20.md`, `system-project-master-audit-2026-04-20.md`,
  `system-project-master-backlog-2026-04-20.md` и
  `system-project-master-remediation-2026-04-20.md` остаются frozen historical evidence.
- Этот документ является актуальным clean-snapshot closeout для repo/platform layer.

## Closeout Boundaries

- Первичная clean-snapshot wave не включала tag/release package и GitHub release notes; follow-up release-closeout добавил GitHub-facing changelog и release body отдельно.
- В эту волну не входило наполнение `starter-work` содержательным article bundle.
- В эту волну не входил content-level verdict по thesis/article manuscript quality.
