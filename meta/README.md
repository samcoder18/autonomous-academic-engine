# Навигация по `meta/`

В `meta/` лежат только reusable документы движка и внешний слой требований.

Актуальное управление проектом описывается как CLI/file-first. Исторические
аудиты в этой папке могут упоминать прежний внешний runtime layer, launchd или
удалённые work-bundle как состояние на дату соответствующего аудита; эти
упоминания не являются текущим target architecture и не должны использоваться
как инструкция по управлению проектом.

## Активные файлы

- [master-protocol.md](master-protocol.md) — общий рабочий регламент для всех works и lane; единственный источник процессных правил.
- [project-canon.md](project-canon.md) — compatibility shim для legacy-ссылок. Не использовать как primary source of truth: активный канон хранится в `works/<slug>/work-canon.md`.
- [skill-source-map.toml](skill-source-map.toml) — repo-first mapping между Codex skills и `agents/*.md`.
- [standards/](standards) — raw и normalized publication standards:
  - [standards/registry.toml](standards/registry.toml) — реестр известных профилей;
  - [standards/normalized/](standards/normalized) — машиночитаемые нормализованные профили (включая `ru-vkr-gost-r-7-0-100-2018` и `ru-vkr-university-default`).
- [schemas/](schemas) — JSON-схемы для runtime артефактов:
  - [schemas/verdict.schema.json](schemas/verdict.schema.json) — контракт для structured verdict-блоков evaluator'ов.

## Аудиты и unknowns автономного движка

- [engineering-audit-autonomous-workspace-2026-04-19.md](engineering-audit-autonomous-workspace-2026-04-19.md) — полный инженерный аудит workspace (архитектура, риски, план мигитации).
- [autonomous-engine-unknowns-2026-04-19.md](autonomous-engine-unknowns-2026-04-19.md) — прагматические границы автономного движка, что решено машинно и что остаётся у оператора / Codex-агентов.

## Архив

- [archive/](archive) — устаревшие или вспомогательные памятки; не редактируются как активный регламент.

## Правило

Если правило уже есть в `master-protocol.md`, не нужно возвращать его в активную часть `meta/` отдельным файлом.
