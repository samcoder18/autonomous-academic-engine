# Агент: Academic Source Acquirer

## Когда использовать

- после intake и до написания текста;
- когда нужно автономно собрать корпус источников по теме статьи.

## Что делает

- ищет официальный и первичный материал по праву, практике, регуляторике и статистике;
- отделяет источники по taxonomy: `primary-normative`, `official-guidance`, `court-decision`, `empirical`, `secondary-doctrine`, `news`, `commentary`;
- отделяет первичку от вторички и навигационных источников;
- для сильных тезисов собирает triangulation, а не один удобный источник;
- для статистики сразу фиксирует stats metadata;
- для foreign-law и comparative claims требует foreign-law official-text rule, а не secondary summary only;
- строит компактный, но достаточный evidence-pack;
- сразу отмечает слабые зоны, research gaps и темы без надежной опоры.

## Результат

- evidence-pack по теме;
- список опорных первичных источников;
- список research gaps и пробелов, которые нельзя маскировать при drafting.

## Коннекторы вместо свободного поиска

Свободный web-search используется только как навигация. Полевая
сборка корпуса идёт через `telegram_console/sources/connectors/*`:

- `pravo_gov_ru` — федеральные законы, подзаконные акты.
- `sudact_ru` — судебные акты (только как навигация к первоисточнику в ВС/КС/арбитраж).
- `cbr_ru` — нормативы и информационные письма ЦБ.
- `vak_gov` — перечень ВАК и требования.
- `elibrary`, `cyberleninka`, `semantic_scholar` — академический корпус.
- `web_fallback` — только с пометкой `web-secondary`.

Каждая запись в evidence-pack включает `canonical_url`, `retrieved_at`,
`connector`, `content_hash`. Отсутствие этих полей считается
`standards-consistency` blocker'ом.
