# Инженерный аудит: автономная система написания академических текстов

Дата проведения: 2026-04-19. Объект: репозиторий `legal-academic-workspace` (корень клона на машине аудита).

## 1. Резюме

Проект представляет собой **оркестрируемый workspace**: регламенты (`meta/master-protocol.md`), роли (`agents/*.md`), шаблоны (`templates/`), CLI для Codex (`scripts/*.sh` → `telegram_console.work_cli`), Telegram-рантайм и автономный daemon поверх `WorkflowOrchestrator`. Канонический текст живёт в `works/<slug>/`; производные DOCX и трассы — в `output/`.

**Сильные стороны:** чёткая иерархия источников истины; разделение thesis/article; execution contracts и quality gates в коде; 216 автотестов на `unittest`; `skill-source-map audit` без расхождений для объявленных 19 skills; профили `work.toml` согласованы с `meta/standards/registry.toml`.

**Главные риски:** монолитный `orchestrator.py` (~1918 строк); 281 замечание `ruff` (в основном длина строк и стиль импортов); широкие `except Exception` в отдельных модулях; pytest в окружении аудита не установлен (тесты запускаются через `python3 -m unittest`).

---

## 2. Инвентаризация по слоям

Подсчёт файлов с расширениями `.py`, `.sh`, `.md`, `.toml`, `.json` вне `.git`/`.cursor`: **166** (приблизительно, по `find`).


| Слой                    | Файлов (find -type f) | Примечание                                            |
| ----------------------- | --------------------- | ----------------------------------------------------- |
| `agents/`               | 19                    | Роли thesis + article                                 |
| `scripts/`              | 9                     | Тонкие оболочки над `work_cli`                        |
| `telegram_console/`     | 77                    | Включая не только `.py` (например README в поддереве) |
| `telegram_console/*.py` | 38                    | Ядро Python                                           |
| `meta/`                 | 35                    | Протокол, стандарты, архив                            |
| `templates/`            | 13                    | Формы brief, review, sync                             |
| `works/`                | 37                    | Активный work bundle + контент                        |
| `output/`               | 29                    | README и локальные артефакты                          |
| `tests/`                | 3                     | Один крупный `test_telegram_console.py` + README      |


Классификация: **ядро движка** — `workspace.toml`, `agents/`, `meta/master-protocol.md`, `meta/skill-source-map.toml`, `templates/`, `telegram_console/`, `scripts/`; **контент** — `works/biometrics-vkr/`; **производное** — `output/docx`, `output/runs`, `output/telegram`, `output/codex`.

---

## 3. Статический анализ Python и безопасность

### 3.1 Ruff

Команда: `python3 -m ruff check telegram_console tests`.

Итог: **281** нарушение. Статистика (крупные категории):

- **E501** (203): строки длиннее 120 символов.
- **I001** (36): неотсортированные импорты.
- **UP017, UP035, UP037** и др.: стиль/модернизация под py311.

77 проблем помечены как автоисправимые (`ruff check --fix`).

### 3.2 Тесты

`pytest` в системе аудита отсутствует. Запуск: `PYTHONPATH=. python3 -m unittest discover -s tests -q`.

Результат: **216 тестов, OK**, время ~11 с.

Вывод: покрытие **существенно шире**, чем «один файл тестов» звучит снаружи; регрессии по workspace, bundle state, gates, autonomous planner частично страхуются.

### 3.3 Subprocess и секреты

- `**shell=True`**: в просмотренных вызовах `subprocess` в `telegram_console` **не обнаружено** — используются списки аргументов и `text=True` где уместно.
- **Секреты**: `[telegram_console/config.py](telegram_console/config.py)` читает `TELEGRAM_BOT_TOKEN`, SMTP, `CODEX_*` из окружения; отдельного логирования значений токенов в grep по паттернам не выявлено.
- **Широкие исключения**: `except Exception` встречается в `[telegram_console/bot.py](telegram_console/bot.py)`, `[telegram_console/work_cli.py](telegram_console/work_cli.py)` — зона для точечного сужения типов исключений при рефакторинге.

---

## 4. Трассировка контрактов: shell → артефакты

### 4.1 Thesis lane

1. `scripts/codex_thesis.sh` → `python3 -m telegram_console.work_cli launch-thesis "$@"`.
2. `[launch_thesis](telegram_console/work_cli.py)`: загрузка `workspace.toml`, `resolve_work_selection`, `resolve_target_for_action`, профиль через `resolve_standard_profile`.
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

- **План:** `[autonomous_planner.build_autonomous_plan](telegram_console/autonomous_planner.py)` — кандидаты из work state, фильтр `[evaluate_autonomous_policy](telegram_console/autonomous_policy.py)`, ограничение `max_steps`.
- **Исполнение:** `[autonomous_runner.execute_autonomous_command](telegram_console/autonomous_runner.py)` — whitelist: `work-status`, `standards-status`, `export-thesis-docx`, `export-article-docx <slug>`, `launch-thesis <preset> <target>`, `launch-academic <workflow> …`.
- **Daemon:** `[autonomous_daemon](telegram_console/autonomous_daemon.py)` — lock-файлы (`*.daemon.lock.json`), stale lock по heartbeat, `run_daemon_tick` проверяет **active_run** и уходит в `waiting`, иначе строит план, `evaluate_daemon_action`, затем `execute_autonomous_command`. Лимиты: `max_cycles`, `max_runtime_minutes`.

### 5.2 Выводы по надёжности

- Параллельный второй daemon на тот же `work_id` блокируется lock с опцией recovery по stale PID.
- Конфликт с уже идущим workflow смягчается через статус `waiting` и причину `active-run`.
- Autonomous путь **не обходит** оркестратор: для «тяжёлых» действий вызывается `orchestrator.start_run`, что согласуется с единой моделью run records.

---

## 6. Skills и стандарты

### 6.1 skill-source-map

Выполнено: `python3 -m telegram_console.work_cli skill-source-map audit --json`.

Результат: `declared_skill_count` 19, `manifest_skill_count` 19, `**issues`: []**, внешние SKILL-файлы в этом прогоне не проверялись (`external_skill_files_checked`: []).

### 6.2 Сверка `works/biometrics-vkr/work.toml` с registry


| Поле              | Значение            | Наличие в registry / normalized                  |
| ----------------- | ------------------- | ------------------------------------------------ |
| `thesis_profile`  | `sogu-vkr-2025`     | Да, `meta/standards/normalized/sogu-vkr-2025.md` |
| `article_profile` | `ru-law-article-v1` | Да, `ru-law-article-v1.md`                       |


Профили из `registry.toml` для journal / rf-dissertation присутствуют в дереве `normalized/`; «висячих» ссылок для активной работы не обнаружено.

### 6.3 Шаблоны и протокол

Упоминание `templates/chat-sync.md` в `[meta/master-protocol.md](meta/master-protocol.md)` соответствует реальному файлу `[templates/chat-sync.md](templates/chat-sync.md)`.

---

## 7. Сопровождаемость и документация

- `**orchestrator.py`**: ~1918 строк — высокая когнитивная нагрузка; разумное направление рефакторинга — выделение подмодулей по lane или по фазам run lifecycle (без изменения поведения).
- **Документация**: README и AGENTS согласованы с фактическими entrypoints; в markdown часто встречаются **абсолютные пути** к workspace — ухудшают переносимость при публикации ссылок из репозитория на другой машине (косметика для git clone).
- **Дублирование skills-каталогов** (`.codex`, `.claude`, `.agents`): ожидаемо для разных клиентов; дисциплина — периодический `skill-source-map audit` с `--skills-root` при использовании внешних копий SKILL.

---

## 8. Приоритизированные рекомендации


| Приоритет | Действие                                                                                                                               | Обоснование                                             |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| P1        | Сохранить `unittest` как основной CI-таргет; в README явно указать `python3 -m unittest discover -s tests` рядом с опциональным pytest | Снижает путаницу на чистых машинах без dev-зависимостей |
| P2        | Постепенно снижать долю E501/I001 через `ruff format` / `--fix` в отдельных PR                                                         | Улучшает читаемость и pre-commit гигиену                |
| P2        | Декомпозиция `orchestrator.py` на модули с сохранением публичного API                                                                  | Долгосрочная сопровождаемость                           |
| P3        | Заменить абсолютные URL в внутренних markdown-ссылках на относительные от корня репо                                                   | Переносимость документации                              |
| P3        | Сужение `except Exception` в bot/work_cli к ожидаемым типам                                                                            | Проще отлаживать сбои Telegram/CLI                      |


---

## 9. Команды, воспроизводящие аудит

```bash
cd /path/to/repo
export PYTHONPATH=.

python3 -m ruff check telegram_console tests --statistics
python3 -m unittest discover -s tests -q
python3 -m telegram_console.work_cli skill-source-map audit --json
```

---

## 10. Ограничения аудита

- Не проводился предметный юридический разбор текстов в `works/biometrics-vkr/thesis/manuscript/`.
- Не запускались реальные `codex exec` и Telegram-бот против продакшен-секретов.
- Внешние файлы `.codex/skills/*.md` вне репозитория в `skill-source-map audit` не передавались.

