# Тесты `telegram_console`

- Полный прогон (из корня репозитория):

  ```bash
  python3 -m unittest discover -s tests -q
  ```

- Основной интеграционный модуль: [`test_telegram_console.py`](test_telegram_console.py).
- Автономный движок (Фазы 0–7):
  - [`test_verdict_parser.py`](test_verdict_parser.py) — структурированные verdict-блоки;
  - [`test_ops_alerts.py`](test_ops_alerts.py), [`test_resource_guards.py`](test_resource_guards.py) — ops-каналы и resource guards;
  - [`test_sources_core.py`](test_sources_core.py), [`test_source_connectors.py`](test_source_connectors.py), [`test_source_verifier.py`](test_source_verifier.py) — connectors + verifier;
  - [`test_originality.py`](test_originality.py) — MinHash fingerprint и corpus;
  - [`test_gost_linter.py`](test_gost_linter.py), [`test_docx_conformance.py`](test_docx_conformance.py) — ГОСТ и DOCX-conformance;
  - [`test_vkr_artifacts.py`](test_vkr_artifacts.py), [`test_work_type.py`](test_work_type.py), [`test_one_shot.py`](test_one_shot.py), [`test_regression_harness.py`](test_regression_harness.py) — VKR-артефакты, work-type-профили и end-to-end smoke.

- Зависимости рантайма — только стандартная библиотека Python 3.11+ (см. [pyproject.toml](../pyproject.toml)).

При дальнейшем развитии допустимо разнести классы `test_telegram_console.py` по пакету `tests/test_telegram_console/` с общими фикстурами в `tests/support/`, сохранив discoverability через `unittest discover -s tests`.
