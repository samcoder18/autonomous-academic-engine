# Legal-Academic Workspace

Это reusable workspace для подготовки юридических академических текстов.
Он поддерживает несколько `work bundle` внутри одного репозитория и разделяет reusable engine от канона конкретной работы.

## Установка и зависимости

- **Python** 3.11 или новее (`tomllib` в стандартной библиотеке).
- Установка editable-пакета (опционально, для явного `PYTHONPATH` и инструментов разработки):

  ```bash
  pip install -e ".[dev]"
  ```

  Иначе достаточно добавить корень репозитория в `PYTHONPATH`, как делают скрипты в `scripts/`.
- **Pandoc** — внешняя утилита для экспорта DOCX (`export_docx.sh`, `export_academic_docx.sh`), не ставится через pip.
- Рантайм Telegram читает секреты из переменных окружения (см. `telegram_console/config.py`); файл `.env` не коммитится.

История перехода на модель `works/<slug>/`: [meta/migration-history.md](meta/migration-history.md).

## Основные уровни

- `workspace.toml` - корневая конфигурация workspace, `default_work`, output paths и список работ.
- `works/<slug>/work.toml` - машинно-читаемая конфигурация конкретной работы.
- `works/<slug>/work-canon.md` - утвержденные решения по конкретной работе.
- `agents/` - reusable роли для structure, research, verification, drafting, critique и style.
- `templates/` - reusable шаблоны для source packs, briefs, reviews, claim maps и checklists.
- `meta/master-protocol.md` - общий рабочий регламент.
- `meta/standards/` - raw и normalized publication profiles, включая `registry.toml` для V2-A standards intake track.
- `scripts/` - launcher, сборка и экспорт.
- `telegram_console/` - Telegram runtime, мультипроектный реестр и work-aware orchestration.

## Текущая работа по умолчанию

- `biometrics-vkr`
- Путь: [works/biometrics-vkr](/Users/albina/дипломная/works/biometrics-vkr)
- Канон: [work-canon.md](/Users/albina/дипломная/works/biometrics-vkr/work-canon.md)

## Структура work bundle

### Thesis lane

- `works/<slug>/thesis/chapters/`
- `works/<slug>/thesis/sources/`
- `works/<slug>/thesis/ledgers/`
- `works/<slug>/thesis/manuscript/sections/`
- `works/<slug>/thesis/reviews/`
- `works/<slug>/thesis/sync/`

### Article lane

- `works/<slug>/articles/briefs/`
- `works/<slug>/articles/evidence/`
- `works/<slug>/articles/claim-maps/`
- `works/<slug>/articles/drafts/`
- `works/<slug>/articles/reviews/`
- `works/<slug>/articles/final/`

## Операционные ориентиры

- Детальный workflow-reglament для thesis lane и article lane живет только в [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md).
- Индекс ролей, launcher links и hard rules живут в [AGENTS.md](/Users/albina/дипломная/AGENTS.md).
- Канонический thesis-текст редактируется только в `works/<slug>/thesis/manuscript/sections/`.
- Article bundle ведется только в `works/<slug>/articles/`.

## Launcher

Основные команды:

- `bash scripts/codex_thesis.sh full-cycle works/biometrics-vkr/thesis/manuscript/sections/03-chapter-2.md`
- `bash scripts/codex_thesis.sh full-cycle manuscript/sections/03-chapter-2.md`
- `bash scripts/codex_thesis.sh write-section thesis/manuscript/sections/04-chapter-3.md --work biometrics-vkr`
- `bash scripts/codex_thesis.sh source-pack sources/02-chapter-2-regulation.md --work biometrics-vkr`
- `bash scripts/codex_academic.sh article --topic "Правовые пределы цифровой идентификации" --work biometrics-vkr`
- `bash scripts/codex_academic.sh review works/biometrics-vkr/articles/drafts/example.md --work biometrics-vkr`
- `bash scripts/codex_academic.sh finalize works/biometrics-vkr/articles/final/example.md --work biometrics-vkr`

Совместимость:

- Старые thesis-style target paths вроде `manuscript/sections/...`, `chapters/...`, `sources/...` и article paths вроде `articles/drafts/...` автоматически резолвятся в `default_work`.
- Если `--work` не передан, launcher сначала пытается вывести work из пути target, иначе использует `default_work` из `workspace.toml`.

## Output paths

- Run trace: `output/runs/<work>/<lane>/`
- DOCX: `output/docx/<work>/` (генерируются локально; в git не версионируются, см. [output/README.md](output/README.md))
- Telegram runtime: `output/telegram/runtime/`

## Legacy-пути и пустые каталоги в корне

Launcher резолвит старые относительные пути (`manuscript/sections/...`, корневой `articles/...`) в `default_work` из `workspace.toml`. Пустые каталоги `chapters/`, `manuscript/`, `sources/` и т.п. в корне могут появляться локально как заглушки — это не канон; канонический контент лежит только в `works/<slug>/`. В свежем клоне этих папок может не быть (git не хранит пустые директории).

## Standards intake

Доступные V2-A команды:

- `python3 -m telegram_console.work_cli standards-intake <profile-id>`
- `python3 -m telegram_console.work_cli standards-refresh <profile-id>`
- `python3 -m telegram_console.work_cli standards-status [profile-id]`
- `python3 -m telegram_console.work_cli work-status [--json]`

`work-status` показывает индекс сигналов и следующий безопасный шаг; он не заменяет верификацию источников, citation pass или repair planner.

## Telegram console

Telegram console теперь умеет:

- работать с мультипроектным реестром;
- держать активный проект и активную работу внутри проекта;
- переключать `active work` отдельно от `active project`;
- использовать work-aware export и status;
- принудительно пересобирать полный chat-context после переключения работы.

Добавление проекта:

- `python3 scripts/telegram_console.py project add --title "Юридический workspace" --root "/абсолютный/путь/к/проекту"`

## Принцип качества

Workspace не используется для обхода антиплагиата, ИИ-детекторов и маскировки заимствований.
Качество достигается через strong research, verified primary sources, evidence trace, honest status downgrade и естественный академический стиль.
