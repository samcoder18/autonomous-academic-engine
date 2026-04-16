# Сборка полного документа

Архивная памятка. Актуальные правила работы с рукописью находятся в [master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md).

## Короткий порядок

1. Редактировать только нужную секцию в [manuscript/sections](/Users/albina/дипломная/manuscript/sections).
2. Проверить, что заголовки и структура не расходятся с архитектурой главы.
3. При необходимости оценить объем через [scripts/check_section_length.sh](/Users/albina/дипломная/scripts/check_section_length.sh).
4. Пересобрать полный Markdown через [scripts/assemble_thesis.sh](/Users/albina/дипломная/scripts/assemble_thesis.sh).
5. При необходимости экспортировать `DOCX` через [scripts/export_docx.sh](/Users/albina/дипломная/scripts/export_docx.sh).

## Напоминания

- [manuscript/full-draft.md](/Users/albina/дипломная/manuscript/full-draft.md) - сборочный файл, а не основной рабочий документ.
- Введение финализируется после того, как собраны основные исследовательские результаты по главам.
- Для рабочей оценки объема можно использовать ориентир около 1800 знаков с пробелами на страницу.
