# sibyl-memory-client 0.8.0 — public API

Read from the installed source at
`.venv/lib/python3.12/site-packages/sibyl_memory_client/` on 2026-09-04.
Nothing here is taken from published docs; line references are to that source.

## Construction

`MemoryClient` is exported from the package root (`from sibyl_memory_client import MemoryClient`).

```python
MemoryClient.local(
    path: str | Path = "~/.sibyl-memory/memory.db",
    *,
    tenant_id: str = DEFAULT_TENANT,
    tier: str = "free",
    account_id: str | None = None,
    session_token: str | None = None,
    credentials_claim: dict | None = None,
    credentials_signature: str | None = None,
) -> MemoryClient                                   # client.py:755
```

- **`.local()` DOES accept a custom db path** — it is the first positional
  argument. `Storage(path)` (`storage.py:181`) expands `~`, refuses symlinked
  and hardlinked db files and sidecars, creates the parent directory with mode
  `0700`, applies the schema idempotently, and chmods the db file to `0600`.
  turnstyl passes `./data/turnstyl.db`.
- **`tenant_id` is a plain constructor keyword**, defaulting to
  `DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"` (`client.py:507`).
  **The SDK never reads `~/.sibyl-memory/credentials.json`** — grepping the
  whole package finds it only in docstrings and comments. `account_id` /
  `session_token` / `tier` are values the *caller* is expected to have loaded
  from that file; nothing in the SDK opens it. So a tenant is not "supplied by
  credentials"; it is whatever string you pass. turnstyl passes the fixed
  tenant `"turnstyl"`.
- `tenant_id` is run through `validate_identifier` at construction and in
  `set_tenant` (`client.py:86`): must be a non-empty `str`, no control
  characters (0x00–0x1F, 0x7F), no `..`, none of `< > | ; " \``, ≤ 1024 chars.
  It need not be a UUID. `:` and `/` are allowed, so `"job:demo"` is a valid key.
- Also public: `MemoryClient(storage: Storage, *, tenant_id=..., tier=..., ...)`
  for an already-built `Storage`; `get_tenant()`, `set_tenant()`, `get_tier()`,
  `set_tier()`, `schema_version()`, `.storage` property.
- The 5 MB free-tier cap gate runs on every write. With `account_id=None` and a
  DB under the cap it is **purely local — no network call** (`_capcheck.py:480`).
  The usage heartbeat is a no-op without an `account_id` (`_heartbeat.py:95`).

## Tier methods — exact signatures

All are methods on `MemoryClient`; every read and write is scoped by
`tenant_id` in SQL.

### HOT — state documents
```python
set_state(key: str, body: dict | list) -> None                       # client.py:986
get_state(key: str) -> dict | None                                   # client.py:1006
```
`set_state` upserts on `(tenant_id, document_key)`; `key` is validated, `body`
must be a dict or list (a bare str/int is rejected by `_require_container`).
`get_state` returns `{"body": <decoded JSON>, "updated_at": <iso>}`, or `None`
when the key is unset — it does **not** raise.

### WARM — entities
```python
set_entity(category: str, name: str, body: dict | list, *,
           status: str | None = None) -> dict                        # client.py:887
get_entity(category: str, name: str) -> dict                         # client.py:940
list_entities(category: str | None = None, *, status: str | None = None,
              limit: int = 100) -> list[dict]                        # client.py:952
delete_entity(category: str, name: str) -> bool                      # client.py:974
archive_entity(category: str, name: str,
               reason: str | None = None) -> dict                    # client.py:1146
search_entities(query: str, *, limit: int = 20, prefix: bool = False,
                category: str | None = None) -> SearchResults        # client.py:1290
```
- `set_entity` upserts on `UNIQUE (tenant_id, category, name)` and **returns the
  resulting row** (it re-reads via `get_entity`).
- A row is `{id, tenant_id, category, name, status, body, created_at,
  updated_at}` with `body` JSON-decoded (`client.py:1869`).
- **`get_entity` raises `NotFoundError`** when absent — it does not return
  `None`. This is the one asymmetry with `get_state`/`get_reference`.
- `delete_entity` returns `True` if a row was removed, `False` if none matched.
- `archive_entity` copies the row into `archived_entities`, deletes the original,
  and returns `{"archived_id": ..., "original_id": ...}`. Raises `NotFoundError`
  before any write if the entity does not exist.
- `search_entities` is FTS5 over name + category + body, warm tier only. It
  returns `verdicts.SearchResults`, a **`list` subclass** (so `len`, iteration,
  indexing, `json.dumps` all behave as with a plain list) carrying an extra
  `.verdict` of `OK` or `NO_MATCH`. An empty/invalid query yields `[]` with
  `tokens_total=0`, not an exception. `MemoryClient.search()` (`client.py:1372`)
  is the cross-tier variant.

### COLD — journal (append-only)
```python
write_event(*, evaluated=None, acted=None, forward=None,
            extra=None, ts: str | None = None) -> str                # client.py:1019
read_events(*, limit: int = 50, since: str | None = None,
            until: str | None = None) -> list[dict]                  # client.py:1056
```
- `write_event` is **keyword-only** — there is no positional payload. All four
  payloads are optional, JSON-serializable, and stored as `NULL` when omitted.
  Returns the new event's UUID. `ts` defaults to now in ISO-8601 UTC (ms).
- `read_events` returns `{id, ts, evaluated, acted, forward, extra}` per row,
  **newest first** (`ORDER BY ts DESC, id DESC`), payloads JSON-decoded.
  `since`/`until` are inclusive ISO-8601 string comparisons.

### REFERENCE — static documents
```python
set_reference(key: str, body: str | dict | list, *,
              metadata: dict | None = None) -> None                  # client.py:1092
get_reference(key: str) -> dict | None                               # client.py:1133
```
`body` accepts a `str`, or a dict/list which is canonicalized to a JSON string
on write. **`get_reference` returns `body` as a `str`, never decoded** —
`{"body": str, "metadata": <decoded JSON | None>, "updated_at": <iso>}`, or
`None` when absent. `json.loads()` it yourself if you stored structured data.

## Errors

`sibyl_memory_client.exceptions`: `SibylMemoryError` (base), `StorageError`,
`SchemaError`, `TenantError`, `NotFoundError`, `ConflictError`,
`ValidationError`, `TierGateError`; plus `CapExceededError` and
`TierVerificationError` from `_capcheck`. `learn()` and `lint()` are paid-tier
only and raise `TierGateError` on the free tier — turnstyl does not use them.
