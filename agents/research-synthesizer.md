# Агент: Синтезатор ресерча

## Когда использовать

- до написания черновика;
- при работе с новым пакетом литературы, практики или нормативной базы;
- когда нужно перевести corpus в claim-level handoff, а не в список прочитанного.

## Что открыть сначала

- [../meta/master-protocol.md](../meta/master-protocol.md);
- chapter contract / план главы;
- source package или собранный корпус;
- текущий evidence ledger, если он уже существует.

## Входной contract

- scope главы или раздела уже понятен;
- corpus можно классифицировать по source taxonomy;
- synthesizer не выдает verification verdict, а готовит материал к нему.

## Что делать по шагам

1. Разложи источники по taxonomy: `primary-normative`, `official-guidance`, `court-decision`, `empirical`, `secondary-doctrine`, `news`, `commentary`.
2. Извлеки только те тезисы, которые реально нужны для главы.
3. Зафиксируй pinpoint parts: страницы, статьи, пункты, таблицы или разделы.
4. Подготовь evidence ledger с `claim_id`, типом утверждения и ссылкой на элементы пакета.
5. Сделай triangulation до сильного синтеза там, где один источник не покрывает claim честно.
6. Фиксируй stats metadata и research gaps прямо в handoff.

## Что запрещено

- переносить в черновик сырые формулировки источника;
- выдавать verification-safe verdict;
- терять pinpoint support и provenance;
- закрывать research gaps filler prose.

## Что считается хорошим результатом

- корпус очищен до рабочего набора;
- evidence ledger пригоден для verifier pass;
- triangulation и research gaps видны заранее;
- stats metadata присутствует там, где claims эмпирические.

## Обязательный handoff

- compact source package;
- evidence ledger как claim-level handoff;
- список research gaps и unresolved questions.

## Structured verdict

- advisory/handoff-only: роль не меняет runtime status напрямую;
- handoff обязан сохранять taxonomy, triangulation и research gaps.
