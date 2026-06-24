# Экспорт

Здесь хранятся производные файлы, которые собираются из основного Markdown-черновика.

Планируемое использование:

- `output/docx/` — Word-версии thesis lane и article lane (создаются `scripts/export_docx.sh` и `scripts/export_academic_docx.sh`; требуется установленный [Pandoc](https://pandoc.org));
- `output/codex/` — финальные сообщения автономных запусков через `codex exec`;
- `output/runs/<workflow-id>/` — локальный `workflow-run/v1`: `workflow.json`, `events.jsonl`, `gates.json`, sandbox, `roles/*/{request,result}.json` и `promotion.json`;
- `output/runtime/` — локальное состояние активных запусков и автономного daemon, не коммитится;
- при необходимости позже можно добавить `output/pdf/`.

## Политика версионирования

`output/docx/` — generated output и не является источником истины. DOCX, PDF,
PNG, contact sheets и render directories под `output/docx/` не коммитятся:
канонический текст живет в `works/<slug>/`, а визуальные проверки должны
перегенерироваться локально из канонического Markdown.

Если для аудита нужен render snapshot, фиксируйте в Markdown-review путь к
каноническому manuscript, дату генерации, команду и краткий результат проверки.
Бинарный snapshot храните вне git или через отдельное явное release/LFS-решение.

Повторяемый код рендера не должен жить в `output/docx/`; shared helper belongs
under `scripts/` or `academic_engine/`.

Run traces в `output/runs/` по умолчанию локальны; при необходимости зафиксируйте
отдельным процессом (или добавьте правило в `.gitignore`).
