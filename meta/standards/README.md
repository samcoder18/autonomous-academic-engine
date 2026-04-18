# Standards

Здесь хранится контур внешних требований для academic engine.

## Структура

- `registry.toml` - реестр профилей V2-A с fallback-профилями, official-only source list и raw/normalized binding.
- `raw/` - исходные официальные PDF, DOCX и иные документы требований.
- `normalized/` - нормализованные профили правил, на которые ссылаются skills и finalizer.

## Правило

Raw и normalized хранятся рядом, но finalizer не должен заявлять полную формальную готовность, если relevant raw-документ отсутствует или конфликтует с normalized profile.

## Intake track

- `python3 -m telegram_console.work_cli standards-intake <profile-id>` - подтягивает отсутствующие raw-источники и пересобирает normalized profile без принудительного refresh уже скачанных файлов.
- `python3 -m telegram_console.work_cli standards-refresh <profile-id>` - принудительно перепроверяет official sources и переписывает raw manifest + normalized profile.
- `python3 -m telegram_console.work_cli standards-status [profile-id]` - показывает resolved profile, fallback, raw state и conflict flag.
