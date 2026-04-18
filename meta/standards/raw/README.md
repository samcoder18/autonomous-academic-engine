# Raw Standards

Сюда загружаются official-only raw bundles по `profile-id` из `meta/standards/registry.toml`.

Внутри `raw/<profile-id>/` лежат:

- скачанные официальные файлы;
- `manifest.json` с URL, final URL, content type, fetched time, checksum и filename;
- при сетевых сбоях возможен partial manifest с `error`, который должен оставаться видимым blocker-ом.

Типовые источники:

- методички ВКР;
- документы Минобрнауки;
- требования кафедры;
- шаблоны титульных листов;
- требования конкретных журналов.

Пока relevant raw-документы не загружены, academic finalizer обязан отражать это как formatting blocker.
