"""
Tests for: Clear a Table's query cache when the table is dropped.

Each test docstring quotes the acceptance criterion it checks.
"""

from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage


def _db():
    return TinyDB(storage=MemoryStorage)


def test_ac_1_search_after_drop_table_returns_empty(tmp_path):
    """
    AC-1 (FR-1): Given users = db.table('users'), the document
    {'name': 'alice'} inserted, and users.search(Query().name == 'alice')
    already called, when db.drop_table('users') is called, then
    users.search(Query().name == 'alice') returns [].
    """
    db = _db()
    users = db.table('users')
    users.insert({'name': 'alice'})
    cond = Query().name == 'alice'
    assert users.search(cond) == [{'name': 'alice'}]

    db.drop_table('users')

    assert users.search(cond) == []


def test_ac_2_count_after_drop_table_returns_zero(tmp_path):
    """
    AC-2 (FR-2): Given the same setup, with users.count(cond) equal to 1
    before the drop, when db.drop_table('users') is called, then
    users.count(cond) returns 0.
    """
    db = _db()
    users = db.table('users')
    users.insert({'name': 'alice'})
    cond = Query().name == 'alice'
    assert users.count(cond) == 1

    db.drop_table('users')

    assert users.count(cond) == 0


def test_ac_3_drop_tables_clears_search_and_count_on_all_retained_tables(tmp_path):
    """
    AC-3 (FR-3): Given tables a and b, each holding {'name': 'alice'} and each
    with a cached search(cond) for Query().name == 'alice', when db.drop_tables()
    is called, then a.search(cond) and b.search(cond) both return [], and
    a.count(cond) and b.count(cond) both return 0.
    """
    db = _db()
    a = db.table('a')
    b = db.table('b')
    a.insert({'name': 'alice'})
    b.insert({'name': 'alice'})
    cond = Query().name == 'alice'
    assert a.search(cond) == [{'name': 'alice'}]
    assert b.search(cond) == [{'name': 'alice'}]

    db.drop_tables()

    assert a.search(cond) == []
    assert b.search(cond) == []
    assert a.count(cond) == 0
    assert b.count(cond) == 0


def test_ac_4_drop_one_table_does_not_affect_other_table(tmp_path):
    """
    AC-4 (FR-4): Given tables a and b that each hold {'name': 'alice'} and each
    have a cached search(cond), when db.drop_table('a') is called, then
    b.search(cond) returns [{'name': 'alice'}].
    """
    db = _db()
    a = db.table('a')
    b = db.table('b')
    a.insert({'name': 'alice'})
    b.insert({'name': 'alice'})
    cond = Query().name == 'alice'
    assert a.search(cond) == [{'name': 'alice'}]
    assert b.search(cond) == [{'name': 'alice'}]

    db.drop_table('a')

    assert b.search(cond) == [{'name': 'alice'}]


def test_ac_5_retained_table_sees_documents_inserted_after_drop(tmp_path):
    """
    AC-5 (FR-5): Given users holds {'name': 'alice'} with users.search(cond)
    cached, when db.drop_table('users') is called and then
    users.insert({'name': 'alice', 'id': 2}) is called on the old reference,
    then users.search(cond) returns exactly [{'name': 'alice', 'id': 2}] and
    users.count(cond) returns 1.
    """
    db = _db()
    users = db.table('users')
    users.insert({'name': 'alice'})
    cond = Query().name == 'alice'
    assert users.search(cond) == [{'name': 'alice'}]

    db.drop_table('users')
    users.insert({'name': 'alice', 'id': 2})

    results = users.search(cond)
    assert [dict(doc) for doc in results] == [{'name': 'alice', 'id': 2}]
    assert users.count(cond) == 1
