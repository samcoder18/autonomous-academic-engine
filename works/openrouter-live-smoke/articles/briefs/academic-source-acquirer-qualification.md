# Qualification fixture: academic source acquirer

## Qualification-only boundary

Этот dossier предназначен только для изолированной проверки маршрута
`academic-source-acquirer` через OpenRouter. Он не является поручением на
поиск, сбор, верификацию или публикацию источников. В qualification run нет
live search, коннекторов, инструментов, доступа к файловой системе провайдера
или внешних баз данных.

## Нейтральная тема и publication contract

Тема: методика ведения evidence pack для учебной академической статьи без
утверждений о внешних фактах.

- Тип материала: учебная академическая статья.
- Язык: русский.
- Сильное утверждение в реальной работе требует отдельной проверяемой опоры.
- Статус: qualification-fixture; не является source corpus, verified evidence
  или submission-ready материалом.

## Fixed source taxonomy

Для будущего evidence pack категории должны оставаться различимыми:

- `primary-normative`;
- `official-guidance`;
- `court-decision`;
- `empirical`;
- `secondary-doctrine`;
- `news`;
- `commentary`.

Ни одна категория здесь не содержит живого источника. Этот список задаёт
только taxonomy для безопасного handoff.

## Provenance and triangulation expectations

Каждая будущая запись evidence pack должна явно хранить как минимум
`canonical_url`, `retrieved_at`, `connector`, `content_hash` и source category.
Для существенного тезиса требуется явная triangulation-оценка, а не одна
удобная ссылка. Эти поля в данном dossier являются требованиями шаблона, а не
заявлениями о полученных данных.

## Research gaps

- Нет собранных primary sources.
- Нет подтверждённых статистических данных и stats metadata.
- Нет foreign-law material и его официального текста.
- Нет проверки полноты coverage или пригодности для submission.

<!-- qualification-fixture: academic-source-acquirer -->
