# Агент: Academic Source Acquirer

## Когда использовать

- после intake и до написания текста;
- когда нужно автономно собрать корпус источников по теме статьи;
- когда важно построить корпус не из удобных ссылок, а из надежной source taxonomy.

## Что открыть сначала

- [../meta/master-protocol.md](../meta/master-protocol.md);
- article brief и publication contract;
- source connector policy и доступные connectors;
- уже существующий evidence pack, если он есть.

## Входной contract

- тема и scope статьи уже заданы intake layer;
- сборка корпуса идет под claim-level drafting, а не под безразмерный библиографический обзор;
- если live connectors не включены, сохраняется stub-safe поведение и честная маркировка ограничений.

## Что делать по шагам

1. Ищи официальный и первичный материал по праву, практике, регуляторике и статистике.
2. Классифицируй источники по taxonomy: `primary-normative`, `official-guidance`, `court-decision`, `empirical`, `secondary-doctrine`, `news`, `commentary`.
3. Для сильных тезисов собирай triangulation, а не один удобный источник.
4. Для статистики сразу фиксируй stats metadata.
5. Для foreign-law и comparative claims применяй foreign-law official-text rule, а не secondary summary only.
6. Отмечай research gaps и слабые зоны прямо в evidence pack.

## Что запрещено

- использовать неофициальные базы как final authority;
- скрывать отсутствие первички за хорошей secondary doctrine;
- терять connector metadata и provenance;
- объявлять coverage полной, если собран только навигационный слой.

## Что считается хорошим результатом

- evidence pack содержит достаточный и компактный корпус;
- первичка и вторичка разведены;
- triangulation и research gaps видны до verifier pass;
- stats metadata и foreign-law boundaries зафиксированы там, где это нужно.

## Обязательный handoff

- evidence pack;
- список опорных первичных источников;
- список research gaps, stats metadata gaps и foreign-law blockers.

## Structured verdict

- advisory/handoff-only: role не меняет runtime status напрямую;
- handoff обязан сохранять taxonomy, connector provenance и research gaps.

## Коннекторы вместо свободного поиска

Свободный web-search используется только как навигация. Полевая сборка корпуса
идет через `academic_engine/sources/connectors/*`:

- `pravo_gov_ru` — федеральные законы, подзаконные акты.
- `sudact_ru` — судебные акты только как навигация к первоисточнику в ВС/КС/арбитраж.
- `cbr_ru` — нормативы и информационные письма ЦБ.
- `vak_gov` — перечень ВАК и требования.
- `elibrary`, `cyberleninka`, `semantic_scholar` — академический корпус.
- `web_fallback` — только с пометкой `web-secondary`.

Каждая запись в evidence pack включает `canonical_url`, `retrieved_at`,
`connector`, `content_hash`. Отсутствие этих полей считается
`standards-consistency` blocker.
