# Academic Article Lane

В этой папке живет отдельный контур для юридических академических статей.

Он не заменяет thesis lane и не пишет в `manuscript/sections`.

## Структура

- `briefs/` - нормализованные article brief и publication contract.
- `evidence/` - evidence-pack по теме, тезисам и источникам.
- `claim-maps/` - карта тезисов, покрытия и пробелов.
- `drafts/` - рабочие article draft до финализации.
- `reviews/` - evaluator и critique sheet по статье.
- `final/` - финальный Markdown и checklist по статье.
- `runs/` - финальные сообщения и manifest автономных article-run.

## Правила

- article lane пишет только в `articles/` и производные выходы вроде `output/docx/articles/`;
- thesis lane пишет только в thesis-артефакты;
- для статьи source of truth - это `brief`, `evidence pack`, `claim map`, `review` и `final` по одному `slug`.
