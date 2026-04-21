# Мастер-протокол legal-academic workspace

## Назначение

Это единый рабочий регламент workspace для всех `work bundle`, thesis lane и article lane.
Если правило процесса уже описано здесь, его не нужно дублировать в других служебных файлах.

## 1. Источники истины

- [workspace.toml](../workspace.toml) хранит корневую конфигурацию workspace.
- `works/<slug>/work.toml` хранит конфигурацию конкретной работы.
- `works/<slug>/work-canon.md` хранит только утвержденные решения конкретной работы.
- `works/<slug>/thesis/manuscript/sections/` хранит канонический thesis-текст конкретной работы.
- `works/<slug>/thesis/ledgers/` хранит claim-level evidence ledger для thesis lane, если он создан.
- `works/<slug>/articles/` хранит article bundle и финальный article text конкретной работы.
- [meta/standards](standards) хранит raw и normalized publication standards.
- [templates/](../templates) задает минимальные reusable формы для повторяемых задач.

Если решение не зафиксировано в каноне активной работы, оно не считается окончательным.

## 2. Когда дробить работу на отдельные чаты

Новый чат открывается, если:

- меняется цель работы;
- меняется `active work`;
- обсуждается больше одной главы или одной статьи одновременно;
- пакет источников выходит за пределы примерно 10-15 позиций;
- началась отдельная подзадача вроде `методология`, `сноски`, `финальная вычитка`, `сравнительное право`;
- текущий чат тратит время на повторение старого контекста вместо движения вперед.

После завершения отдельного чата делается короткая синхронизация в work-local `sync/` по шаблону.

## 3. Агентная цепочка

Порядок ролей описан в [AGENTS.md](../AGENTS.md) и реализуется через специализированные файлы в [agents/](../agents).

Базовая цепочка для thesis lane:

1. Проверка workspace, active work и постановка задачи.
2. Архитектор структуры.
3. Синтезатор ресерча.
4. Evidence ledger как claim-level handoff между пакетом источников и черновиком.
5. Верификатор источников.
6. Автор черновика.
7. Проверка ссылок и атрибуции.
8. Критик аргументации.
9. Редактор стиля и естественности.

Базовая цепочка для article lane:

1. Intake и publication contract.
2. Source acquisition.
3. Source verification.
4. Evidence cartography.
5. Draft writing.
6. Citation checking.
7. Counterargument critique.
8. Submission evaluator.
9. Repair orchestrator при необходимости.
10. Finalizer и checklist.

Пропуск этапа допускается только если это безопасно и явно отмечено в рабочем следе соответствующего lane.

## 4. Разделение lane

- thesis lane пишет только в `works/<slug>/thesis/` и производные thesis-выходы;
- article lane пишет только в `works/<slug>/articles/` и производные article-выходы;
- один автономный прогон не должен смешивать thesis- и article-артефакты как основной output;
- thesis lane обязан использовать канон активной работы;
- article lane использует канон активной работы только когда статья связана с ее исследовательской рамкой.

## 5. Стандарт работы с источниками

- Источники собираются пакетами, а не всем массивом сразу.
- Для новой главы оптимален пакет 6-12 профильных источников; шире - только при явной необходимости.
- Для каждого источника фиксируются роль, полезные тезисы, пределы использования и дата проверки.
- Базовая source taxonomy для thesis и article lane: `primary-normative`, `official-guidance`, `court-decision`, `empirical`, `secondary-doctrine`, `news`, `commentary`.
- Для thesis lane после source package и до drafting ведется evidence ledger с `claim_id`, типом утверждения, статусом проверки, ссылкой на элемент source package и датой проверки первички.
- Для любого non-analytical claim strict claim passport включает как минимум `claim_id`, `basis_type`, `primary_identifier`, `official_primary_link`, `jurisdiction`, `statement_precision`, `knowledge_date`, `verification_result`, `verification_status`, `support_scope`, `pinpoint_locator`, `support_excerpt`, `draft_use`, `false_attribution_check`; для qualified/partial claims обязателен `caveat_note`.
- `draft_use = safe` запрещен для `needs-recheck`, `unsafe-for-draft`, partial/context-only support и stale dynamic material.
- Для норм, судебной практики, статистики и других динамичных данных приоритет всегда за первичными и официальными источниками.
- Для article lane это правило действует как жесткий default: официальный и первичный источник является финальной authority для права, практики, регуляторики и статистики.
- До сильного синтеза нужна triangulation: как минимум сопоставление первички с еще одним релевантным слоем, если тезис не исчерпывается одним источником.
- Для статистики обязательно фиксируются minimum stats metadata: период, территория, метод или провайдер, дата выгрузки, предел интерпретации.
- Для foreign-law и comparative claims нужен foreign-law official-text rule: опора на официальный текст нормы, решения или guidance, а не только на secondary summary.
- Если доступен первоисточник, нельзя ограничиваться пересказом или обзором.
- Неофициальные правовые базы и агрегаторы допустимы только как навигация к первоисточнику.
- Любое сильное утверждение должно быть либо проверено, либо явно помечено как аналитический вывод.
- Нельзя придумывать страницы, цитаты, реквизиты и "удобные" формулировки, которых нет в источнике.
- Research gaps фиксируются явно и не замещаются filler prose или широкими общими абзацами.

## 6. Стандарт качества текста

- Черновик строится из тезисов и доказательств, а не из сырых абзацев источников.
- Оригинальность достигается через собственный анализ, сопоставление позиций и добросовестные ограничения вывода.
- Проект не предназначен для обхода антиплагиата, ИИ-детекторов и иных проверок на самостоятельность.
- Если абзац звучит как универсальный шаблон, его нужно переписать в более предметной юридической логике.
- Красивый стиль не может заменять доказательность и корректную ссылку.
- Draft writer работает только внутри verified evidence envelope.
- Style editor меняет только форму; substantive strengthening возвращается в draft/verifier loop.
- Critic помогает увидеть логические и композиционные проблемы, но не подменяет verification.
- Citation checking отдельно проверяет false attribution risk.
- Для article lane финальный verdict должен прямо различать `submission-ready`, `strong-draft` и `strong-draft-with-blockers`.
- Если primary support недостаточен, результат обязан быть понижен в статусе, а не отредактирован до видимости готовности.

## 7. Правила работы с thesis-рукописью

- Основной рабочий текст редактируется только в `works/<slug>/thesis/manuscript/sections/`.
- Перед drafting сильные тезисы по возможности проходят через `source package -> evidence ledger -> verification`.
- Для больших thesis sections (`> 8 manuscript pages` или `5+ subsections`) критика идет в два прохода: `skeleton pass`, затем `local paragraph pass`.
- Сноски оформляются как Markdown-сноски в конце соответствующей секции.
- После заметных изменений секции нужно пересобрать полный документ через [scripts/assemble_thesis.sh](../scripts/assemble_thesis.sh) с `--work`.
- Для Word-версии со сносками используется [scripts/export_docx.sh](../scripts/export_docx.sh) с `--work`.
- Проверку ориентировочного объема удобно делать через [scripts/check_section_length.sh](../scripts/check_section_length.sh).

## 8. Правила работы с article bundle

- Нормализованный article brief хранится в `works/<slug>/articles/briefs/`.
- Evidence pack хранится в `works/<slug>/articles/evidence/`.
- Claim map хранится в `works/<slug>/articles/claim-maps/`.
- Draft хранится в `works/<slug>/articles/drafts/`.
- Findings-first review хранится в `works/<slug>/articles/reviews/`.
- Финальный Markdown и checklist хранятся в `works/<slug>/articles/final/`.
- DOCX статьи экспортируется через [scripts/export_academic_docx.sh](../scripts/export_academic_docx.sh) с `--work`.
- Finalizer не должен утверждать полную формальную готовность, если relevant raw standard отсутствует или конфликтует с normalized profile.
- Article review/checklist обязаны сохранять машинно читаемые blocking fields по citation safety, footnote consistency, close paraphrase, counterarguments, limits/caveats и другим финальным blockers.
- Если citation / paraphrase / counterargument / caveat blockers остаются открытыми, managed finalization обязан понижать статус до `strong-draft-with-blockers` и блокировать `export-ready`.

## 9. Листы проверки и синхронизации

- Для review-задач и крупных критических проходов создается или обновляется лист проверки в work-local `thesis/reviews/` по [templates/chapter-review-sheet.md](../templates/chapter-review-sheet.md).
- Для больших thesis sections можно дополнительно вести section-scoped `*-glossary.md` и `*-micro-review.md` в `works/<slug>/thesis/reviews/`.
- После каждого значимого рабочего цикла обновляется короткая синхронизация в work-local `thesis/sync/` по [templates/chat-sync.md](../templates/chat-sync.md).
- Если этап агентной цепочки был безопасно пропущен, причина этого фиксируется именно в sync-следе работы.
- Для article lane findings-first review создается или обновляется в `works/<slug>/articles/reviews/` по [templates/article-review-sheet.md](../templates/article-review-sheet.md), а итоговый status и blockers - в checklist.

## 10. Минимальный результат рабочего цикла

В конце каждого значимого рабочего цикла фиксируется:

- что утверждено;
- что написано или обновлено;
- что еще не проверено;
- какие источники требуют перепроверки;
- какой следующий шаг.

Для article lane к этому добавляется:

- какой статус результата сейчас допустим;
- какие blockers мешают `submission-ready`;
- какие файлы составляют текущий article bundle.

## 11. Автономные machine-driven гейты (thesis / VKR / диссертация)

Перед тем как объявить любой thesis-результат `submission-ready`, рукопись обязана пройти детерминированный one-shot pipeline:

- `python3 -m telegram_console.work_cli build-vkr-frontmatter [--work <slug>]` — собирает title-page, abstract, keywords, task-sheet из `works/<slug>/thesis/metadata.toml`. Любая метаданная-дыра (автор, научный руководитель, abstract < 200 символов, < 3 ключевых слов RU/EN) блокирует сборку.
- `python3 -m telegram_console.work_cli one-shot-thesis [--work <slug>] [--corpus <path>] [--skip-docx] [--work-type <profile>]` — запускает гейты `vkr-frontmatter`, `gost-bibliography`, `docx-conformance`, `originality`, `work-type-structure`, а для managed thesis bundle еще и `thesis-quality-contract`, и пишет отчёт в `works/<slug>/thesis/reviews/<date>-one-shot-report.(md|json)`.

Правила статуса:

- при любом FAIL хотя бы одного гейта отчёт получает статус `strong-draft-with-blockers` — финализатор обязан понизить итоговый статус и передать список блокеров в `repair_kernel`;
- статус `submission-ready` разрешён только если все применимые гейты PASS и `work-type-structure` сошёлся с выбранным профилем (`article`, `vkr-bachelor`, `vkr-specialist`, `master-thesis`, `dissertation-candidate`, `dissertation-doctor`);
- `thesis-quality-contract` требует для managed thesis bundle claim ledger, verification log и review artifacts с machine-readable strict fields; отсутствие этих артефактов или неполный claim passport блокируют `submission-ready`;
- origin-text originality-gate обязан использовать локальный corpus (`telegram_console.originality`). Внешние AI-детекторы и anti-plagiarism SaaS запрещены и системой не поддерживаются (см. AGENTS.md, hard rules).

### 11.1 Repo-level release claims

- Сильный repo-level claim вида `release-quality`, `fully final` или эквивалентной формулировки допускается только на clean git snapshot: без незакоммиченных repo-tracked изменений в рабочем дереве.
- Перед таким claim обязан быть полностью зелёный deterministic verification matrix workspace: `python3 -m unittest discover -s tests -q`, `ruff check telegram_console/ tests/`, `ruff format --check telegram_console/ tests/`, `python3 -m telegram_console.work_cli skill-source-map audit --json`.
- Если аудит или verification matrix были выполнены на dirty tree, это допустимо как engineering evidence и промежуточный closeout, но не как финальный безусловный release-quality verdict.

### 11.2 Операционный канал daemon'а

- Долгоживущие компоненты (`autonomous_daemon.run_daemon_foreground`, Telegram bot) эмитят структурированные ops-alerts через [`telegram_console.ops_alerts`](../telegram_console/ops_alerts.py). События, которые обязаны доходить до оператора: `daemon/stale-lock-recovered`, `daemon/lock-blocked`, `daemon/terminal-max-cycles`, `daemon/terminal-max-runtime`, `daemon/run-stuck`, `daemon/timeout-exceeded`, `daemon/unhandled-exception`.
- Конфигурация доставки: `OPS_ALERT_CHAT_ID` (Telegram-чат для ops-событий, **не** совпадающий с пользовательским чатом проекта) и `OPS_ALERT_LOG_PATH` (файл для offline-tee). Если ни одно не выставлено, алерты идут в stderr и в Python `logging` — local-run остаётся тихим, но событие не теряется.
- Stuck-detector активируется флагом `--stuck-after-minutes` у `autonomous daemon run` или переменной `DAEMON_STUCK_AFTER_MINUTES`. При срабатывании daemon пишет terminal-state `run-stuck`, эмитит `daemon/run-stuck` (severity CRITICAL) и завершается штатно.
- Ops-канал намеренно отделён от продуктовых уведомлений: сбой одного не блокирует другой. Любое изменение kind-ов алертов фиксируется в [tests/test_daemon_ops_integration.py](../tests/test_daemon_ops_integration.py) и в §6 unknowns.

Известные пределы pipeline документированы в [meta/autonomous-engine-unknowns-2026-04-19.md](autonomous-engine-unknowns-2026-04-19.md). Любые pragmatic boundaries обновляются там, а не в прозе work-canon.
