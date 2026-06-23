# Тесты `academic_engine`

- Полный прогон (из корня репозитория):

  ```bash
  python3 -m unittest discover -s tests -q
  ```

- Канонический deterministic entrypoint для локальной проверки и CI — именно `unittest discover`, а не один "главный" модуль.
- Regression pack уже split по подсистемам:
  - [`test_daemon_ops_integration.py`](test_daemon_ops_integration.py), [`test_daemon_smoke.py`](test_daemon_smoke.py) — daemon, ops-alerts и runtime containment;
  - [`test_work_cli_autonomous.py`](test_work_cli_autonomous.py), [`test_work_cli_launchd.py`](test_work_cli_launchd.py), [`test_work_cli_runtime.py`](test_work_cli_runtime.py) — JSON-first CLI surfaces, автономный macOS LaunchAgent, runtime status и machine-readable contracts;
  - [`test_work_bootstrap.py`](test_work_bootstrap.py), [`test_work_state.py`](test_work_state.py), [`test_work_type.py`](test_work_type.py) — work bundle bootstrap, next-action/state logic и profile coupling;
  - [`test_dissertation_artifacts.py`](test_dissertation_artifacts.py), [`test_dissertation_standards.py`](test_dissertation_standards.py), [`test_one_shot.py`](test_one_shot.py) — dissertation contour, standards gating и one-shot flows;
  - [`test_regression_harness.py`](test_regression_harness.py) — offline smoke вокруг публичных workflow surfaces.
- [`test_academic_engine.py`](test_academic_engine.py) остаётся большим историческим regression pack: актуальная supported surface — CLI/file-first, а не bot control surface.
- Базовые тематические группы по-прежнему покрывают:
  - [`test_verdict_parser.py`](test_verdict_parser.py) — структурированные verdict-блоки;
  - [`test_ops_alerts.py`](test_ops_alerts.py), [`test_resource_guards.py`](test_resource_guards.py) — ops-каналы и resource guards;
  - [`test_sources_core.py`](test_sources_core.py), [`test_source_connectors.py`](test_source_connectors.py), [`test_source_verifier.py`](test_source_verifier.py) — connectors + verifier;
  - [`test_originality.py`](test_originality.py) — MinHash fingerprint и corpus;
  - [`test_gost_linter.py`](test_gost_linter.py), [`test_docx_conformance.py`](test_docx_conformance.py), [`test_vkr_artifacts.py`](test_vkr_artifacts.py) — ГОСТ, DOCX-conformance и VKR artifacts.

- Зависимости рантайма — только стандартная библиотека Python 3.11+ (см. [pyproject.toml](../pyproject.toml)).
