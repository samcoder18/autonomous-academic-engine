# Telegram Console Runtime

Здесь хранится runtime-state для локальной Telegram-консоли, которая работает как remote chat с Codex.

- `projects.json` - локальный реестр проектов для мультипроектного режима бота.
- `runtime/` - active project, persistent project chat state, Codex session ids, фоновые chat-task обертки и очередь уведомлений.

Это не каноническая часть thesis/article артефактов. Папка считается операционной и не коммитится.
