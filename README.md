# Система legal-academic workflow

Это рабочее пространство для юридического академического движка с двумя lane:

- `thesis` - дипломный контур с каноном проекта и секционной рукописью;
- `article` - отдельный контур для автономной сборки юридических академических статей.

## Активные документы

- [AGENTS.md](/Users/albina/дипломная/AGENTS.md) - оркестрация ролей и порядок работы.
- [meta/project-canon.md](/Users/albina/дипломная/meta/project-canon.md) - только утвержденные решения.
- [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md) - единый рабочий регламент без повторов.
- [manuscript/sections](/Users/albina/дипломная/manuscript/sections) - канонические текстовые секции диплома.
- [articles/](/Users/albina/дипломная/articles) - article bundle для академических статей.
- [meta/standards](/Users/albina/дипломная/meta/standards) - raw и normalized publication profiles.

## Структура папок

- `agents/` - специализированные роли для структуры, ресерча, верификации, письма и редактуры.
- `articles/` - brief, evidence pack, claim map, draft, review, final и run trace для article lane.
- `meta/` - канон проекта, протокол и требования.
- `templates/` - шаблоны для пакетов источников, брифов, проверки и синхронизации.
- `sources/` - пакеты источников и рабочие заметки.
- `chapters/` - материалы по отдельным главам.
- `manuscript/` - секции диплома и сборочный файл.
- `scripts/` - служебные скрипты сборки, проверки объема и экспорта.
- `output/` - экспортированные версии документа.
- `reviews/` - листы проверки.
- `sync/` - короткие рабочие синхронизации.

## Как работать

### Thesis lane

1. Свериться с [meta/project-canon.md](/Users/albina/дипломная/meta/project-canon.md).
2. Взять порядок шагов из [AGENTS.md](/Users/albina/дипломная/AGENTS.md) и [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md).
3. Собирать источники по [templates/source-package-passport.md](/Users/albina/дипломная/templates/source-package-passport.md).
4. Планировать главу через [templates/chapter-brief.md](/Users/albina/дипломная/templates/chapter-brief.md).
5. Проверять фрагменты по [templates/chapter-review-sheet.md](/Users/albina/дипломная/templates/chapter-review-sheet.md).
6. Вносить текст только в [manuscript/sections](/Users/albina/дипломная/manuscript/sections), затем пересобирать документ через [scripts/assemble_thesis.sh](/Users/albina/дипломная/scripts/assemble_thesis.sh).
7. Для Word-версии со сносками использовать [scripts/export_docx.sh](/Users/albina/дипломная/scripts/export_docx.sh).

### Article lane

1. Открыть publication profile в [meta/standards/normalized](/Users/albina/дипломная/meta/standards/normalized).
2. Нормализовать тему в brief по [templates/article-brief.md](/Users/albina/дипломная/templates/article-brief.md).
3. Собрать evidence pack по [templates/evidence-pack.md](/Users/albina/дипломная/templates/evidence-pack.md).
4. Построить claim map по [templates/claim-map.md](/Users/albina/дипломная/templates/claim-map.md).
5. Прогнать evaluator review по [templates/article-review-sheet.md](/Users/albina/дипломная/templates/article-review-sheet.md).
6. Финализировать статью, checklist и DOCX через article lane и [scripts/export_academic_docx.sh](/Users/albina/дипломная/scripts/export_academic_docx.sh).

## Готовые запуски

Основные launcher:

- [scripts/codex_academic.sh](/Users/albina/дипломная/scripts/codex_academic.sh) - общий legal-academic launcher для article lane и thesis proxy.
- [scripts/codex_thesis.sh](/Users/albina/дипломная/scripts/codex_thesis.sh) - thesis-only launcher.

Примеры:

- `bash scripts/codex_academic.sh article --topic "Конституционные пределы биометрической идентификации"`
- `bash scripts/codex_academic.sh article --brief articles/briefs/biometrics.md`
- `bash scripts/codex_academic.sh review articles/drafts/biometrics.md`
- `bash scripts/codex_academic.sh repair articles/reviews/biometrics.md`
- `bash scripts/codex_academic.sh thesis full-cycle manuscript/sections/03-chapter-2.md`
- `bash scripts/codex_thesis.sh full-cycle manuscript/sections/03-chapter-2.md`
- `bash scripts/codex_thesis.sh source-pack sources/02-chapter-2-regulation.md --notes "Собери пакет по ЕБС и практике 2025-2026"`
- `bash scripts/codex_thesis.sh verify manuscript/sections/03-chapter-2.md --notes "Особенно проверь 152-ФЗ и 572-ФЗ"`
- `bash scripts/codex_thesis.sh write-section manuscript/sections/04-chapter-3.md --notes chapters/03-chapter-3-brief.md`
- `bash scripts/codex_thesis.sh review-section manuscript/sections/02-chapter-1.md`
- `bash scripts/codex_thesis.sh style-pass manuscript/sections/02-chapter-1.md`

Что делает academic launcher:

- подставляет нужный academic или thesis skill по сценарию;
- запускает `codex exec` из корня проекта;
- отделяет thesis lane от article lane и не смешивает их артефакты;
- для article lane строит managed bundle в [articles/](/Users/albina/дипломная/articles);
- включает web search по умолчанию там, где критична актуальность первоисточников;
- сохраняет финальные сообщения и manifest article-run в [articles/runs](/Users/albina/дипломная/articles/runs), а thesis-run в [output/codex](/Users/albina/дипломная/output/codex).

## Базовый принцип

Проект не используется для обхода антиплагиата, детекторов ИИ или маскировки заимствований. Качество повышается через сильный ресерч, проверку первоисточников, evidence trace, evaluator verdict и естественный академический стиль.
