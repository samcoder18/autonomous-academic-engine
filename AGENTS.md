# Агентная система workspace

Этот файл задает оркестрацию legal-academic engine в `/Users/albina/дипломная`.
Он описывает reusable workflow и не заменяет канон конкретной работы.

## Источники истины

- Производные артефакты сборки (`output/docx/*.docx`, при необходимости трассы в `output/runs/`) не являются источником истины для текста работы и по умолчанию не версионируются в git; канонический текст живёт в Markdown под `works/<slug>/`. Подробнее: [output/README.md](/Users/albina/дипломная/output/README.md).
- Машинно-читаемая конфигурация workspace хранится в [workspace.toml](/Users/albina/дипломная/workspace.toml).
- Рабочий регламент для всех lane хранится только в [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md).
- Канон конкретной работы хранится только в `works/<slug>/work-canon.md`.
- Конфигурация конкретной работы хранится только в `works/<slug>/work.toml`.
- Внешние требования и publication profiles живут в [meta/standards](/Users/albina/дипломная/meta/standards) в модели `raw + normalized`.
- Сборочные thesis-файлы и DOCX не редактируются вручную как основные документы.

По умолчанию активная работа workspace: `biometrics-vkr`.
CLI и Telegram runtime могут переключать `active work`.

## Агентные роли

### Thesis lane

- [agents/structure-architect.md](/Users/albina/дипломная/agents/structure-architect.md) - строит логику главы, тезисы и доказательную рамку.
- [agents/research-synthesizer.md](/Users/albina/дипломная/agents/research-synthesizer.md) - собирает пакет источников и выделяет полезные опоры.
- [agents/source-verifier.md](/Users/albina/дипломная/agents/source-verifier.md) - проверяет надежность, актуальность и первичность источников.
- [agents/draft-writer.md](/Users/albina/дипломная/agents/draft-writer.md) - пишет академический черновик на основе проверенных тезисов.
- [agents/citation-checker.md](/Users/albina/дипломная/agents/citation-checker.md) - проверяет сноски, силу атрибуции и достаточность первичной опоры.
- [agents/argument-critic.md](/Users/albina/дипломная/agents/argument-critic.md) - ищет логические дыры, чрезмерные обобщения и слабые выводы.
- [agents/style-editor.md](/Users/albina/дипломная/agents/style-editor.md) - выравнивает стиль, ритм и академическую естественность без имитации "обхода" проверок.

### Article lane

- [agents/academic-intake.md](/Users/albina/дипломная/agents/academic-intake.md) - превращает тему в article brief и publication contract.
- [agents/academic-source-acquirer.md](/Users/albina/дипломная/agents/academic-source-acquirer.md) - автономно собирает первичный и академический корпус источников.
- [agents/academic-source-verifier.md](/Users/albina/дипломная/agents/academic-source-verifier.md) - проверяет первичность, дату и точную поддержку тезисов.
- [agents/academic-evidence-cartographer.md](/Users/albina/дипломная/agents/academic-evidence-cartographer.md) - строит claim map и coverage map.
- [agents/academic-draft-writer.md](/Users/albina/дипломная/agents/academic-draft-writer.md) - пишет статью только по verified evidence.
- [agents/academic-citation-checker.md](/Users/albina/дипломная/agents/academic-citation-checker.md) - проверяет footnotes и citation safety.
- [agents/academic-counterargument-critic.md](/Users/albina/дипломная/agents/academic-counterargument-critic.md) - ищет альтернативные позиции и overclaim.
- [agents/academic-submission-evaluator.md](/Users/albina/дипломная/agents/academic-submission-evaluator.md) - выдает итоговый verdict по quality gates.
- [agents/academic-repair-orchestrator.md](/Users/albina/дипломная/agents/academic-repair-orchestrator.md) - проводит ограниченный repair loop.
- [agents/academic-finalizer.md](/Users/albina/дипломная/agents/academic-finalizer.md) - собирает final bundle, checklist и DOCX.

## Формальные skills Codex

Для более формального и автономного запуска эта логика вынесена в skills Codex.
Repo-first mapping между skills и role docs хранится в [meta/skill-source-map.toml](/Users/albina/дипломная/meta/skill-source-map.toml).
`agents/*.md` остаются source of truth для reusable role behavior; внешний `SKILL.md` должен явно ссылаться на соответствующий repo-side источник.

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

- [scripts/codex_thesis.sh](/Users/albina/дипломная/scripts/codex_thesis.sh) - thesis launcher с поддержкой `--work`.
- [scripts/codex_academic.sh](/Users/albina/дипломная/scripts/codex_academic.sh) - article launcher и thesis proxy с поддержкой `--work`.
- [scripts/assemble_thesis.sh](/Users/albina/дипломная/scripts/assemble_thesis.sh) - пересобирает thesis manuscript выбранной работы.
- [scripts/export_docx.sh](/Users/albina/дипломная/scripts/export_docx.sh) - экспортирует thesis DOCX выбранной работы.
- [scripts/export_academic_docx.sh](/Users/albina/дипломная/scripts/export_academic_docx.sh) - экспортирует article DOCX выбранной работы.

## Процесс

- Детальный процесс для thesis lane и article lane хранится только в [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md).
- При изменении регламента обновляй master protocol как единственный процессный source of truth, а здесь оставляй только роли, launcher index и hard rules.

## Жесткие правила

- Не использовать проект для обхода антиплагиата, ИИ-детекторов или сокрытия заимствований.
- Добиваться оригинальности через самостоятельный анализ, корректные ссылки, сравнение позиций и собственные выводы.
- Для динамичных правовых норм, судебной практики и статистики всегда делать повторную проверку на дату написания фрагмента.
- Любое сильное утверждение должно иметь либо проверенную первичную опору, либо быть прямо помечено как аналитический вывод.
- Для article lane не использовать неофициальные базы как финальную authority; они допустимы только как навигация к первоисточнику.
- При отсутствии достаточной первичной опоры не заявлять `submission-ready`, а честно понижать статус результата.
