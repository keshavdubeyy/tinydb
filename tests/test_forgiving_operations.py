from tinydb import TinyDB
from tinydb.operations import add, decrement, delete, increment, subtract
from tinydb.storages import MemoryStorage


def _db():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{'name': 'old', 'visits': 5, 'tag': 't'}, {'name': 'new'}])
    return db


def values(db, field):
    return [d.get(field) for d in db.all()]


def test_increment_missing_field_starts_at_zero():
    db = _db()
    db.update(increment('visits'))
    assert values(db, 'visits') == [6, 1]


def test_decrement_missing_field_starts_at_zero():
    db = _db()
    db.update(decrement('visits'))
    assert values(db, 'visits') == [4, -1]


def test_add_and_subtract_missing_field():
    db = _db()
    db.update(add('visits', 10))
    assert values(db, 'visits') == [15, 10]
    db = _db()
    db.update(subtract('visits', 2))
    assert values(db, 'visits') == [3, -2]


def test_add_string_to_missing_field():
    db = _db()
    db.update(add('nick', 'x'))
    assert values(db, 'nick') == ['x', 'x']


def test_delete_skips_documents_without_the_field():
    db = _db()
    db.update(delete('tag'))
    assert all('tag' not in d for d in db.all())
    assert values(db, 'name') == ['old', 'new']
