# Тесты `telegram_console`

- Основной набор: [`test_telegram_console.py`](test_telegram_console.py) — крупный модуль интеграционных и модульных проверок на `unittest`.
- Запуск из корня репозитория:

  ```bash
  python3 -m unittest tests.test_telegram_console
  ```

- Зависимости рантайма — только стандартная библиотека Python 3.11+ (см. [pyproject.toml](../pyproject.toml)).

При дальнейшем развитии допустимо разнести классы по `tests/test_telegram_console/*.py` с общими фикстурами в `tests/support/`, сохранив имя модуля для обратной совместимости или обновив путь в CI.
