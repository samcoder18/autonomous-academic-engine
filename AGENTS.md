# Агентная система проекта

Этот файл задает рабочую оркестрацию проекта. Он не заменяет содержание диплома и не изменяет канон темы; его задача - держать процесс компактным, проверяемым и без дублирования инструкций.

## Что является источником истины

- Устойчивые решения по thesis lane хранятся только в [meta/project-canon.md](/Users/albina/дипломная/meta/project-canon.md).
- Рабочий регламент для всех lane хранится только в [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md).
- Канонический текст диплома хранится только в [manuscript/sections](/Users/albina/дипломная/manuscript/sections).
- Канонический текст article lane хранится только в [articles/final](/Users/albina/дипломная/articles/final), а доказательная база - в `articles/briefs`, `articles/evidence`, `articles/claim-maps` и `articles/reviews`.
- Внешние требования и publication profiles живут в [meta/standards](/Users/albina/дипломная/meta/standards) в модели `raw + normalized`.
- [manuscript/full-draft.md](/Users/albina/дипломная/manuscript/full-draft.md) - сборочный файл thesis lane; вручную как основной документ не редактируется.

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

Для более формального и автономного запуска эта же логика вынесена в skills Codex.

### Thesis skills

- `$thesis-workflow-orchestrator` - главный skill для полного цикла.
- `$thesis-structure-architect` - структура и доказательная логика.
- `$thesis-research-synthesizer` - сжатие корпуса источников в рабочий пакет.
- `$thesis-source-verifier` - проверка актуальности и достоверности опор.
- `$thesis-draft-writer` - написание разделов по проверенной базе.
- `$thesis-citation-checker` - проверка ссылок, сносок и силы атрибуции.
- `$thesis-argument-critic` - критический проход по аргументации.
- `$thesis-style-editor` - финальный проход по естественности и академическому стилю.

### Academic article skills

- `$academic-workflow-orchestrator` - главный skill для article lane.
- `$academic-intake` - нормализует тему в article brief.
- `$academic-source-acquirer` - собирает первичный evidence-pack.
- `$academic-source-verifier` - верифицирует даты, редакции и поддержку тезисов.
- `$academic-evidence-cartographer` - строит claim map.
- `$academic-draft-writer` - пишет article draft по verified evidence.
- `$academic-citation-checker` - проверяет footnotes и attribution.
- `$academic-counterargument-critic` - проверяет контраргументы и пределы вывода.
- `$academic-submission-evaluator` - выдает verdict `submission-ready` / `strong-draft` / `strong-draft-with-blockers`.
- `$academic-repair-orchestrator` - проводит repair по findings evaluator-а.
- `$academic-finalizer` - собирает final markdown, checklist и DOCX.

Эти skills установлены в `/Users/albina/.codex/skills` и привязаны именно к этому проекту.
Для thesis lane вход по умолчанию: `$thesis-workflow-orchestrator`.
Для article lane вход по умолчанию: `$academic-workflow-orchestrator`.

## Launcher

Для article lane используйте [scripts/codex_academic.sh](/Users/albina/дипломная/scripts/codex_academic.sh). Он подставляет нужный academic skill, строит managed article bundle, держит strict primary-source policy, запускает evaluator/repair/finalizer и сохраняет run trace в `articles/runs/`.

Для thesis lane продолжает работать [scripts/codex_thesis.sh](/Users/albina/дипломная/scripts/codex_thesis.sh). Он остается специализированным launcher для дипломного контура.

## Базовый порядок работы

### Thesis lane

1. Сначала свериться с каноном проекта.
2. Построить структуру фрагмента.
3. Собрать и сжать пакет источников.
4. Отдельно проверить источники и актуальность норм.
5. Написать черновик только по проверенным опорам.
6. Проверить ссылки, сноски и силу атрибуции.
7. Провести критический проход по аргументации.
8. Выполнить финальную редактуру стиля и формулировок.

### Article lane

1. Нормализовать тему в article brief.
2. Собрать первичный evidence-pack.
3. Отдельно проверить первичность, даты и точную поддержку тезисов.
4. Построить claim map и coverage map.
5. Написать draft только по verified evidence.
6. Проверить footnotes и citation safety.
7. Проверить контраргументы и overclaim.
8. Получить evaluator verdict.
9. При необходимости провести ограниченный repair loop.
10. Собрать final markdown, checklist и DOCX без ложного заявления формальной готовности.

Пропуск шага допустим только когда это явно безопасно и зафиксировано в рабочем следе соответствующего lane.

## Жесткие правила

- Не использовать проект для обхода антиплагиата, ИИ-детекторов или сокрытия заимствований.
- Добиваться оригинальности через самостоятельный анализ, корректные ссылки, сравнение позиций и собственные выводы.
- Для динамичных правовых норм, судебной практики и статистики всегда делать повторную проверку на дату написания фрагмента.
- Любое сильное утверждение должно иметь либо проверенную первичную опору, либо быть прямо помечено как аналитический вывод.
- Для article lane не использовать неофициальные базы как финальную authority; они допустимы только как навигация к первоисточнику.
- При отсутствии достаточной первичной опоры не заявлять `submission-ready`, а честно понижать статус результата.
