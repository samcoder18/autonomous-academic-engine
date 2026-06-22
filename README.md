# Autonomous Academic Engine

Автономный workflow-engine для подготовки юридических академических текстов: ВКР, диссертаций и статей с machine-driven quality gates, verified evidence trace и честным finalization.

Система не используется для обхода антиплагиата, AI-детекторов и маскировки заимствований. Качество достигается через strong research, verified primary sources, evidence trace, честное понижение статуса и естественный академический стиль. Подробнее — [Принцип качества](#принцип-качества) и [AGENTS.md](AGENTS.md).

## Что умеет

- **Multi-work**: один репозиторий держит произвольное число `works/<slug>/` с собственным каноном, thesis lane, article lane и standards.
- **Deterministic gates**: фронтматтер ВКР, dissertation artifacts/maps/reviews, publication-claim coverage, ГОСТ-библиография, DOCX-conformance, originality (MinHash), work-type структура и length-conformance — всё машинно, без внешних SaaS.
- **Isolated role DAG**: каждая профильная роль работает отдельным Codex-процессом в staging-копии work; публикация происходит только после machine gates и conflict-safe promotion.
- **One-shot pipeline**: команда прогоняет machine gates и возвращает `machine-gates-passed` или `blocked`. Academic readiness вычисляется отдельно через evaluator + machine veto.
- **Source connectors**: stub/live архитектура для `publication.pravo.gov.ru`, `sudact.ru`, `cbr.ru`, `elibrary.ru`, `cyberleninka`, `semantic_scholar`, `vak.gov`. Live-режим opt-in per-connector.
- **Autonomous daemon + локальный ops-канал**: long-running цикл с ops-alerts (stale-lock, stuck-detector, unhandled exception) и resource-guards через stderr/log-файл.
- **CLI-first управление**: создание работ, запуск workflow, one-shot gates, экспорт и статус выполняются через `python3 -m telegram_console.work_cli` и shell launchers.
- **Work-type profiles**: `article`, `vkr-bachelor`, `vkr-specialist`, `master-thesis`, `dissertation-candidate`, `dissertation-doctor` — разные требования к структуре, библиографии и порогу оригинальности.

## Быстрый старт

```bash
git clone https://github.com/samcoder18/autonomous-academic-engine.git
cd autonomous-academic-engine
pip install -e ".[dev]"
python3 -m unittest discover -s tests -q   # offline, детерминированный
```

Создать новую работу (статью / ВКР / диссертацию) и сразу начать по ней работать:

```bash
# Статья
python3 -m telegram_console.work_cli work init smart-contracts-article \
  --artifact-type article \
  --title "Правовая природа смарт-контрактов" \
  --topic "Смарт-контракты в ГК РФ"

# ВКР (бакалавриат) и сразу сделать её активной
python3 -m telegram_console.work_cli work init my-vkr-2026 \
  --artifact-type vkr-bachelor \
  --title "ВКР 2026" \
  --topic "Тема ВКР" \
  --set-default

# Кандидатская диссертация с обеими lanes
python3 -m telegram_console.work_cli work init phd-law \
  --artifact-type dissertation-candidate \
  --title "Кандидатская диссертация" \
  --topic "Тема диссертации" \
  --lanes thesis,article
```

Запустить machine-driven гейты по thesis-capable работе:

```bash
python3 -m telegram_console.work_cli one-shot-thesis --work <thesis-slug> --corpus <corpus.json>
```

Для dissertation contour есть явный entrypoint:

```bash
python3 -m telegram_console.work_cli one-shot-dissertation --work <dissertation-slug> --corpus <corpus.json>
```

Запуск role workflow возвращает `queued` и `workflow_id`; выполнение идёт в background:

```bash
python3 -m telegram_console.work_cli launch-thesis full-cycle <section> --work <slug>
python3 -m telegram_console.work_cli launch-academic article --topic "..." --work <slug>
```

Посмотреть следующий безопасный шаг:

```bash
python3 -m telegram_console.work_cli work-status
```

Сгенерировать фронтматтер ВКР из `works/<slug>/thesis/metadata.toml`:

```bash
python3 -m telegram_console.work_cli build-vkr-frontmatter --work <thesis-slug>
```

Сгенерировать dissertation artifacts из `works/<slug>/thesis/dissertation/metadata.toml`:

```bash
python3 -m telegram_console.work_cli build-dissertation-artifacts --work <dissertation-slug>
```

## Установка и зависимости

- **Python** 3.11+ (используется `tomllib` из stdlib).
- **Pandoc** — внешняя утилита для DOCX-экспорта (`scripts/export_docx.sh`, `scripts/export_academic_docx.sh`); не ставится через pip.
- **Runtime-зависимости** — только stdlib. ГОСТ-линтер, originality и source-connectors написаны без внешних пакетов. DOCX preview formatter использует `python-docx` только как опциональную локальную зависимость при явном запуске preview-скрипта.
- **Dev-зависимости** — `ruff`, `pytest` (опционально), `pre-commit`. Ставятся через `pip install -e ".[dev]"`.
- `.env` и локальные runtime-секреты не коммитятся. Поддерживаемая operator surface проекта — CLI/file-first; прежний remote-chat control layer не является актуальной частью проекта.

## Архитектура

```
workspace.toml                  # корневая конфигурация; список работ, default_work
├── works/<slug>/               # work bundle (ВКР / статья / диссертация)
│   ├── work.toml               # конфигурация работы
│   ├── work-canon.md           # утверждённые решения (канон)
│   ├── thesis/                 # thesis lane
│   │   ├── metadata.toml       # метаданные для фронтматтера (автор, абстракт, ключевые слова)
│   │   ├── dissertation/       # dissertation contour: maps, reviews, publications, artifacts, defense
│   │   ├── manuscript/sections # канонический текст (Markdown)
│   │   ├── reviews/            # review-артефакты + one-shot отчёты
│   │   ├── ledgers/            # claim-level evidence
│   │   └── sources/            # исследовательский пул источников
│   └── articles/               # article lane (brief, evidence, claim-map, draft, review, final)
├── agents/                     # reusable агентные роли (thesis + article lanes)
├── meta/                       # reusable регламент + стандарты + schemas
│   ├── master-protocol.md      # единый процессный регламент
│   ├── standards/              # raw + normalized publication profiles
│   └── schemas/                # JSON-схемы для verdict-блоков и других контрактов
├── scripts/                    # launcher, assemble, export
├── telegram_console/           # Python-пакет с legacy-именем: CLI, daemon, connectors, gates
└── output/                     # runtime-артефакты (не версионируется; см. output/README.md)
```

Подробный регламент: [meta/master-protocol.md](meta/master-protocol.md). Индекс ролей и launcher'ов: [AGENTS.md](AGENTS.md). Известные пределы автономного движка: [meta/autonomous-engine-unknowns-2026-04-19.md](meta/autonomous-engine-unknowns-2026-04-19.md). Audit candidate contour перед doctor-phase: [meta/candidate-polish-audit-2026-04-20.md](meta/candidate-polish-audit-2026-04-20.md). Runtime reliability wave для daemon/CLI: [meta/runtime-reliability-audit-2026-04-20.md](meta/runtime-reliability-audit-2026-04-20.md). Итоговый финальный polish-аудит: [meta/final-quality-audit-2026-04-20.md](meta/final-quality-audit-2026-04-20.md). Актуальный clean-snapshot closeout repo/platform layer: [meta/repo-clean-snapshot-closeout-2026-04-21.md](meta/repo-clean-snapshot-closeout-2026-04-21.md).

## Текущая работа по умолчанию

- `starter-work`
- Путь: [works/starter-work](works/starter-work)
- Канон: [work-canon.md](works/starter-work/work-canon.md)
- Тип: clean article-only starter bundle c profile `journal-jrp`
- Содержательные научные работы в clean snapshot не версионируются; новые работы
  создаются отдельными bundle через `work init`.
- Для thesis/dissertation launcher'ов указывай явный `--work <slug>`, потому что default work не thesis-capable.

## Autonomous VKR / thesis pipeline

Deterministic machine-driven гейты: фронтматтер, ГОСТ-библиография, DOCX-conformance, оригинальность, work-type структура.

- `python3 -m telegram_console.work_cli build-vkr-frontmatter [--work <slug>]` — генерирует `title-page.md`, `abstract-ru.md`, `abstract-en.md`, `keywords.md`, `task-sheet.md` из `works/<slug>/thesis/metadata.toml`. Любая метаданная-дыра блокирует сборку.
- `python3 -m telegram_console.work_cli build-dissertation-artifacts [--work <slug>]` — генерирует `author-abstract.md` и `defense-checklist.md` из `works/<slug>/thesis/dissertation/metadata.toml`. Для candidate contour это завершающая формальная ступень после maps, review sequence и author-position drafting; `publication-claim-matrix.md` поддерживается отдельно как обязательный scaffold artifact.
- `python3 -m telegram_console.work_cli one-shot-thesis [--work <slug>] --corpus <path> [--work-type <profile>]` — strict thesis/VKR gates; DOCX, metadata/frontmatter, work type и originality corpus обязательны.
- `python3 -m telegram_console.work_cli one-shot-dissertation [--work <slug>] --corpus <path> [--work-type <profile>]` — strict dissertation gates: artifacts, maps, reviews, publication evidence, publication-claim matrix, length, ГОСТ, DOCX, originality.
- Legacy-флаг `--skip-docx` принимается для CLI-совместимости, но в strict mode не отключает обязательный DOCX gate.

Инварианты статуса:

- `submission-ready` — только когда независимый evaluator допускает статус, а все обязательные machine gates PASS.
- `strong-draft-with-blockers` — при любом FAIL; финализатор обязан понизить статус и передать блокеры в `repair_kernel`.
- Прямой DOCX export также fail-closed: без последнего `workflow-run/v1` со статусом `submission-ready` экспорт блокируется.
- `one-shot-thesis` сохраняет отчёт в `works/<slug>/thesis/reviews/<дата>-one-shot-report.(md|json)`.
- `one-shot-dissertation` сохраняет отчёт в `works/<slug>/thesis/reviews/<дата>-one-shot-dissertation-report.(md|json)`.

Внешние AI-детекторы и anti-plagiarism SaaS запрещены и системой не поддерживаются — см. hard rules в [AGENTS.md](AGENTS.md).

Known limits и unknowns: [meta/autonomous-engine-unknowns-2026-04-19.md](meta/autonomous-engine-unknowns-2026-04-19.md).

### Operational alerts (daemon / long-running)

- `autonomous_daemon` эмитит структурированные ops-события (stale-lock recovery, lock-blocked, terminal-stop, run-stuck, unhandled exception) через [`telegram_console/ops_alerts.py`](telegram_console/ops_alerts.py).
- Доставка настраивается `OPS_ALERT_LOG_PATH` (tee-файл). Без конфигурации события идут в stderr + Python `logging`.
- Stuck-detector включается флагом `--stuck-after-minutes` у `autonomous daemon run` или переменной `DAEMON_STUCK_AFTER_MINUTES`. Срабатывание = terminal-state `run-stuck` + CRITICAL alert.
- Runtime state/lock/stop файлы под `output/telegram/runtime/autonomous/` пишутся атомарно из соображений обратной совместимости с историческим namespace; это не удалённое chat-управление.
- JSON-first daemon/autonomous surfaces удерживают machine-readable contract: `kind`, `status`, `readiness_claim`; blocked/error payloads добавляют `stop_reason`.
- Интеграционные тесты: [`tests/test_daemon_ops_integration.py`](tests/test_daemon_ops_integration.py).

### Source connectors (stub/live)

- По умолчанию все коннекторы в stub-режиме: CI и локальные прогоны не ходят в сеть, stub-ответы содержат настоящие первоисточники с `canonical_url`, датами редакций и `SourceKind`.
- Live-режим opt-in per-connector: `SOURCES_PRAVO_GOV_ENABLE=1`, `SOURCES_SUDACT_ENABLE=1`, `SOURCES_CBR_ENABLE=1`, `SOURCES_ELIBRARY_ENABLE=1`, `SOURCES_CYBERLENINKA_ENABLE=1`, `SOURCES_SEMANTIC_SCHOLAR_ENABLE=1`, `SOURCES_VAK_ENABLE=1`, `SOURCES_WEB_FALLBACK_ENABLE=1`.
- HTTP-транспорт инъектируем (`HttpClient(transport=...)`), так что тесты парсинга HTML проходят без urllib. Вторая линия защиты от случайных сетевых вызовов.

## Launcher / CLI

Верхнеуровневые команды:

```bash
# Thesis lane
bash scripts/codex_thesis.sh full-cycle works/<thesis-slug>/thesis/manuscript/sections/03-chapter-2.md
bash scripts/codex_thesis.sh write-section thesis/manuscript/sections/04-chapter-3.md --work <thesis-slug>
bash scripts/codex_thesis.sh source-pack sources/02-chapter-2-regulation.md --work <thesis-slug>

# Article lane
bash scripts/codex_academic.sh article --topic "Правовые пределы цифровой идентификации" --work starter-work
bash scripts/codex_academic.sh review works/<article-slug>/articles/drafts/example.md --work <article-slug>
bash scripts/codex_academic.sh finalize works/<article-slug>/articles/final/example.md --work <article-slug>

# Сборка / export
bash scripts/assemble_thesis.sh --work <thesis-slug>
bash scripts/export_docx.sh --work <thesis-slug>
bash scripts/export_academic_docx.sh --work <article-slug>

# Bootstrap a new work bundle
python3 -m telegram_console.work_cli work init <slug> \
  --artifact-type {article|vkr|vkr-bachelor|vkr-specialist|master-thesis|dissertation-candidate|dissertation-doctor} \
  --title "Название" [--topic "Тема"] [--language ru] \
  [--lanes thesis,article] [--thesis-profile <id>] [--article-profile <id>] \
  [--set-default] [--json]

# Status + standards
python3 -m telegram_console.work_cli work-status [--json]
python3 -m telegram_console.work_cli standards-status [profile-id]
python3 -m telegram_console.work_cli standards-intake <profile-id>
python3 -m telegram_console.work_cli standards-refresh <profile-id>

# Autonomous deterministic gates
python3 -m telegram_console.work_cli build-vkr-frontmatter [--work <slug>]
python3 -m telegram_console.work_cli build-dissertation-artifacts [--work <slug>]
python3 -m telegram_console.work_cli one-shot-thesis [--work <slug>] --corpus <path> [--work-type <profile>]
python3 -m telegram_console.work_cli one-shot-dissertation [--work <slug>] --corpus <path> [--work-type <profile>]

# Autonomous daemon
python3 -m telegram_console.work_cli autonomous daemon run --work <slug> [--stuck-after-minutes 30]
python3 -m telegram_console.work_cli autonomous daemon status --work <slug>
python3 -m telegram_console.work_cli autonomous daemon stop --work <slug>

# Maintenance
python3 -m telegram_console.work_cli skill-source-map audit [--skills-root <path>] [--json]
python3 -m telegram_console.work_cli skill-source-map sync-external --skills-root <path> [--write]
```

`skill-source-map sync-external` синхронизирует внешний `SKILL.md` целиком с repo-side `agents/*.md`, сохраняя frontmatter внешнего файла и добавляя `Source of truth`.

### Совместимость legacy-путей

- Старые thesis target paths вроде `manuscript/sections/...`, `chapters/...`, `sources/...` и article paths вроде `articles/drafts/...` автоматически резолвятся в `default_work`.
- Если `--work` не передан, launcher сначала пытается вывести work из пути target, иначе использует `default_work` из `workspace.toml`.
- Пустые каталоги `chapters/`, `manuscript/`, `sources/` в корне — локальные заглушки, не канон; канонический контент лежит только в `works/<slug>/`.

## Output paths

- Run trace: `output/runs/<workflow-id>/`
- DOCX: `output/docx/<work>/` (генерируется локально, в git не версионируется — см. [output/README.md](output/README.md))
- Legacy local runtime namespace: `output/telegram/runtime/` (историческое имя директории; не означает удалённое chat-управление)
- Thesis one-shot reports: `works/<slug>/thesis/reviews/<дата>-one-shot-report.(md|json)`
- Dissertation one-shot reports: `works/<slug>/thesis/reviews/<дата>-one-shot-dissertation-report.(md|json)`
- JSON one-shot traces в git не коммитятся по `.gitignore`.

## Supported Control Surface

Актуальное управление проектом — локальное и file-first:

- `workspace.toml` хранит список work-bundle и `default_work`;
- `python3 -m telegram_console.work_cli ...` запускает bootstrap, status, gates, workflow и export;
- shell launchers в `scripts/` являются тонкими обертками над `work_cli`;
- `output/runs/`, `output/docx/` и runtime-файлы являются производными артефактами.

Историческое имя Python-пакета `telegram_console` сохранено ради совместимости импортов, тестов и CLI entrypoints. Legacy compatibility layer больше не документируется как supported management surface.

## Разработка и тесты

```bash
pip install -e ".[dev]"
export PYTHONPATH=.
python3 -m unittest discover -s tests -q   # offline, детерминированный
ruff check telegram_console/ tests/
ruff format --check telegram_console/ tests/
```

Подробнее: [tests/README.md](tests/README.md). История перехода на модель `works/<slug>/`: [meta/migration-history.md](meta/migration-history.md). Targeted daemon/CLI reliability regression pack: [`tests/test_daemon_ops_integration.py`](tests/test_daemon_ops_integration.py), [`tests/test_daemon_smoke.py`](tests/test_daemon_smoke.py), [`tests/test_work_cli_autonomous.py`](tests/test_work_cli_autonomous.py), [`tests/test_work_cli_launchd.py`](tests/test_work_cli_launchd.py), [`tests/test_work_cli_runtime.py`](tests/test_work_cli_runtime.py).

## CI

- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) на каждый push / PR запускает `ruff check`, `ruff format --check`, `python3 -m unittest discover -s tests`, `skill-source-map audit` и smoke one-shot pipeline ([`tests/test_regression_harness.py`](tests/test_regression_harness.py)).
- Локальные хуки: [`.pre-commit-config.yaml`](.pre-commit-config.yaml) (`pip install pre-commit && pre-commit install`) — `ruff` + `ruff-format` на commit.
- Сильный repo-level claim вида `release-quality` / `fully final` допускается только на clean git snapshot при полностью зелёной verification matrix; процессный канон закреплён в [meta/master-protocol.md](meta/master-protocol.md).

## Ключевые документы

- [CHANGELOG.md](CHANGELOG.md) — короткий GitHub-facing changelog по релизным изменениям.
- [AGENTS.md](AGENTS.md) — индекс агентных ролей, launcher'ов и hard rules.
- [meta/master-protocol.md](meta/master-protocol.md) — единый workflow-регламент для всех lane.
- [meta/github-release-body-2026-04-21.md](meta/github-release-body-2026-04-21.md) — готовый короткий текст для GitHub Release body.
- [meta/autonomous-engine-unknowns-2026-04-19.md](meta/autonomous-engine-unknowns-2026-04-19.md) — границы автономного движка.
- [meta/engineering-audit-autonomous-workspace-2026-04-19.md](meta/engineering-audit-autonomous-workspace-2026-04-19.md) — полный инженерный аудит и план мигитации.
- [meta/runtime-reliability-audit-2026-04-20.md](meta/runtime-reliability-audit-2026-04-20.md) — отдельный аудит daemon/CLI/runtime reliability.
- [meta/runtime-reliability-backlog-2026-04-20.md](meta/runtime-reliability-backlog-2026-04-20.md) — backlog после runtime reliability wave.
- [meta/final-quality-audit-2026-04-20.md](meta/final-quality-audit-2026-04-20.md) — итоговый repo-wide quality audit и conservative repair summary.
- [meta/system-project-master-remediation-2026-04-20.md](meta/system-project-master-remediation-2026-04-20.md) — closeout repo-only remediation wave поверх master audit.
- [meta/repo-clean-snapshot-closeout-2026-04-21.md](meta/repo-clean-snapshot-closeout-2026-04-21.md) — канонический final closeout для repo/platform layer на clean snapshot.
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
