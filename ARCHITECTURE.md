# TinyDB — Architecture

TinyDB is a small document database written in pure Python. A database is a set of named
tables; a table is a mapping from integer document IDs to JSON-like dicts. The whole database
state is read from and written to a pluggable *storage* as one nested dict.

## 1. Overview

| Component | File | Responsibility | Depends on |
| --- | --- | --- | --- |
| **TinyDB** (database) | `tinydb/database.py` | Entry point. Creates the storage instance, creates and caches `Table` objects, forwards unknown attributes to the default table (`_default`). | Table, Storage |
| **Table** | `tinydb/table.py` | All document operations (insert, search, update, remove, upsert, …). Owns a per-table LRU query cache and the next-ID counter. Reads/writes the *whole* database through the storage. | Storage, QueryLike, LRUCache |
| **Queries** | `tinydb/queries.py` | Query builder (`Query`, `where`) producing `QueryInstance` objects: callables `doc -> bool` with a stable hash. Supports `&`, `\|`, `~`. | utils (`freeze`) |
| **Storages** | `tinydb/storages.py` | Persistence back-ends behind the abstract `Storage` interface: `JSONStorage` (file) and `MemoryStorage`. | stdlib only |
| **Middlewares** | `tinydb/middlewares.py` | Wrap a storage class and look like a storage to TinyDB. `CachingMiddleware` buffers reads/writes in memory. | Storage |
| **Operations** | `tinydb/operations.py` | Ready-made update transforms (`delete`, `add`, `set`, `increment`, …) passed to `Table.update`. | — |
| **Utils** | `tinydb/utils.py` | `LRUCache`, `freeze`/`FrozenDict` (make values hashable), `with_typehint`. | — |

```mermaid
flowchart LR
    User -->|"TinyDB(path, storage=...)"| DB[TinyDB<br/>database.py]
    DB -->|table(name)| T[Table<br/>table.py]
    User -->|"Query().f == x"| Q[Query / QueryInstance<br/>queries.py]
    T -->|"cond(doc)"| Q
    T -->|LRU query cache| U[utils.py<br/>LRUCache, freeze]
    Q -->|freeze args for hash| U
    T -->|"update(fn)"| O[operations.py]
    DB -->|creates| S{{storage}}
    T -->|"read() / write(all tables)"| S
    S -.is a.-> ST[Storage ABC<br/>JSONStorage / MemoryStorage]
    S -.or.-> MW[Middleware e.g. CachingMiddleware] -->|wraps| ST
```

**Typical call path.** `db.search(q)` → `TinyDB.__getattr__` → default `Table.search(q)` →
look up `q` in the query cache → on miss, `Storage.read()` → test every document with `q(doc)` →
store result in cache if `q.is_cacheable()`. Every write goes through `Table._update_table`:
read all tables, modify this table, convert IDs to `str`, `Storage.write(all tables)`, clear this table's query cache.

**Data format at the storage boundary.** `{table_name: {str(doc_id): document_dict}}`.
`read()` returns `None` when nothing is stored yet.

## 2. Architecture Decision Records

### ADR-1: Storages are a minimal pluggable read/write-everything interface
- **Status:** Verified — `Storage` ABC docstring and `@abstractmethod read/write` (`storages.py:36-76`); `_update_table` docstring: *"The storage interface used by TinyDB only allows to read/write the complete database data"* (`table.py:808-810`).
- **Context:** TinyDB targets small datasets and must support very different back-ends (file, memory, user extensions) with as little code per back-end as possible.
- **Decision:** A storage implements only `read() -> dict | None`, `write(dict)` and optionally `close()`. It always receives/returns the *entire* database. Serialization lives inside the storage. Extra constructor arguments given to `TinyDB(...)` are forwarded to the storage (`database.py:92-95`).
- **Alternatives:** Per-table or per-document storage APIs (more efficient, but every back-end would need to implement partial updates, indexing, ID handling).
- **Consequences:** New back-ends are trivial to write and plug in with `TinyDB(storage=MyStorage)`. Each write re-serializes everything (fine for small DBs, slow for big ones). Storages must not know about tables, queries or document IDs.

### ADR-2: Cross-cutting behaviour via storage-wrapping middlewares
- **Status:** Verified — long explanation in `Middleware.__call__` docstring (`middlewares.py:25-61`).
- **Context:** Features like write buffering or logging should work with *any* storage without modifying TinyDB or each storage.
- **Decision:** A middleware is constructed with a storage **class**, `Middleware(StorageCls)`. TinyDB calls it like a class (`storage(*args, **kwargs)`); `Middleware.__call__` instantiates the real storage with those arguments and returns itself. Unknown attributes are forwarded to the wrapped storage (`__getattr__`). Middlewares can be nested.
- **Alternatives:** Subclassing each storage (combinatorial explosion), or hooks inside `Table` (couples features to the table layer).
- **Consequences:** Any storage that follows the `Storage` contract works with `CachingMiddleware` automatically. With caching, data reaches disk only after `WRITE_CACHE_SIZE` writes, an explicit `flush()`, or `close()` — so users must close the DB. After `close()`, `CachingMiddleware` rejects reads/writes.

### ADR-3: Queries are hashable values so search results can be cached per table
- **Status:** Verified — `QueryInstance.__hash__` comment (*"hashval … describes this query uniquely"*, `queries.py:99-103`); commit `e83efcd` "include regex flags in matches/search query hash" (*"Queries that differed only by flags shared a Table query-cache entry"*).
- **Context:** Repeated identical searches are common; scanning all documents each time is wasteful.
- **Decision:** Each `Table` keeps an `LRUCache` (default capacity 10) keyed by the query object. Every query carries a `hashval` tuple built in `Query._generate_test` from the operation name, the field path (`self._path`) and the arguments (passed through `freeze` where they may be unhashable); equality and hashing use it. Queries whose hashval is `None` (e.g. after `map()`, or unhashable args) are not cached. Any write clears the table's cache.
- **Alternatives:** No caching (simpler, slower); caching keyed by `repr` or identity (fragile/useless).
- **Consequences:** Correctness of `search` depends on the hashval being complete: two queries that can match different documents **must** have different hashvals, otherwise the cache returns wrong results. New query operators should be built with `_generate_test` and include every argument that affects the result.

## 3. Constraints (rules not to break)

| # | Constraint | Where it can be seen |
| --- | --- | --- |
| C1 | New storages must subclass `Storage` and implement `read`/`write` (and `close` if they hold resources); `read` returns `None` for an empty store. | `tinydb/storages.py:36-76` |
| C2 | The core library has **no third-party runtime dependencies**; core modules import only the standard library. (Sole exception: the optional `mypy_plugin.py`, used only by type checkers.) | `pyproject.toml` (`[project]` has no `dependencies` entry); imports in `tinydb/*.py`, `tinydb/mypy_plugin.py:4-6` |
| C3 | Storages and middlewares must stay independent of tables and queries: `storages.py` imports nothing from `tinydb`, and `table.py` talks to storage only through `read()`/`write()`. | `tinydb/storages.py:6-11`, `tinydb/table.py:789, 818, 851` |
| C4 | A query's hashval must uniquely describe what it matches (operation, path, arguments); new operators go through `Query._generate_test`. | `tinydb/queries.py:218-252`, `tinydb/table.py:265, 291-295` |
| C5 | Document IDs are stored as strings and converted back to `document_id_class` (int) in `Table`; storages must accept and return string keys unchanged. | `tinydb/table.py:831-845` |

Public names exposed to users are listed in `tinydb/__init__.py` (`__all__`); a new storage meant for end users should be exported there like `JSONStorage`.
