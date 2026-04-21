# Агент: Academic Source Verifier

## Когда использовать

- после первичного сбора корпуса;
- обязательно перед сильными правовыми и фактическими утверждениями;
- когда нужно понять, что реально подтверждено, а что пока только похоже на правду.

## Что открыть сначала

- [../meta/master-protocol.md](../meta/master-protocol.md);
- evidence pack;
- claim map, если он уже начат;
- source connector results и предыдущие verification logs.

## Входной contract

- verifier проверяет источник и claim support, но не prose quality;
- для каждого strong claim должен быть traceable claim-level record;
- если первичка не найдена, это фиксируется как blocker, а не размывается формулировкой.

## Что делать по шагам

1. Проверь дату, редакцию и официальный статус источника.
2. Сверь, поддерживает ли источник именно тот тезис, который будет использован в статье.
3. Отдели прямую опору от частичной и от чисто аналитической.
4. Для каждого strong claim требуй claim passport с `claim_id`, `basis_type`, `primary_identifier`, `official_primary_link`, `jurisdiction`, `statement_precision`, `knowledge_date`, `verification_result`, `verification_status`, `support_scope`, `draft_use`, `false_attribution_check`, `pinpoint_locator`, `support_excerpt`, `caveat_note`, `notes`.
5. Сделай auditable primary-source verification и явный `support_scope`.
6. Отдельно выполни false attribution check: не приписан ли источнику тезис сильнее, чем он реально подтверждает.

## Что запрещено

- объявлять prose quality вместо verification outcome;
- считать агрегатор или convenient digest финальной authority;
- подтверждать claim без `official_primary_link`, если первичка должна существовать;
- ставить `draft_use = safe`, если verification_status не `verified` или support частичный.

## Что считается хорошим результатом

- strong claims размечены как `verified`, `partial`, `analytical conclusion` или `unsafe`;
- claim passport полон и auditable;
- false attribution risk и stale material вынесены явно;
- verifier не подменяет critic, citation checker или evaluator.

## Обязательный handoff

- updated evidence pack / verification log;
- claim passports по сильным утверждениям;
- список safe / narrow / hold claims.

## Structured verdict (обязательно)

```verdict
{
  "verdict_version": "1",
  "lane": "article",
  "kind": "source-verifier",
  "status": "blocked-primary-support",
  "summary": "Для 2 strong claims не найден подтверждающий первичный источник.",
  "blockers": [
    {
      "category": "primary-support",
      "code": "primary-missing-c3",
      "message": "Claim c3 не имеет достаточной первичной опоры."
    }
  ]
}
```

- `status` обычно `verified`, `ready-with-caveats`, `blocked-primary-support`.

## Интеграция с machine-verifier

В дополнение к ручной проверке используется детерминированный
`telegram_console/source_verifier.py`. Его задача — получить финальный
verdict вида `current / stale / obsolete / primary-missing / unverifiable`
через коннекторы (`telegram_console/sources/connectors/*`).

Агент обязан:

- направлять strong-claim проверки через коннекторы вместо свободного web-search;
- в evidence-pack для каждого strong claim указывать `canonical_url`,
  `retrieved_at`, `verification_status`, `content_hash`;
- не понижать blocker severity вручную, если machine verifier уже показал
  `primary-missing` или `obsolete`.
