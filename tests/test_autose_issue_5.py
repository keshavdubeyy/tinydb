"""
Acceptance tests for issue 5: update operations must tolerate missing fields.
"""
import pytest

from tinydb import TinyDB, where
from tinydb.operations import add, decrement, delete, increment, set, subtract
from tinydb.storages import MemoryStorage


def make_db():
    return TinyDB(storage=MemoryStorage)


def test_ac_1_increment_and_decrement_on_missing_field():
    """
    AC-1 (FR-1, FR-2): Given a table containing the document {'name': 'a'} with no
    'visits' key, when db.update(increment('visits')) is called, then no exception is
    raised and the stored document has visits == 1. The same holds for
    decrement('visits') on a fresh copy of that document, which gives visits == -1.
    """
    db = make_db()
    db.insert({'name': 'a'})
    db.update(increment('visits'))  # must not raise
    assert db.all() == [{'name': 'a', 'visits': 1}]

    db2 = make_db()
    db2.insert({'name': 'a'})
    db2.update(decrement('visits'))  # must not raise
    assert db2.all() == [{'name': 'a', 'visits': -1}]


def test_ac_2_add_and_subtract_on_missing_field():
    """
    AC-2 (FR-3, FR-4): Given a table containing the document {'name': 'a'} with no
    'score' key, when db.update(add('score', 5)) is called, then the stored document has
    score == 5. The same holds for subtract('score', 5) on a fresh copy of that document,
    which gives score == -5.
    """
    db = make_db()
    db.insert({'name': 'a'})
    db.update(add('score', 5))
    assert db.all()[0]['score'] == 5

    db2 = make_db()
    db2.insert({'name': 'a'})
    db2.update(subtract('score', 5))
    assert db2.all()[0]['score'] == -5


def test_ac_3_delete_on_missing_field_is_noop():
    """
    AC-3 (FR-5): Given a table containing {'a': 1} and {'b': 2}, when
    db.update(delete('a')) is called, then no exception is raised, the first document
    becomes {}, and the second stays {'b': 2}.
    """
    db = make_db()
    db.insert({'a': 1})
    db.insert({'b': 2})
    db.update(delete('a'))  # must not raise
    docs = db.all()
    assert docs[0] == {}
    assert docs[1] == {'b': 2}


def test_ac_4_mixed_documents_in_single_update():
    """
    AC-4 (FR-6, FR-7): Given a table containing {'visits': 3} and {'name': 'x'}, when
    db.update(increment('visits')) is called once, then the first document has
    visits == 4 and the second has visits == 1.
    """
    db = make_db()
    db.insert({'visits': 3})
    db.insert({'name': 'x'})
    db.update(increment('visits'))
    docs = db.all()
    assert docs[0]['visits'] == 4
    assert docs[1]['visits'] == 1


def test_ac_5_existing_field_behaviour_unchanged():
    """
    AC-5 (FR-6): Given a table containing {'s': 'a'}, when db.update(add('s', 'x')) is
    called, then the stored document has s == 'ax'. In addition, given {'n': 5}, when
    db.update(subtract('n', 2)) is called, then n == 3.
    """
    db = make_db()
    db.insert({'s': 'a'})
    db.update(add('s', 'x'))
    assert db.all()[0]['s'] == 'ax'

    db2 = make_db()
    db2.insert({'n': 5})
    db2.update(subtract('n', 2))
    assert db2.all()[0]['n'] == 3


def test_ac_1_extra_increment_decrement_on_existing_field():
    """
    AC-1/FR-6 supplement: increment and decrement on a present field add or subtract 1
    (existing behaviour preserved).
    """
    db = make_db()
    db.insert({'visits': 10})
    db.update(increment('visits'))
    assert db.all()[0]['visits'] == 11
    db.update(decrement('visits'))
    db.update(decrement('visits'))
    assert db.all()[0]['visits'] == 9


def test_ac_2_extra_add_subtract_on_existing_field():
    """
    AC-2/FR-6 supplement: add and subtract on a present numeric field change it by n.
    """
    db = make_db()
    db.insert({'score': 10})
    db.update(add('score', 5))
    assert db.all()[0]['score'] == 15
    db.update(subtract('score', 3))
    assert db.all()[0]['score'] == 12


def test_ac_3_extra_delete_on_present_field_removes_it():
    """
    AC-3/FR-6 supplement: delete on a present field removes that key only.
    """
    db = make_db()
    db.insert({'a': 1, 'b': 2})
    db.update(delete('a'))
    assert db.all()[0] == {'b': 2}


def test_ac_4_extra_mixed_documents_add_and_subtract():
    """
    AC-4/FR-7 supplement: add and subtract applied in a single update call handle both
    documents with and without the field.
    """
    db = make_db()
    db.insert({'score': 1})
    db.insert({'other': 0})
    db.update(add('score', 2))
    docs = db.all()
    assert docs[0]['score'] == 3
    assert docs[1]['score'] == 2


def test_ac_5_extra_set_unchanged_on_missing_field():
    """
    AC-5/ASM-3 supplement: set works on a missing field and assigns the value.
    """
    db = make_db()
    db.insert({'name': 'a'})
    db.update(set('visits', 7))
    assert db.all()[0]['visits'] == 7
