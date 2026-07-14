# Qualification evidence-pack template: academic source acquirer

Статус: pre-existing qualification template. Этот файл является единственным
разрешённым sandbox write target для bounded write-plan route. Он не содержит
live sources, verified evidence или submission verdict.

## Taxonomy handoff

| Категория | Qualification status | Notes |
| --- | --- | --- |
| `primary-normative` | not-acquired | Требуется первичная опора в реальной работе. |
| `official-guidance` | not-acquired | Требуется provenance record в реальной работе. |
| `court-decision` | not-acquired | Навигация не заменяет официальный первоисточник. |
| `empirical` | not-acquired | Нужны stats metadata и дата проверки. |
| `secondary-doctrine` | not-acquired | Не заменяет первичную опору для сильного тезиса. |
| `news` | not-acquired | Не является final authority. |
| `commentary` | not-acquired | Допустим только как навигационный слой. |

## Provenance record schema

Для будущей записи должны быть заполнены:

- `canonical_url`;
- `retrieved_at`;
- `connector`;
- `content_hash`;
- `source_category`;
- `triangulation_status`.

Поля намеренно не заполнены: qualification run не выполняет acquisition или
verification.

## Research-gap handoff

- Нет live collection или connector result.
- Нет первичной верификации, triangulation или coverage claim.
- Нет основания для submission-ready или publication claim.

<!-- qualification-template: academic-source-acquirer -->
