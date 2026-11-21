"""
Tests for the optional ``limit`` argument of ``Table.search()``.
"""

from tinydb import Query, TinyDB
from tinydb.storages import MemoryStorage


def make_table():
    db = TinyDB(storage=MemoryStorage)
    return db.table('logs')


def populate_10(table):
    """Insert 10 docs; levels: 8 'error' and 2 'info', interleaved."""
    levels = ['error', 'info', 'error', 'error', 'info',
              'error', 'error', 'error', 'error', 'error']
    table.insert_multiple([{'n': i, 'level': lvl} for i, lvl in enumerate(levels)])


def ids(docs):
    return [d.doc_id for d in docs]


def test_ac_1_limit_returns_first_five_of_eight_matches():
    """AC-1: Given 10 docs, 8 matching Query().level == 'error', when
    table.search(cond, limit=5) is called, then it returns exactly 5 documents
    equal to the first 5 elements of table.search(cond), in the same order."""
    table = make_table()
    populate_10(table)
    cond = Query().level == 'error'

    full = table.search(cond)
    limited = table.search(cond, limit=5)

    assert len(full) == 8
    assert isinstance(limited, list)
    assert len(limited) == 5
    assert limited == full[:5]
    assert ids(limited) == ids(full)[:5]


def test_ac_2_limit_larger_than_matches_returns_all_without_error():
    """AC-2: Given a table where only 3 documents match cond, when
    table.search(cond, limit=5) is called, then all 3 matches are returned
    and no error is raised."""
    table = make_table()
    table.insert_multiple([
        {'level': 'error'}, {'level': 'info'}, {'level': 'error'},
        {'level': 'warn'}, {'level': 'error'},
    ])
    cond = Query().level == 'error'

    result = table.search(cond, limit=5)

    assert len(result) == 3
    assert all(doc['level'] == 'error' for doc in result)
    assert ids(result) == ids(table.search(cond))


def test_ac_3_no_limit_and_limit_none_return_full_list():
    """AC-3: Given a populated table, when table.search(cond) and
    table.search(cond, limit=None) are called, then both return the same full
    list of matches as before the change (all 8 'error' docs, in order)."""
    table = make_table()
    populate_10(table)
    cond = Query().level == 'error'

    plain = table.search(cond)
    with_none = table.search(cond, limit=None)

    expected_ids = [doc.doc_id for doc in table if doc['level'] == 'error']
    assert len(plain) == 8
    assert ids(plain) == expected_ids
    assert plain == with_none
    assert ids(with_none) == expected_ids


def test_ac_4_limited_search_does_not_poison_cache():
    """AC-4: Given a fresh table and a cacheable cond with more than 2 matches,
    when table.search(cond, limit=2) is called and then table.search(cond) is
    called, then the second call returns all matching documents, not 2."""
    table = make_table()
    populate_10(table)
    cond = Query().level == 'error'

    limited = table.search(cond, limit=2)
    assert len(limited) == 2

    full = table.search(cond)
    assert len(full) == 8
    assert ids(full) == [doc.doc_id for doc in table if doc['level'] == 'error']


def test_ac_5_limit_on_cached_query_returns_prefix_and_keeps_full_cache():
    """AC-5: Given the same cond was already searched without limit (so it is
    cached), when table.search(cond, limit=2) is called, then it returns the
    first 2 matches. A following table.search(cond) still returns the full
    list."""
    table = make_table()
    populate_10(table)
    cond = Query().level == 'error'

    first_full = table.search(cond)
    assert len(first_full) == 8

    limited = table.search(cond, limit=2)
    assert len(limited) == 2
    assert limited == first_full[:2]

    again_full = table.search(cond)
    assert len(again_full) == 8
    assert again_full == first_full
