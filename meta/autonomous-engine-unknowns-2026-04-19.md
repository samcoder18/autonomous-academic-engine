# Known unknowns and pragmatic limits (2026-04-19)

После завершения Фаз 0–7 плана `autonomous_academic_engine_d4df2a4c`
система может выпустить честный `submission-ready`/`strong-draft-with-
blockers` вердикт для VKR, статьи, магистерской и (в стартовом
варианте) кандидатской/докторской диссертации. Здесь зафиксированы
вещи, которые **остаются за пределами автоматизации** и должны либо
проверяться человеком, либо подкрепляться внешними системами.

## 1. Источники и первичные опоры

### Решено автоматически

- Контрактные проверки структуры источника (`Source`) — связаны в
  [telegram_console/sources](../telegram_console/sources).
- Stub/live режим и throttling (stub включен по умолчанию в CI).
- Коннекторы: `pravo_gov_ru`, `sudact_ru`, `cbr_ru`, `vak_gov`,
  `elibrary`, `semantic_scholar`, `cyberleninka`, `web_fallback`.
- Верификатор `telegram_console/source_verifier.py`: статусы
  `current/stale/obsolete/primary-missing/unverifiable` и блокеры.

### Unknowns

- Автоматический pull первичных актов `publication.pravo.gov.ru`
  может выходить из строя при изменениях их верстки — требуется
  мониторинг stub vs live divergence.
- `elibrary.ru` при авторизованном режиме требует ручной cookie —
  ротация не автоматизирована.
- Иностранные базы (HeinOnline, Westlaw, LexisNexis) не подключены
  из-за лицензионных ограничений; их покрытие остаётся ручным.
- Датированность судебных актов: `sudact.ru` не всегда содержит
  последнюю редакцию — требуется cross-check с `pravo.gov.ru` или
  `supcourt.ru` перед финализацией.

## 2. Оригинальность и MinHash

### Решено автоматически

- MinHash fingerprint + локальный корпус
  ([`telegram_console/originality`](../telegram_console/originality));
- Блокеры `originality/high-similarity-<passage_id>` для
  `repair_kernel`;
- Гейт в `one_shot` с порогом из work-type профиля.

### Unknowns

- Локальный корпус создается вручную или загружается из
  стороннего источника. Интеграции с `antiplagiat.ru`,
  `ruscorpora`, `crossref similarity check` нет и быть не должно
  (canon запрещает имитацию «обхода» детекторов).
- Semantic-уровневый плагиат (перефразирование) детектируется
  только на shingles-уровне, не семантическом embedding'е.
- Корпус для пре-модерации студенческих работ должен собираться
  кафедрой вручную (не наша ответственность).

## 3. GOST и оформление

### Решено автоматически

- `telegram_console/gost_linter.py`: 5 классов проверок (структура,
  терминальная точка, дубликаты URL, дубликаты entry);
- `telegram_console/docx_conformance.py`: шрифт, кегль, поля,
  междустрочный, heading-стили, footnotes;
- Нормализованные профили: `ru-vkr-gost-r-7-0-100-2018`,
  `ru-vkr-university-default`.

### Unknowns

- Таблицы, схемы и рисунки в DOCX не проверяются (python-docx не
  используется, чтобы не тянуть зависимость).
- Нумерация формул, titлейblока на титульном листе, расположение
  подписей под рисунками — вне scope deterministic линтера.
- Кафедральные/университетские отклонения от ГОСТ (например,
  СОГУ 2025) требуют отдельного нормализованного профиля; сейчас
  такой профиль подключен как `sogu-vkr-2025`, но поля docx-
  conformance пока дефолтные `ru-vkr-university-default`.

## 4. Агентные верификации

### Решено автоматически

- Структурированные verdict-блоки (`meta/schemas/verdict.schema.json`)
  с fallback на legacy regex;
- Blocker aggregator в `repair_kernel`;
- `contract_gates` проверяет обязательные outputs и статусы;
- `resource_guards` ограничивает wall-time и retry budget.

### Unknowns

- Качество аналитических выводов (тезисы, контраргументы,
  интерпретация нормативной практики) остаётся на стороне Codex и
  агентов. `argument-critic` и `counterargument-critic` заявлены, но
  их metrics невозможно автоматически валидировать.
- Ограничение иллюстраций и таблиц к первоисточникам требует
  ручной верификации при `submission-ready`.

## 5. Регламентная часть VKR и диссертаций

### Решено автоматически

- Фронтматтер: `title-page`, `abstract-ru`, `abstract-en`,
  `keywords`, `task-sheet` —
  [`telegram_console/vkr_artifacts.py`](../telegram_console/vkr_artifacts.py).
- Dissertation contour: `author-abstract`, `defense-checklist`,
  `historiography-map`, `novelty-contribution-map`,
  `dissertation-claim-map`, `counterargument-review`,
  `dissertation-review`, `publication-evidence`,
  `publication-claim-matrix` — через
  [`telegram_console/dissertation_artifacts.py`](../telegram_console/dissertation_artifacts.py),
  [`telegram_console/dissertation_contour.py`](../telegram_console/dissertation_contour.py)
  и `one-shot-dissertation`.
- Work-type профили (`article`, `vkr-bachelor`, `vkr-specialist`,
  `master-thesis`, `dissertation-candidate`, `dissertation-doctor`) —
  [`telegram_console/work_type.py`](../telegram_console/work_type.py).
- Минимальное количество источников/порог similarity — из профиля.

### Unknowns

- Список работ ВАК/Scopus/WoS заявителя — ручной сбор; система
  может проверить только базовые формальные поля и связку с
  `publication-claim-matrix`.
- Отзыв ведущей организации и оппонентов не собирается автоматически
  как живой пакет защиты; для докторской пока моделируются только
  placeholders и deterministic presence checks.
- Даты предзащиты и защиты заполняются вручную в `metadata.toml`.
- Институционально-специфичные требования диссовета и вуза остаются
  reference-driven, пока не подключён отдельный operative overlay.

## 6. Пайплайн и CI

### Решено автоматически

- `one-shot-thesis` CLI + offline harness
  ([`tests/test_regression_harness.py`](../tests/test_regression_harness.py)).
- GitHub Actions: ruff, unittest, skill source map audit, smoke
  verdict parser, smoke one-shot pipeline.
- Fake Codex для verdict smoke.
- Ops-alerts ([`telegram_console/ops_alerts.py`](../telegram_console/ops_alerts.py))
  подключены к `autonomous_daemon.acquire_daemon_lock` (stale-lock recovery,
  lock-blocked) и к `run_daemon_foreground` (terminal-stop, stuck, unhandled
  exception). Offline sink настраивается через `OPS_ALERT_LOG_PATH`; без него
  события остаются в stderr + Python `logging`.
- Resource guards ([`telegram_console/resource_guards.py`](../telegram_console/resource_guards.py))
  активны в `run_daemon_foreground`: `TimeoutBudget` как defense-in-depth к
  существующему `max_runtime_minutes`, `StuckDetector` через CLI-флаг
  `--stuck-after-minutes` или env `DAEMON_STUCK_AFTER_MINUTES`.

### Unknowns

- Live role execution требует реального OPENAI/Codex бюджета; CI использует
  offline fakes и не поднимает внешние operator surfaces.
- Pandoc смоук не включен в CI (по умолчанию pandoc отсутствует
  в стандартных runners; добавление — следующий шаг).
- python-docx отсутствует намеренно: все DOCX-проверки через stdlib.
- Ops-sink по умолчанию пишет в stderr + optional log file. Так специально:
  без явной конфигурации daemon остаётся локальным и детерминированным.
- launchd coverage остаётся unit-style: CLI и plist generation покрыты
  тестами, но реальный smoke против системного launchd в CI по-прежнему
  не запускается.

## 7. Этическая рамка

- Пайплайн автоматически **не** маскирует ИИ-атрибуты текста, не
  обходит антиплагиат и не имитирует «защитные» трансформации.
- Любой gate может только **понизить** статус до
  `strong-draft-with-blockers` или ниже; повысить до
  `submission-ready` может только отсутствие блокеров в
  deterministic gates плюс положительный вердикт
  `submission-evaluator`.
- Настройка `originality_threshold` через work-type профиль строгая;
  она не предназначена для ослабления в интересах «проходимости».

## 8. План next steps (post-2026-04-19)

1. Добавить таблицы/рисунки в DOCX-проверки (минимум: наличие
   «Список таблиц», «Список рисунков», нумерация);
2. Включить live режим коннекторов в nightly CI за счёт
   отдельного job (`OFFLINE=0` + секреты);
3. Добавить institution-specific overlays для кандидатских
   диссертаций и пакета защиты;
4. Вынести fake codex в `scripts/tests/fixtures/` и добавить более
   разнообразные сценарии (blocked, needs-repair, updated);
5. Поддержать candidate/doctor quality gates уровня межглавного
   синтеза и publication matrix coverage beyond structural checks.
