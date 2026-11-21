from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def _table():
    db = TinyDB(storage=MemoryStorage)
    t = db.table('items')
    t.insert_multiple([{'kind': 'a'}, {'kind': 'b'}, {'kind': 'a'}])
    return db, t


def test_count_without_query_counts_all_documents():
    _, t = _table()
    assert t.count() == 3


def test_count_without_query_on_database_uses_default_table():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{'x': 1}, {'x': 2}])
    assert db.count() == 2 == len(db)


def test_count_without_query_on_empty_table_is_zero():
    db = TinyDB(storage=MemoryStorage)
    assert db.table('empty').count() == 0


def test_count_with_query_is_unchanged():
    _, t = _table()
    assert t.count(where('kind') == 'a') == 2
    assert t.count(where('kind') == 'zzz') == 0
