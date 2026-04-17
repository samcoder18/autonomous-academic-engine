# Telegram Console Runtime

Здесь хранится runtime-state для локальной Telegram-консоли, которая работает как remote chat с Codex.

- `projects.json` - локальный реестр проектов для мультипроектного режима бота.
- `.env.launchd` - локальный env-файл для macOS LaunchAgent c `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID` и опциональными launchd-настройками.
- `runtime/` - active project, persistent project chat state, Codex session ids, фоновые chat-task обертки и очередь уведомлений.

Это не каноническая часть thesis/article артефактов. Папка считается операционной и не коммитится.
