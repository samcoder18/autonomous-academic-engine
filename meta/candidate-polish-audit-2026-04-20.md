# Candidate Polish Audit (2026-04-20)

## Scope

Аудит ограничен candidate-adjacent системой:

- dissertation contour;
- `one-shot-dissertation`, `work-status`, planner/runtime summary;
- candidate standards bindings;
- dissertation templates и thesis role contracts;
- candidate tests и truth-sync документации.

## Verdict

`dissertation-candidate` доведён до `framework-ready` состояния для текущей
фазы. Контур стал строже не только формально, но и операционно: следующий шаг
теперь удерживает intellectual sequence, а финальный статус блокируется по
publication matrix, review sequence, author abstract, publication evidence и
length conformance.

## Closed Must-Fix Items

| Item | Before | After |
| --- | --- | --- |
| Candidate workflow order | `formal-artifacts` мог перетягивать следующий шаг раньше core drafting | Порядок закреплён как `build-maps -> verify-claims -> counterargument-pass -> draft-author-position -> formal-artifacts` |
| Publication matrix | Не была обязательной частью candidate profile и one-shot contour | `publication-claim-matrix.md` включена в bootstrap, profile, work-state и one-shot |
| Candidate maturity signal | Отсутствовал явный summary signal для maturity/review sequence | Добавлены `publication_matrix_complete`, `review_sequence_complete`, `candidate_intellectual_maturity_complete` |
| Repo truth sync | В docs оставались ложные ограничения про dissertation author abstract | README, AGENTS и audit/unknowns docs синхронизированы с фактическим feature set |

## Current Candidate Guarantees

- Bootstrap кандидатской создаёт dissertation subtree с `publication-claim-matrix.md`
  и без doctor-only defense placeholders.
- `one-shot-dissertation` для candidate валит `submission-ready`, если отсутствуют:
  historiography map, novelty/contribution map, dissertation claim map,
  chapter contracts, dissertation review, counterargument review,
  publication evidence, publication-claim matrix, generated author abstract /
  defense checklist или length-conformance.
- `work-status` и planner теперь не подталкивают к formal artifacts, пока не
  завершены research scaffold и review sequence, а author-position drafting ещё
  не подтверждён.
- Thesis role docs и dissertation templates требуют problem field,
  historiography position, author contribution, limits и адресованный
  counterargument.

## Verification Snapshot

- `python3 -m unittest tests.test_work_state tests.test_one_shot tests.test_work_bootstrap tests.test_work_type tests.test_dissertation_standards tests.test_dissertation_artifacts -q`
- `python3 -m ruff check academic_engine tests`
- `python3 -m unittest discover -s tests -q`
- `python3 -m academic_engine.work_cli skill-source-map audit --json`

Результат на 2026-04-20:

- targeted tests: green;
- `ruff`: clean for `academic_engine` + `tests`;
- full unittest suite: `384 tests OK`;
- `skill-source-map audit`: `ok=true`, `issues=[]`.

## Residual Risks

- Publication matrix пока проверяется структурно, а не на semantic completeness
  тезис-к-публикации.
- Candidate contour ещё не обкатан на отдельной живой dissertation work в
  рамках этого audit pass.
- Institution-specific overlays для конкретного диссовета и вузовских
  требований остаются следующей фазой.
