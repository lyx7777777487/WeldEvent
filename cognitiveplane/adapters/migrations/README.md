# Alembic migrations — Phase 1 placeholder

This directory hosts Alembic migration scripts for the cognitive plane's
Postgres schema described in `cognitiveplane/adapters/database/models.py`.

Phase 1 keeps the directory empty on disk; Phase 2 introduces:
- `env.py` (Alembic environment wired to `AsyncDatabaseEngine`)
- `versions/0001_initial.py` (creates the six tables in `ALL_TABLES`)
- A `migrate` CLI entry point in `cognitiveplane/adapters/database/__init__.py`

Until then, the in-memory repos under `cognitiveplane/adapters/database/`
serve as the runtime backing store and tests never touch a real database.
