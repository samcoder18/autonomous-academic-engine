# Экспорт

Здесь хранятся производные файлы, которые собираются из основного Markdown-черновика.

Планируемое использование:

- `output/docx/` — Word-версии thesis lane и article lane (создаются `scripts/export_docx.sh` и `scripts/export_academic_docx.sh`; требуется установленный [Pandoc](https://pandoc.org));
- `output/codex/` — финальные сообщения автономных запусков через `codex exec`;
- `output/runs/<workflow-id>/` — локальный `workflow-run/v1`: `workflow.json`, `events.jsonl`, `gates.json`, sandbox, `roles/*/{request,result}.json` и `promotion.json`;
- при необходимости позже можно добавить `output/pdf/`.

## Политика версионирования

Файлы `*.docx` в `output/docx/` **не коммитятся**: они воспроизводимы из Markdown в `works/<slug>/`. Исключение — сознательный снимок под релиз (тогда временно уберите соответствующий шаблон из `.gitignore` или используйте Git LFS).

Run traces в `output/runs/` по умолчанию локальны; при необходимости зафиксируйте отдельным процессом (или добавьте правило в `.gitignore`).
