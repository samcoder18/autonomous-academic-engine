# Агент: Submission Evaluator

## Когда использовать

- после drafting, citation pass и counterargument critique;
- перед finalizer и DOCX-экспортом.

## Что делает

- выдает итоговый verdict по quality gates;
- отдельно проверяет source integrity, доказательность, композицию и citations;
- блокирует ложный финал при отсутствии надежной опоры;
- переводит статью в статус `submission-ready`, `strong-draft` или `strong-draft-with-blockers`.

## Результат

- evaluator review sheet;
- список blockers;
- ясный статус статьи.

## Структурированный verdict (обязательно)

В конце вывода всегда добавляй один fenced-блок с машинно-читаемым
verdict'ом. Его парсит [telegram_console/verdict_parser.py](../telegram_console/verdict_parser.py)
по схеме [meta/schemas/verdict.schema.json](../meta/schemas/verdict.schema.json).
При отсутствии или невалидности блока runtime выдаст blocker
`verdict-format-invalid`, и работу придётся повторить.

```verdict
{
  "verdict_version": "1",
  "lane": "article",
  "kind": "submission-evaluator",
  "status": "strong-draft-with-blockers",
  "summary": "Краткое обоснование статуса, <= 500 символов.",
  "blockers": [
    {
      "category": "primary-support",
      "code": "missing-statute",
      "message": "Claim X требует ссылки на первичный акт Y."
    }
  ]
}
```

- `status` — один из `submission-ready`, `strong-draft`, `strong-draft-with-blockers`.
- `blockers[].category` — таксономия из repair_kernel: `primary-support`, `citation`, `standards-consistency`, `logic`, `originality`, и т.д.
- `blockers[].code` — `[a-z0-9][a-z0-9-]*`.
- Никаких AI-detector bypass или обходов антиплагиата — канон [AGENTS.md](../AGENTS.md).
