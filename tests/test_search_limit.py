import pytest

from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def _logs():
    db = TinyDB(storage=MemoryStorage)
    t = db.table('logs')
    t.insert_multiple([{'level': 'error', 'n': i} for i in range(10)])
    t.insert({'level': 'info', 'n': 99})
    return t


def test_limit_returns_at_most_n_results_in_order():
    t = _logs()
    res = t.search(where('level') == 'error', limit=5)
    assert [d['n'] for d in res] == [0, 1, 2, 3, 4]


def test_limit_larger_than_matches_returns_all():
    t = _logs()
    assert len(t.search(where('level') == 'error', limit=50)) == 10


def test_no_limit_works_like_before():
    t = _logs()
    assert len(t.search(where('level') == 'error')) == 10


def test_negative_limit_is_rejected():
    t = _logs()
    with pytest.raises(ValueError):
        t.search(where('level') == 'error', limit=-1)
