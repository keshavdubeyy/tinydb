from tinydb import TinyDB
from tinydb.storages import MemoryStorage


def test_drop_table_removes_table():
    db = TinyDB(storage=MemoryStorage)
    users = db.table('users')
    users.insert({'name': 'John'})
    db.drop_table('users')
    assert 'users' not in db.tables()


def test_drop_tables_does_not_raise():
    db = TinyDB(storage=MemoryStorage)
    db.table('users').insert({'name': 'John'})
    db.drop_tables()
    assert db.tables() == set()
