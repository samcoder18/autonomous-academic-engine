# Legal-Academic Workspace

Reusable workspace для подготовки юридических академических текстов (ВКР, диссертаций, статей) с автономным движком машинно-проверяемых гейтов.

Система не используется для обхода антиплагиата, AI-детекторов и маскировки заимствований. Качество достигается через strong research, verified primary sources, evidence trace, честное понижение статуса и естественный академический стиль. Подробнее — [Принцип качества](#принцип-качества) и [AGENTS.md](AGENTS.md).

## Что умеет

- **Multi-work**: один репозиторий держит произвольное число `works/<slug>/` с собственным каноном, thesis lane, article lane и standards.
- **Deterministic gates**: фронтматтер ВКР, ГОСТ-библиография, DOCX-conformance, оригинальность (MinHash), work-type структура — всё машинно, без внешних SaaS.
- **One-shot pipeline**: единственная команда прогоняет все гейты и выдаёт честный вердикт (`submission-ready` или `strong-draft-with-blockers` с полным списком блокеров).
- **Source connectors**: stub/live архитектура для `publication.pravo.gov.ru`, `sudact.ru`, `cbr.ru`, `elibrary.ru`, `cyberleninka`, `semantic_scholar`, `vak.gov`. Live-режим opt-in per-connector.
- **Autonomous daemon + ops-канал**: long-running цикл с ops-alerts (stale-lock, stuck-detector, unhandled exception) и resource-guards.
- **Telegram runtime**: работа-aware orchestration с мультипроектным реестром, активной работой, принудительной пересборкой chat-context.
- **Work-type profiles**: `article`, `vkr-bachelor`, `vkr-specialist`, `master-thesis`, `dissertation-candidate`, `dissertation-doctor` — разные требования к структуре, библиографии и порогу оригинальности.

## Быстрый старт

```bash
git clone https://github.com/sam111-crypto/VKR.git
cd VKR
pip install -e ".[dev]"
python3 -m unittest discover -s tests -q   # 331 теста, ~11 секунд, offline
```

Запустить machine-driven гейты для активной работы (`biometrics-vkr` по умолчанию):

```bash
python3 -m telegram_console.work_cli one-shot-thesis --skip-docx
```

Сгенерировать фронтматтер ВКР из `works/<slug>/thesis/metadata.toml`:

```bash
python3 -m telegram_console.work_cli build-vkr-frontmatter
```

Посмотреть следующий безопасный шаг для активной работы:

```bash
python3 -m telegram_console.work_cli work-status
```

## Установка и зависимости

- **Python** 3.11+ (используется `tomllib` из stdlib).
- **Pandoc** — внешняя утилита для DOCX-экспорта (`scripts/export_docx.sh`, `scripts/export_academic_docx.sh`); не ставится через pip.
- **Runtime-зависимости** — только stdlib. Всё, включая DOCX-парсер, ГОСТ-линтер, оригинальность и source-connectors, написано без внешних пакетов.
- **Dev-зависимости** — `ruff`, `pytest` (опционально), `pre-commit`. Ставятся через `pip install -e ".[dev]"`.
- Секреты Telegram-рантайма — через env ([`telegram_console/config.py`](telegram_console/config.py)); `.env` не коммитится.

## Архитектура

```
workspace.toml                  # корневая конфигурация; список работ, default_work
├── works/<slug>/               # work bundle (ВКР / статья / диссертация)
│   ├── work.toml               # конфигурация работы
│   ├── work-canon.md           # утверждённые решения (канон)
│   ├── thesis/                 # thesis lane
│   │   ├── metadata.toml       # метаданные для фронтматтера (автор, абстракт, ключевые слова)
│   │   ├── manuscript/sections # канонический текст (Markdown)
│   │   ├── reviews/            # review-артефакты + one-shot-report
│   │   ├── ledgers/            # claim-level evidence
│   │   └── sources/            # исследовательский пул источников
│   └── articles/               # article lane (brief, evidence, claim-map, draft, review, final)
├── agents/                     # reusable агентные роли (thesis + article lanes)
├── meta/                       # reusable регламент + стандарты + schemas
│   ├── master-protocol.md      # единый процессный регламент
│   ├── standards/              # raw + normalized publication profiles
│   └── schemas/                # JSON-схемы для verdict-блоков и других контрактов
├── scripts/                    # launcher, assemble, export
├── telegram_console/           # рантайм: bot, autonomous daemon, connectors, gates
└── output/                     # runtime-артефакты (не версионируется; см. output/README.md)
```

Подробный регламент: [meta/master-protocol.md](meta/master-protocol.md). Индекс ролей и launcher'ов: [AGENTS.md](AGENTS.md). Известные пределы автономного движка: [meta/autonomous-engine-unknowns-2026-04-19.md](meta/autonomous-engine-unknowns-2026-04-19.md).

## Текущая работа по умолчанию

- `biometrics-vkr`
- Путь: [works/biometrics-vkr](works/biometrics-vkr)
- Канон: [work-canon.md](works/biometrics-vkr/work-canon.md)

## Autonomous VKR / thesis pipeline

Deterministic machine-driven гейты: фронтматтер, ГОСТ-библиография, DOCX-conformance, оригинальность, work-type структура.

- `python3 -m telegram_console.work_cli build-vkr-frontmatter [--work <slug>]` — генерирует `title-page.md`, `abstract-ru.md`, `abstract-en.md`, `keywords.md`, `task-sheet.md` из `works/<slug>/thesis/metadata.toml`. Любая метаданная-дыра блокирует сборку.
- `python3 -m telegram_console.work_cli one-shot-thesis [--work <slug>] [--corpus <path>] [--skip-docx] [--work-type <profile>]` — прогон всех гейтов с честным итоговым статусом.

Инварианты статуса:

- `submission-ready` — только когда все применимые гейты PASS **и** `work-type-structure` сошёлся с выбранным профилем.
- `strong-draft-with-blockers` — при любом FAIL; финализатор обязан понизить статус и передать блокеры в `repair_kernel`.
- Отчёт сохраняется в `works/<slug>/thesis/reviews/<дата>-one-shot-report.(md|json)`.

Внешние AI-детекторы и anti-plagiarism SaaS запрещены и системой не поддерживаются — см. hard rules в [AGENTS.md](AGENTS.md).

Known limits и unknowns: [meta/autonomous-engine-unknowns-2026-04-19.md](meta/autonomous-engine-unknowns-2026-04-19.md).

### Operational alerts (daemon / long-running)

- `autonomous_daemon` и `bot.py` эмитят структурированные ops-события (stale-lock recovery, lock-blocked, terminal-stop, run-stuck, unhandled exception) через [`telegram_console/ops_alerts.py`](telegram_console/ops_alerts.py).
- Доставка настраивается env-переменными `OPS_ALERT_CHAT_ID` (Telegram-чат для ops-событий — **не** совпадающий с пользовательским чатом проекта) и `OPS_ALERT_LOG_PATH` (tee-файл). Без конфигурации события идут в stderr + Python `logging`.
- Stuck-detector включается флагом `--stuck-after-minutes` у `autonomous daemon run` или переменной `DAEMON_STUCK_AFTER_MINUTES`. Срабатывание = terminal-state `run-stuck` + CRITICAL alert.
- Интеграционные тесты: [`tests/test_daemon_ops_integration.py`](tests/test_daemon_ops_integration.py).

### Source connectors (stub/live)

- По умолчанию все коннекторы в stub-режиме: CI и локальные прогоны не ходят в сеть, stub-ответы содержат настоящие первоисточники с `canonical_url`, датами редакций и `SourceKind`.
- Live-режим opt-in per-connector: `SOURCES_PRAVO_GOV_ENABLE=1`, `SOURCES_SUDACT_ENABLE=1`, `SOURCES_CBR_ENABLE=1`, `SOURCES_ELIBRARY_ENABLE=1`, `SOURCES_CYBERLENINKA_ENABLE=1`, `SOURCES_SEMANTIC_SCHOLAR_ENABLE=1`, `SOURCES_VAK_ENABLE=1`, `SOURCES_WEB_FALLBACK_ENABLE=1`.
- HTTP-транспорт инъектируем (`HttpClient(transport=...)`), так что тесты парсинга HTML проходят без urllib. Вторая линия защиты от случайных сетевых вызовов.

## Launcher / CLI

Верхнеуровневые команды:

```bash
# Thesis lane
bash scripts/codex_thesis.sh full-cycle works/biometrics-vkr/thesis/manuscript/sections/03-chapter-2.md
bash scripts/codex_thesis.sh write-section thesis/manuscript/sections/04-chapter-3.md --work biometrics-vkr
bash scripts/codex_thesis.sh source-pack sources/02-chapter-2-regulation.md --work biometrics-vkr

# Article lane
bash scripts/codex_academic.sh article --topic "Правовые пределы цифровой идентификации" --work biometrics-vkr
bash scripts/codex_academic.sh review works/biometrics-vkr/articles/drafts/example.md --work biometrics-vkr
bash scripts/codex_academic.sh finalize works/biometrics-vkr/articles/final/example.md --work biometrics-vkr

# Sborka / export
bash scripts/assemble_thesis.sh --work biometrics-vkr
bash scripts/export_docx.sh --work biometrics-vkr
bash scripts/export_academic_docx.sh --work biometrics-vkr

# Status + standards
python3 -m telegram_console.work_cli work-status [--json]
python3 -m telegram_console.work_cli standards-status [profile-id]
python3 -m telegram_console.work_cli standards-intake <profile-id>
python3 -m telegram_console.work_cli standards-refresh <profile-id>

# Autonomous deterministic gates
python3 -m telegram_console.work_cli build-vkr-frontmatter [--work <slug>]
python3 -m telegram_console.work_cli one-shot-thesis [--work <slug>] [--corpus <path>] [--skip-docx] [--work-type <profile>]

# Autonomous daemon
python3 -m telegram_console.work_cli autonomous daemon run --work <slug> [--stuck-after-minutes 30]
python3 -m telegram_console.work_cli autonomous daemon status --work <slug>
python3 -m telegram_console.work_cli autonomous daemon stop --work <slug>

# Maintenance
python3 -m telegram_console.work_cli skill-source-map audit [--skills-root <path>] [--json]
python3 -m telegram_console.work_cli skill-source-map sync-external --skills-root <path> [--write]
```

### Совместимость legacy-путей

- Старые thesis target paths вроде `manuscript/sections/...`, `chapters/...`, `sources/...` и article paths вроде `articles/drafts/...` автоматически резолвятся в `default_work`.
- Если `--work` не передан, launcher сначала пытается вывести work из пути target, иначе использует `default_work` из `workspace.toml`.
- Пустые каталоги `chapters/`, `manuscript/`, `sources/` в корне — локальные заглушки, не канон; канонический контент лежит только в `works/<slug>/`.

## Output paths

- Run trace: `output/runs/<work>/<lane>/`
- DOCX: `output/docx/<work>/` (генерируется локально, в git не версионируется — см. [output/README.md](output/README.md))
- Telegram runtime: `output/telegram/runtime/`
- One-shot reports: `works/<slug>/thesis/reviews/<дата>-one-shot-report.(md|json)` (JSON в git не коммитится по `.gitignore`)

## Telegram console

- Мультипроектный реестр с активным проектом и активной работой внутри проекта.
- Work-aware export, status, и chat-context.
- Ops-события daemon'а автоматически форвардятся в отдельный чат через `OPS_ALERT_CHAT_ID`.

Добавить проект:

```bash
python3 scripts/telegram_console.py project add \
  --title "Юридический workspace" \
  --root "/абсолютный/путь/к/проекту"
```

## Разработка и тесты

```bash
pip install -e ".[dev]"
export PYTHONPATH=.
python3 -m unittest discover -s tests -q   # 331 тест, offline, детерминированный
ruff check telegram_console/ tests/
ruff format --check telegram_console/ tests/
```

Подробнее: [tests/README.md](tests/README.md). История перехода на модель `works/<slug>/`: [meta/migration-history.md](meta/migration-history.md).

## CI

- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) на каждый push / PR запускает `ruff check`, `python3 -m unittest discover -s tests`, `skill-source-map audit` и smoke one-shot pipeline ([`tests/test_regression_harness.py`](tests/test_regression_harness.py)).
- Локальные хуки: [`.pre-commit-config.yaml`](.pre-commit-config.yaml) (`pip install pre-commit && pre-commit install`) — `ruff` + `ruff-format` на commit.

## Ключевые документы

- [AGENTS.md](AGENTS.md) — индекс агентных ролей, launcher'ов и hard rules.
- [meta/master-protocol.md](meta/master-protocol.md) — единый workflow-регламент для всех lane.
- [meta/autonomous-engine-unknowns-2026-04-19.md](meta/autonomous-engine-unknowns-2026-04-19.md) — границы автономного движка.
- [meta/engineering-audit-autonomous-workspace-2026-04-19.md](meta/engineering-audit-autonomous-workspace-2026-04-19.md) — полный инженерный аудит и план мигитации.
- [meta/standards/registry.toml](meta/standards/registry.toml) — реестр publication profiles.
- [output/README.md](output/README.md) — правила для runtime-артефактов.

## Принцип качества

Workspace **не используется** для:

- обхода антиплагиата и AI-детекторов;
- маскировки заимствований;
- имитации `submission-ready` без реальной первичной опоры.

Качество достигается через:

- strong research и собственный анализ;
- verified primary sources с зафиксированным provenance;
- evidence trace от claim к источнику;
- честное понижение статуса, когда гейты не сходятся;
- естественный академический русский без шаблонных фраз и без попыток обойти детекторы.

Полный список hard rules — [AGENTS.md](AGENTS.md).
