"""Tests for making Table.update() atomic on MemoryStorage (issue 2)."""
import pytest

from tinydb import TinyDB, where
from tinydb.operations import add
from tinydb.storages import MemoryStorage


def _make_db():
    db = TinyDB(storage=MemoryStorage)
    db.insert({'score': 1})
    db.insert({'score': 2})
    db.insert({'score': 'oops'})
    return db


def test_ac_1_failed_update_leaves_all_docs_unchanged():
    """AC-1: Given a TinyDB(storage=MemoryStorage) containing {'score': 1},
    {'score': 2}, {'score': 'oops'}, when db.update(add('score', 10)) is called
    and raises TypeError, then db.all() equals
    [{'score': 1}, {'score': 2}, {'score': 'oops'}]."""
    db = _make_db()

    with pytest.raises(TypeError):
        db.update(add('score', 10))

    assert db.all() == [{'score': 1}, {'score': 2}, {'score': 'oops'}]


def test_ac_2_failed_update_with_cond_leaves_docs_unchanged():
    """AC-2: Given the same database, when db.update(add('score', 10),
    where('score') != 'zzz') (the cond= form) raises TypeError, then db.all()
    is unchanged."""
    db = _make_db()

    with pytest.raises(TypeError):
        db.update(add('score', 10), where('score') != 'zzz')

    assert db.all() == [{'score': 1}, {'score': 2}, {'score': 'oops'}]


def test_ac_2_failed_update_with_doc_ids_leaves_docs_unchanged():
    """AC-2: Given the same database, when db.update(add('score', 10),
    doc_ids=[1, 2, 3]) raises TypeError, then db.all() is unchanged."""
    db = _make_db()

    with pytest.raises(TypeError):
        db.update(add('score', 10), doc_ids=[1, 2, 3])

    assert db.all() == [{'score': 1}, {'score': 2}, {'score': 'oops'}]


def test_ac_3_callable_fields_failure_rolls_back_earlier_mutations():
    """AC-3: Given a MemoryStorage database with several documents and a user
    callable passed as fields that mutates the document and then raises on the
    last one, when db.update(callable) is called, then the exception propagates
    and db.all() equals the original contents, including the first documents
    which the callable had already mutated."""
    db = TinyDB(storage=MemoryStorage)
    db.insert({'name': 'a', 'count': 0})
    db.insert({'name': 'b', 'count': 0})
    db.insert({'name': 'c', 'count': 0})
    original = [{'name': 'a', 'count': 0}, {'name': 'b', 'count': 0},
                {'name': 'c', 'count': 0}]

    def bump_then_fail_on_last(doc):
        doc['count'] = doc['count'] + 1
        if doc['name'] == 'c':
            raise RuntimeError('boom')

    with pytest.raises(RuntimeError):
        db.update(bump_then_fail_on_last)

    assert db.all() == original


def test_ac_4_successful_update_returns_ids_and_updates_docs():
    """AC-4: Given a MemoryStorage database with {'score': 1} and {'score': 2},
    when db.update(add('score', 10)) is called, then it returns [1, 2] and
    db.all() equals [{'score': 11}, {'score': 12}]."""
    db = TinyDB(storage=MemoryStorage)
    db.insert({'score': 1})
    db.insert({'score': 2})

    result = db.update(add('score', 10))

    assert result == [1, 2]
    assert db.all() == [{'score': 11}, {'score': 12}]
