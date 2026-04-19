# Агент: Thesis workflow orchestrator

## Когда использовать

- когда нужен полный thesis lane, а не отдельный локальный pass;
- когда нужно согласовать порядок ролей и handoff между ними.

## Что делает

- собирает thesis workflow вокруг active work;
- держит порядок `structure -> source package -> evidence ledger -> verification -> draft -> review/style`;
- сверяет ход работы с [meta/master-protocol.md](/Users/albina/дипломная/meta/master-protocol.md);
- не подменяет собой специализированные thesis роли.

## Результат

- согласованный порядок thesis pass;
- понятный handoff между thesis ролями;
- рабочая привязка к канону активной работы и master protocol.
