# Агентная система workspace

Этот файл задает оркестрацию legal-academic engine в корне этого git-репозитория.
Он описывает reusable workflow и не заменяет канон конкретной работы.

## Источники истины

- Производные артефакты сборки (`output/docx/*.docx`, при необходимости трассы в `output/runs/`) не являются источником истины для текста работы и по умолчанию не версионируются в git; канонический текст живёт в Markdown под `works/<slug>/`. Подробнее: [output/README.md](output/README.md).
- Машинно-читаемая конфигурация workspace хранится в [workspace.toml](workspace.toml).
- Рабочий регламент для всех lane хранится только в [meta/master-protocol.md](meta/master-protocol.md).
- Канон конкретной работы хранится только в `works/<slug>/work-canon.md`.
- Конфигурация конкретной работы хранится только в `works/<slug>/work.toml`.
- Внешние требования и publication profiles живут в [meta/standards](meta/standards) в модели `raw + normalized`.
- Сборочные thesis-файлы и DOCX не редактируются вручную как основные документы.

По умолчанию активная работа workspace: `starter-work`.
CLI и Telegram runtime могут переключать `active work`.

## Агентные роли

### Thesis lane

- [agents/structure-architect.md](agents/structure-architect.md) - строит логику главы, тезисы и доказательную рамку.
- [agents/research-synthesizer.md](agents/research-synthesizer.md) - собирает пакет источников и выделяет полезные опоры.
- [agents/source-verifier.md](agents/source-verifier.md) - проверяет надежность, актуальность и первичность источников.
- [agents/draft-writer.md](agents/draft-writer.md) - пишет академический черновик на основе проверенных тезисов.
- [agents/citation-checker.md](agents/citation-checker.md) - проверяет сноски, силу атрибуции и достаточность первичной опоры.
- [agents/argument-critic.md](agents/argument-critic.md) - ищет логические дыры, чрезмерные обобщения и слабые выводы.
- [agents/style-editor.md](agents/style-editor.md) - выравнивает стиль, ритм и академическую естественность без имитации "обхода" проверок.

### Article lane

- [agents/academic-intake.md](agents/academic-intake.md) - превращает тему в article brief и publication contract.
- [agents/academic-source-acquirer.md](agents/academic-source-acquirer.md) - автономно собирает первичный и академический корпус источников.
- [agents/academic-source-verifier.md](agents/academic-source-verifier.md) - проверяет первичность, дату и точную поддержку тезисов.
- [agents/academic-evidence-cartographer.md](agents/academic-evidence-cartographer.md) - строит claim map и coverage map.
- [agents/academic-draft-writer.md](agents/academic-draft-writer.md) - пишет статью только по verified evidence.
- [agents/academic-citation-checker.md](agents/academic-citation-checker.md) - проверяет footnotes и citation safety.
- [agents/academic-counterargument-critic.md](agents/academic-counterargument-critic.md) - ищет альтернативные позиции и overclaim.
- [agents/academic-submission-evaluator.md](agents/academic-submission-evaluator.md) - выдает итоговый verdict по quality gates.
- [agents/academic-repair-orchestrator.md](agents/academic-repair-orchestrator.md) - проводит ограниченный repair loop.
- [agents/academic-finalizer.md](agents/academic-finalizer.md) - собирает final bundle, checklist и DOCX.

## Формальные skills Codex

Для более формального и автономного запуска эта логика вынесена в skills Codex.
Repo-first mapping между skills и role docs хранится в [meta/skill-source-map.toml](meta/skill-source-map.toml).
`agents/*.md` остаются source of truth для reusable role behavior; внешний `SKILL.md` должен явно ссылаться на соответствующий repo-side источник и синхронизироваться с ним целиком, а не только через отдельную ссылку.

### Thesis skills

- `$thesis-workflow-orchestrator`
- `$thesis-structure-architect`
- `$thesis-research-synthesizer`
- `$thesis-source-verifier`
- `$thesis-draft-writer`
- `$thesis-citation-checker`
- `$thesis-argument-critic`
- `$thesis-style-editor`

### Academic article skills

- `$academic-workflow-orchestrator`
- `$academic-intake`
- `$academic-source-acquirer`
- `$academic-source-verifier`
- `$academic-evidence-cartographer`
- `$academic-draft-writer`
- `$academic-citation-checker`
- `$academic-counterargument-critic`
- `$academic-submission-evaluator`
- `$academic-repair-orchestrator`
- `$academic-finalizer`

## Launcher

- [scripts/codex_thesis.sh](scripts/codex_thesis.sh) - thesis launcher с поддержкой `--work`.
- [scripts/codex_academic.sh](scripts/codex_academic.sh) - article launcher и thesis proxy с поддержкой `--work`.
- [scripts/assemble_thesis.sh](scripts/assemble_thesis.sh) - пересобирает thesis manuscript выбранной работы.
- [scripts/export_docx.sh](scripts/export_docx.sh) - экспортирует thesis DOCX выбранной работы.
- [scripts/export_academic_docx.sh](scripts/export_academic_docx.sh) - экспортирует article DOCX выбранной работы.
- `python3 -m telegram_console.work_cli build-vkr-frontmatter` - генерирует title-page / abstract / keywords / task-sheet для VKR по `works/<slug>/thesis/metadata.toml`.
- `python3 -m telegram_console.work_cli build-dissertation-artifacts` - генерирует `author-abstract.md` и `defense-checklist.md` для dissertation contour по `works/<slug>/thesis/dissertation/metadata.toml`; для candidate contour вызывается после maps, review sequence и author-position drafting, а `publication-claim-matrix.md` ведется как отдельный обязательный scaffold artifact.
- `python3 -m telegram_console.work_cli one-shot-thesis` - запускает автономные machine-driven гейты (frontmatter, ГОСТ, DOCX, originality, work-type, strict thesis quality contract для managed thesis bundle) и пишет отчёт в `works/<slug>/thesis/reviews/`. Регламент описан в §11 [master-protocol.md](meta/master-protocol.md).
- `python3 -m telegram_console.work_cli one-shot-dissertation` - запускает dissertation-specific machine-driven гейты (artifacts, maps, reviews, publication evidence, publication-claim matrix, length, ГОСТ, DOCX, originality) и пишет отчёт в `works/<slug>/thesis/reviews/`.
- `python3 -m telegram_console.work_cli autonomous daemon run [--stuck-after-minutes N]` - запускает long-running автономный цикл с ops-alerts и resource-guards. Операционный канал описан в §11.2 [master-protocol.md](meta/master-protocol.md).
- `python3 -m telegram_console.work_cli work-status [--json]` - показывает индекс сигналов и следующий безопасный шаг по активной работе.
- `python3 -m telegram_console.work_cli work init <slug> --artifact-type <type> --title "..." [--topic "..."] [--lanes thesis,article] [--set-default]` - создает новый `works/<slug>/` bundle (`work.toml`, `work-canon.md`, обязательные подпапки lane) и регистрирует его в `workspace.toml`. Новые works полностью изолированы от существующих.

## Навигация

- [README.md](README.md) - пользовательский обзор, quickstart, архитектура и CI.
- [meta/master-protocol.md](meta/master-protocol.md) - единый процессный регламент (включая §11 автономный движок, §11.1 repo-level release claims и §11.2 ops-канал).
- [meta/autonomous-engine-unknowns-2026-04-19.md](meta/autonomous-engine-unknowns-2026-04-19.md) - прагматические границы автономного движка.
- [meta/engineering-audit-autonomous-workspace-2026-04-19.md](meta/engineering-audit-autonomous-workspace-2026-04-19.md) - инженерный аудит workspace и план мигитации.
- [meta/runtime-reliability-audit-2026-04-20.md](meta/runtime-reliability-audit-2026-04-20.md) - аудит daemon/CLI/runtime reliability после hardening wave.
- [meta/runtime-reliability-backlog-2026-04-20.md](meta/runtime-reliability-backlog-2026-04-20.md) - backlog runtime reliability после фиксов этой волны.
- [meta/candidate-polish-audit-2026-04-20.md](meta/candidate-polish-audit-2026-04-20.md) - audit-отчёт по candidate contour перед doctor-phase.
- [meta/candidate-polish-backlog-2026-04-20.md](meta/candidate-polish-backlog-2026-04-20.md) - backlog с разделением на `must-fix before doctor`, `candidate-safe defer`, `doctor-phase only`.
- [meta/final-quality-audit-2026-04-20.md](meta/final-quality-audit-2026-04-20.md) - итоговый repo-wide quality audit, findings и conservative repair summary.
- [meta/repo-clean-snapshot-closeout-2026-04-21.md](meta/repo-clean-snapshot-closeout-2026-04-21.md) - актуальный clean-snapshot closeout для repo/platform layer.

## Процесс

- Детальный процесс для thesis lane и article lane хранится только в [meta/master-protocol.md](meta/master-protocol.md).
- При изменении регламента обновляй master protocol как единственный процессный source of truth, а здесь оставляй только роли, launcher index и hard rules.

## Жесткие правила

- Не использовать проект для обхода антиплагиата, ИИ-детекторов или сокрытия заимствований. Интеграции с внешними AI-детекторами или anti-plagiarism SaaS запрещены.
- Добиваться оригинальности через самостоятельный анализ, корректные ссылки, сравнение позиций и собственные выводы.
- Для динамичных правовых норм, судебной практики и статистики всегда делать повторную проверку на дату написания фрагмента.
- Любое сильное утверждение должно иметь либо проверенную первичную опору, либо быть прямо помечено как аналитический вывод.
- Для article lane не использовать неофициальные базы как финальную authority; они допустимы только как навигация к первоисточнику.
- При отсутствии достаточной первичной опоры не заявлять `submission-ready`, а честно понижать статус результата до `strong-draft-with-blockers` с полным списком блокеров.
- Source connectors по умолчанию работают в stub-режиме; live-режим включается только явными `SOURCES_*_ENABLE=1` флагами и уважает rate-limits целевых регуляторов (`publication.pravo.gov.ru`, `sudact.ru`, `cbr.ru`, `elibrary.ru`, `cyberleninka`, `semantic_scholar`, `vak.gov`).
