# Legacy Local Runtime Namespace

Эта директория сохраняет историческое имя `output/telegram/`, потому что часть
локального runtime-кода и тестов всё ещё ожидает этот namespace. Это не
документирует удалённую operator surface проекта.

- `projects.json` - legacy локальный реестр проектов, если старый compatibility
  слой ещё читает этот файл.
- `.env.launchd` - legacy локальный env-файл для macOS service-manager smoke /
  compatibility checks; не является обязательной конфигурацией актуального CLI.
- `runtime/` - state/lock/stop файлы автономного daemon'а и совместимые runtime
  JSON-артефакты.

Это не каноническая часть thesis/article артефактов. Папка считается
операционной и не коммитится.
