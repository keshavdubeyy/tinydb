import pytest

from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def _db():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{'n': 1}, {'n': 2}, {'n': 'oops'}, {'n': 4}])
    return db


def bump(doc):
    doc['n'] = doc['n'] + 10


def test_failed_update_changes_nothing():
    db = _db()
    with pytest.raises(TypeError):
        db.update(bump)
    assert [d['n'] for d in db.all()] == [1, 2, 'oops', 4]


def test_failed_update_with_query_changes_nothing():
    db = _db()
    with pytest.raises(TypeError):
        db.update(bump, where('n').exists())
    assert [d['n'] for d in db.all()] == [1, 2, 'oops', 4]


def test_failed_update_multiple_changes_nothing():
    db = _db()
    with pytest.raises(TypeError):
        db.update_multiple([(bump, where('n').exists())])
    assert [d['n'] for d in db.all()] == [1, 2, 'oops', 4]


def test_successful_update_still_works():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{'n': 1}, {'n': 2}])
    db.update(bump)
    assert [d['n'] for d in db.all()] == [11, 12]
