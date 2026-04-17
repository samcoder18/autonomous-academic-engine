# Система legal-academic workflow

Это рабочее пространство для юридического академического движка с двумя lane:

- `thesis` - дипломный контур с каноном проекта и секционной рукописью;
- `article` - отдельный контур для автономной сборки юридических академических статей.

## Активные документы

- [AGENTS.md](/Users/albina/дипломная/AGENTS.md) - оркестрация ролей и порядок работы.
- [meta/project-canon.md](/Users/albina/дипломная/meta/project-canon.md) - только утвержденные решения.
- [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md) - единый рабочий регламент без повторов.
- [manuscript/sections](/Users/albina/дипломная/manuscript/sections) - канонические текстовые секции диплома.
- [articles/](/Users/albina/дипломная/articles) - article bundle для академических статей.
- [meta/standards](/Users/albina/дипломная/meta/standards) - raw и normalized publication profiles.

## Структура папок

- `agents/` - специализированные роли для структуры, ресерча, верификации, письма и редактуры.
- `articles/` - brief, evidence pack, claim map, draft, review, final и run trace для article lane.
- `meta/` - канон проекта, протокол и требования.
- `templates/` - шаблоны для пакетов источников, брифов, проверки и синхронизации.
- `sources/` - пакеты источников и рабочие заметки.
- `chapters/` - материалы по отдельным главам.
- `manuscript/` - секции диплома и сборочный файл.
- `scripts/` - служебные скрипты сборки, проверки объема и экспорта.
- `output/` - экспортированные версии документа.
- `reviews/` - листы проверки.
- `sync/` - короткие рабочие синхронизации.

## Как работать

### Thesis lane

1. Свериться с [meta/project-canon.md](/Users/albina/дипломная/meta/project-canon.md).
2. Взять порядок шагов из [AGENTS.md](/Users/albina/дипломная/AGENTS.md) и [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md).
3. Собирать источники по [templates/source-package-passport.md](/Users/albina/дипломная/templates/source-package-passport.md).
4. Планировать главу через [templates/chapter-brief.md](/Users/albina/дипломная/templates/chapter-brief.md).
5. Проверять фрагменты по [templates/chapter-review-sheet.md](/Users/albina/дипломная/templates/chapter-review-sheet.md).
6. Вносить текст только в [manuscript/sections](/Users/albina/дипломная/manuscript/sections), затем пересобирать документ через [scripts/assemble_thesis.sh](/Users/albina/дипломная/scripts/assemble_thesis.sh).
7. Для Word-версии со сносками использовать [scripts/export_docx.sh](/Users/albina/дипломная/scripts/export_docx.sh).

### Article lane

1. Открыть publication profile в [meta/standards/normalized](/Users/albina/дипломная/meta/standards/normalized).
2. Нормализовать тему в brief по [templates/article-brief.md](/Users/albina/дипломная/templates/article-brief.md).
3. Собрать evidence pack по [templates/evidence-pack.md](/Users/albina/дипломная/templates/evidence-pack.md).
4. Построить claim map по [templates/claim-map.md](/Users/albina/дипломная/templates/claim-map.md).
5. Прогнать evaluator review по [templates/article-review-sheet.md](/Users/albina/дипломная/templates/article-review-sheet.md).
6. Финализировать статью, checklist и DOCX через article lane и [scripts/export_academic_docx.sh](/Users/albina/дипломная/scripts/export_academic_docx.sh).

## Готовые запуски

Основные launcher:

- [scripts/codex_academic.sh](/Users/albina/дипломная/scripts/codex_academic.sh) - общий legal-academic launcher для article lane и thesis proxy.
- [scripts/codex_thesis.sh](/Users/albina/дипломная/scripts/codex_thesis.sh) - thesis-only launcher.
- [scripts/telegram_console.py](/Users/albina/дипломная/scripts/telegram_console.py) - Telegram-консоль для удаленного project-centric чата с Codex и экспорта.

Примеры:

- `bash scripts/codex_academic.sh article --topic "Конституционные пределы биометрической идентификации"`
- `bash scripts/codex_academic.sh article --brief articles/briefs/biometrics.md`
- `bash scripts/codex_academic.sh review articles/drafts/biometrics.md`
- `bash scripts/codex_academic.sh repair articles/reviews/biometrics.md`
- `bash scripts/codex_academic.sh thesis full-cycle manuscript/sections/03-chapter-2.md`
- `bash scripts/codex_thesis.sh full-cycle manuscript/sections/03-chapter-2.md`
- `bash scripts/codex_thesis.sh source-pack sources/02-chapter-2-regulation.md --notes "Собери пакет по ЕБС и практике 2025-2026"`
- `bash scripts/codex_thesis.sh verify manuscript/sections/03-chapter-2.md --notes "Особенно проверь 152-ФЗ и 572-ФЗ"`
- `bash scripts/codex_thesis.sh write-section manuscript/sections/04-chapter-3.md --notes chapters/03-chapter-3-brief.md`
- `bash scripts/codex_thesis.sh review-section manuscript/sections/02-chapter-1.md`
- `bash scripts/codex_thesis.sh style-pass manuscript/sections/02-chapter-1.md`

Что делает academic launcher:

- подставляет нужный academic или thesis skill по сценарию;
- запускает `codex exec` из корня проекта;
- отделяет thesis lane от article lane и не смешивает их артефакты;
- для article lane строит managed bundle в [articles/](/Users/albina/дипломная/articles);
- включает web search по умолчанию там, где критична актуальность первоисточников;
- сохраняет финальные сообщения и manifest article-run в [articles/runs](/Users/albina/дипломная/articles/runs), а thesis-run в [output/codex](/Users/albina/дипломная/output/codex).

## Telegram console

Telegram console теперь работает как минималистичный удаленный чат с Codex поверх мультипроектного реестра.

Что умеет текущая версия:

- работать в мультипроектном режиме через локальный реестр `output/telegram/projects.json`;
- держать один активный проект в Telegram и явно переключать его через `📚 Проекты`;
- показывать на главном экране действующие проекты, их статус и короткое поле “что сейчас в разработке”;
- принимать обычные текстовые сообщения и отправлять их напрямую в Codex CLI в контексте активного проекта;
- хранить persistent `session_id` по каждому проекту и продолжать разговор после перезапуска бота;
- использовать глобальный lock: пока Codex отвечает по одному проекту, новые запросы временно блокируются;
- экспортировать основной итоговый файл активного проекта через `📦 Экспорт` (для thesis-проектов это DOCX диплома);
- при желании дублировать финальный DOCX на почту через SMTP;
- ограничивать доступ одним `TELEGRAM_ALLOWED_CHAT_ID`;
- держать runtime-state отдельно в `output/telegram/runtime/`, не смешивая его с каноническими артефактами проекта.

Реестр проектов редактируется локально и не коммитится. Формат записи:

```json
{
  "projects": [
    {
      "id": "law-thesis-a",
      "title": "Диплом по биометрии",
      "root_dir": "/абсолютный/путь/к/проекту",
      "capabilities": ["thesis", "article"]
    }
  ]
}
```

Если `projects.json` еще нет, а бот запускается из одного валидного проекта, он создаст запись `default` автоматически.
Если хочешь добавить новый проект без ручного редактирования файла, используй локальную команду:

- `python3 scripts/telegram_console.py project add --title "Диплом по биометрии" --root "/абсолютный/путь/к/проекту"`
- `python3 -m telegram_console project add --title "Диплом по биометрии" --root "/абсолютный/путь/к/проекту"`

В этом режиме бот сам:

- валидирует папку проекта;
- определяет `capabilities`;
- генерирует читаемый `id` из названия;
- при конфликте добавляет суффиксы вроде `-2`, `-3`;
- не создает дубликат, если такой путь уже зарегистрирован.

Обязательные переменные окружения:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_ID`

Опционально:

- `CODEX_BIN`
- `CODEX_MODEL`
- `TELEGRAM_POLL_TIMEOUT`
- `SMTP_HOST`
- `SMTP_PORT` - порт SMTP, по умолчанию `587`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_SECURITY` - `starttls`, `ssl` или `none`; по умолчанию `starttls`
- `SMTP_FROM_EMAIL`
- `SMTP_FROM_NAME` - имя отправителя, по умолчанию `Академический штурман`
- `SMTP_TO_EMAIL`
- `SMTP_TIMEOUT_SECONDS` - таймаут SMTP, по умолчанию `30`

Локальный запуск вручную:

- `python3 scripts/telegram_console.py`
- `python3 -m telegram_console`

Локальный always-on режим через macOS LaunchAgent:

1. Заполни локальный env-файл `output/telegram/.env.launchd`
2. Выполни `python3 scripts/telegram_console.py service install`
3. Дальше управляй сервисом командами ниже, без открытого терминала

Команды сервиса:

- `python3 scripts/telegram_console.py service install`
- `python3 scripts/telegram_console.py service start`
- `python3 scripts/telegram_console.py service stop`
- `python3 scripts/telegram_console.py service restart`
- `python3 scripts/telegram_console.py service status`
- `python3 scripts/telegram_console.py service uninstall`

Что делает LaunchAgent-режим:

- запускает бота в фоне без открытого терминала;
- поднимает его после логина в macOS;
- перезапускает после падения;
- берет секреты из `output/telegram/.env.launchd`, а не из ручных `export`.

Где лежат служебные файлы:

- локальный env: `output/telegram/.env.launchd`
- plist template в репозитории: `deploy/local-telegram-console.plist`
- установленный plist: `~/Library/LaunchAgents/com.albina.telegram-console.plist`
- stdout лог: `output/telegram/runtime/bot.stdout.log`
- stderr лог: `output/telegram/runtime/bot.stderr.log`

Если `output/telegram/.env.launchd` еще не существует, `service install` сам создаст шаблон и подскажет, что заполнить.

Опция `--root` теперь означает `bot home`: именно там бот хранит `projects.json`, `runtime/` и свой служебный state.

Ключевой UX внутри Telegram:

- постоянные кнопки: `📚 Проекты` и `📦 Экспорт`
- `/start` - открыть dashboard
- все остальные сообщения, включая текст со слэшем, идут напрямую в агент как обычный prompt
- выбор проекта делается кнопкой `📚 Проекты`, а не текстовыми slash-командами

Почтовая отправка включается только если заданы `SMTP_HOST`, `SMTP_FROM_EMAIL` и `SMTP_TO_EMAIL`.
Если SMTP-настройки не заданы или заданы неполно, Telegram console работает как раньше, без почтовой доставки.
Письмо отправляется только для финального DOCX из сценария `Экспорт`: бот сначала собирает и отдает файл в Telegram, а затем отправляет тот же DOCX на почту как дополнительный канал доставки.

## Базовый принцип

Проект не используется для обхода антиплагиата, детекторов ИИ или маскировки заимствований. Качество повышается через сильный ресерч, проверку первоисточников, evidence trace, evaluator verdict и естественный академический стиль.
