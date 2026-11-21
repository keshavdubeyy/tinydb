"""
Tests for issue 3: count() must work without a query argument.
"""
import pytest

from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage


def make_table(docs):
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t')
    for doc in docs:
        table.insert(doc)
    return db, table


def test_ac_1_table_count_without_argument_returns_total():
    """
    AC-1 (FR-1): Given a table holding 3 documents, when `table.count()` is
    called with no argument, then it returns `3`.
    """
    _, table = make_table([{'x': 1}, {'x': 2}, {'x': 3}])
    assert table.count() == 3


def test_ac_2_tinydb_count_without_argument_forwards_to_default_table():
    """
    AC-2 (FR-2): Given a `TinyDB` whose default table holds 3 documents, when
    `db.count()` is called, then it returns `3`, equal to `len(db)`.
    """
    db = TinyDB(storage=MemoryStorage)
    for i in range(3):
        db.insert({'x': i})
    result = db.count()
    assert result == 3
    assert result == len(db)


def test_ac_3_count_without_argument_on_empty_table_returns_zero():
    """
    AC-3 (FR-1): Given an empty table, when `count()` is called with no
    argument, then it returns `0`.
    """
    _, table = make_table([])
    assert table.count() == 0


def test_ac_4_count_with_query_still_counts_matches():
    """
    AC-4 (FR-3): Given a table with documents where 2 of 3 match
    `Query().x == 1`, when `table.count(Query().x == 1)` is called, then it
    returns `2`.
    """
    _, table = make_table([{'x': 1}, {'x': 1}, {'x': 2}])
    assert table.count(Query().x == 1) == 2
