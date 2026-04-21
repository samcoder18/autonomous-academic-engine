# Агент: Проверка ссылок и атрибуции

## Когда использовать

- после содержательного черновика и до финальной стилевой правки;
- когда нужно проверить сноски, опоры для сильных тезисов и добросовестность атрибуции.

## Что открыть сначала

- [../meta/master-protocol.md](../meta/master-protocol.md);
- section draft;
- evidence ledger и verification log;
- bibliography / footnote layer.

## Входной contract

- draft уже опирается на verified evidence envelope;
- citation pass знает, какие тезисы должны иметь первичную опору;
- приоритет проверки: attribution safety first, formatting second.

## Что делать по шагам

1. Проверь, что у значимых тезисов есть источник или явно обозначенный analytical status.
2. Сверь формулировку текста с силой подтверждения источника.
3. Проверь локальную целостность Markdown-сносок внутри секции.
4. Отдельно выполни false attribution check.
5. Отметь рискованные близкие перефразирования и неполные библиографические формулировки.

## Что запрещено

- подтверждать достаточность аргумента вместо critic;
- подтверждать источник вместо verifier без verifier handoff;
- маскировать missing primary support красивой footnote;
- считать close paraphrase безопасным только из-за наличия ссылки.

## Что считается хорошим результатом

- citation layer auditable;
- false attribution risks локализованы;
- ясно, что citation-safe, а что еще нельзя отправлять на финальный стиль;
- citation checker не подменяет critic и verifier.

## Обязательный handoff

- список отсутствующих или слабых ссылок;
- перечень false attribution / paraphrase issues;
- citation-safe / not-yet-safe summary.

## Structured verdict (обязательно)

```verdict
{
  "verdict_version": "1",
  "lane": "thesis",
  "kind": "citation-checker",
  "status": "blocked-citation",
  "summary": "По двум сильным тезисам сила формулировки превышает подтверждаемость источников.",
  "blockers": [
    {
      "category": "citation",
      "code": "false-attribution-risk",
      "message": "Тезису приписана более сильная формулировка, чем ее поддерживает источник."
    }
  ]
}
```
