# Инженерный аудит: автономная система написания академических текстов

> Historical note (2026-04-21): references to `works/biometrics-vkr/` in this
> document are archival only. The bundle was removed from the active workspace
> during migration to `starter-work`.

Дата проведения: 2026-04-19. Объект: репозиторий `legal-academic-workspace` (корень клона на машине аудита).
Актуализация candidate-adjacent правды и метрик: 2026-04-20.
Актуализация daemon/CLI runtime reliability: 2026-04-20.

## 1. Резюме

Проект представляет собой **оркестрируемый workspace**: регламенты (`meta/master-protocol.md`), роли (`agents/*.md`), шаблоны (`templates/`), CLI для Codex (`scripts/*.sh` → `academic_engine.work_cli`), legacy chat/runtime слой и автономный daemon поверх `WorkflowOrchestrator`. Канонический текст живёт в `works/<slug>/`; производные DOCX и трассы — в `output/`.

**Сильные стороны:** чёткая иерархия источников истины; разделение thesis/article; execution contracts и quality gates в коде; runtime reliability wave с общим persistence слоем для daemon/autonomous state; выделенные CLI/runtime regression packs; candidate dissertation contour с отдельными maps/reviews/publication artifacts; `skill-source-map audit` без расхождений для объявленных 19 skills; профили `work.toml` согласованы с `meta/standards/registry.toml`.

**Главные риски:** инженерный долг вне candidate-adjacent зоны по-прежнему требует выборочного review; intentional top-level failsafe `except Exception` в daemon foreground loop остаётся осознанным компромиссом long-running runtime; pytest в окружении аудита не установлен (тесты запускаются через `python3 -m unittest`).

---

## 2. Инвентаризация по слоям

Подсчёт файлов на 2026-04-20: **394** (по `Path.rglob`, включая runtime/docs/test fixtures в workspace).


| Слой                    | Файлов (find -type f) | Примечание                                            |
| ----------------------- | --------------------- | ----------------------------------------------------- |
| `agents/`               | 19                    | Роли thesis + article                                 |
| `scripts/`              | 10                    | Тонкие оболочки над `work_cli`                        |
| `academic_engine/`     | 150                   | Python-ядро, dissertation contour, runtime support    |
| `academic_engine/*.py` | 75                    | Ядро Python                                           |
| `meta/`                 | 44                    | Протокол, стандарты, архив, audit docs                |
| `templates/`            | 20                    | Формы brief, review, dissertation scaffold            |
| `works/`                | 38                    | Активный work bundle + контент                        |
| `output/`               | 51                    | README и локальные артефакты                          |
| `tests/`                | 39                    | Отдельные regression-модули по workspace и gates      |


Классификация: **ядро движка** — `workspace.toml`, `agents/`, `meta/master-protocol.md`, `meta/skill-source-map.toml`, `templates/`, `academic_engine/`, `scripts/`; **контент** — `works/biometrics-vkr/`; **производное** — `output/docx`, `output/runs`, runtime namespace, `output/codex`.

---

## 3. Статический анализ Python и безопасность

### 3.1 Ruff

Команда: `python3 -m ruff check academic_engine tests`.

Итог на 2026-04-20: `python3 -m ruff check academic_engine tests` — **чисто**.

Вывод: candidate-adjacent python/test scope приведён к zero-issue состоянию.
Repo-wide cleanup вне этого скоупа по-прежнему стоит делать отдельными узкими
пачками, а не одним sweep-рефакторингом.

### 3.2 Тесты

`pytest` в системе аудита отсутствует. Запуск: `PYTHONPATH=. python3 -m unittest discover -s tests -q`.

Результат на 2026-04-20: **384 теста, OK**, время ~21 с.

Вывод: покрытие **существенно шире**, чем звучало в ранней версии аудита;
регрессии по workspace, dissertation contour, bundle state, gates,
autonomous planner и CLI страхуются отдельными тестовыми модулями.

### 3.3 Subprocess и секреты

- `**shell=True`**: в просмотренных вызовах `subprocess` в `academic_engine` **не обнаружено** — используются списки аргументов и `text=True` где уместно.
- **Секреты**: `[academic_engine/config.py](../academic_engine/config.py)` читает legacy chat token, SMTP, `CODEX_*` из окружения; отдельного логирования значений токенов в grep по паттернам не выявлено.
- **Широкие исключения**: после runtime wave intentional `except Exception` в touched scope остались в `[academic_engine/autonomous_daemon.py](../academic_engine/autonomous_daemon.py)` (top-level failsafe foreground loop) и `[academic_engine/autonomous_scheduler.py](../academic_engine/autonomous_scheduler.py)` (single-work failure isolation inside multi-work scheduling). Это скорее reliability containment, чем случайный broad catch, но для будущей типизации здесь всё ещё есть работа.

---

## 4. Трассировка контрактов: shell → артефакты

### 4.1 Thesis lane

1. `scripts/codex_thesis.sh` → `python3 -m academic_engine.work_cli launch-thesis "$@"`.
2. `[launch_thesis](../academic_engine/work_cli.py)`: загрузка `workspace.toml`, `resolve_work_selection`, `resolve_target_for_action`, профиль через `resolve_standard_profile`.
3. `build_thesis_execution_contract` + `_build_thesis_prompt`.
4. `**_run_codex`**: `codex exec -C <root> --skip-git-repo-check --full-auto -o <out_file> [-m model]`, stdin = prompt; при необходимости флаг `--search`.
5. **Артефакты прогона**: Markdown-ответ в `work.thesis.paths.output_runs_dir` (из `work.toml` относительно work root), метаданные `*.meta.json` с контрактом, путями target, профилем.

Канон секций **не перезаписывается** самим launcher: правки делает агент в репозитории по инструкции в prompt; launcher фиксирует trace.

### 4.2 Article lane

1. `scripts/codex_academic.sh` (без подкоманды `thesis`) → `work_cli launch-academic`.
2. Разбор `--topic` / `--brief` или target для `review` / `repair` / `finalize`.
3. `article_bundle_paths`, `build_article_execution_contract`, соответствующий `_build_*_prompt`.
4. Тот же `**_run_codex`**.
5. **Артефакты**: `works/<slug>/articles/runs/` — `*.md` + `*.meta.json`; управляемые пути bundle (brief, evidence, claim-map, draft, review, final, checklist, docx) задаются контрактом и тестами.

### 4.3 DOCX

Экспорт: отдельные скрипты `assemble_thesis.sh`, `export_docx.sh`, `export_academic_docx.sh` и методы оркестратора (`export-thesis-docx`, `export-article-docx` в autonomous runner). Pandoc вызывается списком аргументов без shell.

---

## 5. Автономный daemon и согласование с оркестратором

### 5.1 Компоненты

- **План:** `[autonomous_planner.build_autonomous_plan](../academic_engine/autonomous_planner.py)` — кандидаты из work state, фильтр `[evaluate_autonomous_policy](../academic_engine/autonomous_policy.py)`, ограничение `max_steps`.
- **Исполнение:** `[autonomous_runner.execute_autonomous_command](../academic_engine/autonomous_runner.py)` — whitelist: `work-status`, `standards-status`, `export-thesis-docx`, `export-article-docx <slug>`, `launch-thesis <preset> <target>`, `launch-academic <workflow> …`.
- **Daemon:** `[autonomous_daemon](../academic_engine/autonomous_daemon.py)` — lock-файлы (`*.daemon.lock.json`), stale lock по heartbeat, `run_daemon_tick` проверяет **active_run** и уходит в `waiting`, иначе строит план, `evaluate_daemon_action`, затем `execute_autonomous_command`. Лимиты: `max_cycles`, `max_runtime_minutes`.
- **Runtime store:** `[autonomous_runtime_store](../academic_engine/autonomous_runtime_store.py)` — общий слой для runtime dir, atomic JSON writes, lock/stop payload builders и fallback-safe read path.
- **CLI autonomous surface:** `[work_cli_autonomous](../academic_engine/work_cli_autonomous.py)` + `[work_cli_output](../academic_engine/work_cli_output.py)` — вынесенные handlers и JSON/error rendering helpers без смены публичного syntax `work_cli`.

### 5.2 Выводы по надёжности

- Параллельный второй daemon на тот же `work_id` блокируется lock с опцией recovery по stale PID.
- Конфликт с уже идущим workflow смягчается через статус `waiting` и причину `active-run`.
- Autonomous путь **не обходит** оркестратор: для «тяжёлых» действий вызывается `orchestrator.start_run`, что согласуется с единой моделью run records.
- Corrupted runtime JSON больше не должен валить status surfaces: CLI возвращает deterministic fallback payload.
- Stop request single/multi daemon теперь потребляется в рамках terminal cycle, а не остаётся подвешенным между status/tick вызовами.

---

## 6. Skills и стандарты

### 6.1 skill-source-map

Выполнено: `python3 -m academic_engine.work_cli skill-source-map audit --json`.

Результат: `declared_skill_count` 19, `manifest_skill_count` 19, `**issues`: []**, внешние SKILL-файлы в этом прогоне не проверялись (`external_skill_files_checked`: []).

### 6.2 Сверка `works/biometrics-vkr/work.toml` с registry


| Поле              | Значение            | Наличие в registry / normalized                  |
| ----------------- | ------------------- | ------------------------------------------------ |
| `thesis_profile`  | `sogu-vkr-2025`     | Да, `meta/standards/normalized/sogu-vkr-2025.md` |
| `article_profile` | `ru-law-article-v1` | Да, `ru-law-article-v1.md`                       |


Профили из `registry.toml` для journal / rf-dissertation присутствуют в дереве `normalized/`; «висячих» ссылок для активной работы не обнаружено.

### 6.3 Шаблоны и протокол

Упоминание `templates/chat-sync.md` в `[meta/master-protocol.md](master-protocol.md)` соответствует реальному файлу `[templates/chat-sync.md](../templates/chat-sync.md)`.

---

## 7. Сопровождаемость и документация

- `**orchestrator.py`** после текущей волны — тонкая оболочка (~58 строк), а orchestration разнесён по mixin-модулям `orchestrator_launch/runtime/status/workspace/article/thesis`. Главный remaining debt уже не в одном монолите, а в том, что несколько mixin-модулей всё ещё крупные и требуют точечной дальнейшей полировки.
- **Документация**: README и AGENTS согласованы с фактическими entrypoints; в markdown часто встречаются **абсолютные пути** к workspace — ухудшают переносимость при публикации ссылок из репозитория на другой машине (косметика для git clone).
- **Дублирование skills-каталогов** (`.codex`, `.claude`, `.agents`): ожидаемо для разных клиентов; дисциплина — периодический `skill-source-map audit` с `--skills-root` при использовании внешних копий SKILL.

---

## 8. Приоритизированные рекомендации


| Приоритет | Действие                                                                                                                               | Обоснование                                             |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| P1        | Сохранить `unittest` как основной CI-таргет; в README явно указать `python3 -m unittest discover -s tests` рядом с опциональным pytest | Снижает путаницу на чистых машинах без dev-зависимостей |
| P2        | Постепенно снижать долю E501/I001 через `ruff format` / `--fix` в отдельных PR                                                         | Улучшает читаемость и pre-commit гигиену                |
| P2        | Продолжать точечную полировку mixin-модулей оркестратора, а не возвращаться к монолиту                                                 | Поддерживаемость после завершённой декомпозиции         |
| P3        | Заменить абсолютные URL в внутренних markdown-ссылках на относительные от корня репо                                                   | Переносимость документации                              |
| P3        | Сужение `except Exception` в legacy bot/work_cli к ожидаемым типам                                                                     | Проще отлаживать сбои legacy runtime/CLI                |


---

## 9. Команды, воспроизводящие аудит

```bash
cd /path/to/repo
export PYTHONPATH=.

python3 -m ruff check academic_engine tests --statistics
python3 -m unittest discover -s tests -q
python3 -m academic_engine.work_cli skill-source-map audit --json
```

---

## 10. Ограничения аудита

- Не проводился предметный юридический разбор текстов в `works/biometrics-vkr/thesis/manuscript/`.
- Не запускались реальные `codex exec` и legacy bot layer против продакшен-секретов.
- Внешние файлы `.codex/skills/*.md` вне репозитория в `skill-source-map audit` не передавались.
