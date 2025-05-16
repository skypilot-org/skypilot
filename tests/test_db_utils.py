import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from sky.utils.db_utils import add_column_to_table_sqlalchemy


@pytest.fixture
def sqlalchemy_sqlite_session():
    """Create a SQLite session for testing."""
    engine = create_engine('sqlite:///:memory:')
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create a test table
    session.execute(
        text('''
        CREATE TABLE test_table (
            id INTEGER PRIMARY KEY,
            name TEXT
        )
    '''))
    session.commit()

    return session


def test_add_column_basic(sqlalchemy_sqlite_session):
    """Test adding a basic column to a table."""
    add_column_to_table_sqlalchemy(sqlalchemy_sqlite_session, 'test_table',
                                   'age', 'INTEGER')

    # Verify the column was added
    result = sqlalchemy_sqlite_session.execute(
        text('PRAGMA table_info(test_table)')).fetchall()
    column_names = [row[1] for row in result]
    assert 'age' in column_names


def test_add_column_with_copy(sqlalchemy_sqlite_session):
    """Test adding a column and copying values from another column."""
    # Add some test data
    sqlalchemy_sqlite_session.execute(
        text('''
        INSERT INTO test_table (name) VALUES ('Alice'), ('Bob')
    '''))

    add_column_to_table_sqlalchemy(sqlalchemy_sqlite_session,
                                   'test_table',
                                   'name_copy',
                                   'TEXT',
                                   copy_from='name')

    # Verify the values were copied
    result = sqlalchemy_sqlite_session.execute(
        text('SELECT name, name_copy FROM test_table')).fetchall()
    for row in result:
        assert row[0] == row[1]


def test_add_column_with_default_value(sqlalchemy_sqlite_session):
    """Test adding a column with a default value."""
    add_column_to_table_sqlalchemy(
        sqlalchemy_sqlite_session,
        'test_table',
        'test1',
        'TEXT DEFAULT "default_value"',
    )
    # add a test entry
    sqlalchemy_sqlite_session.execute(
        text('''
        INSERT INTO test_table (name) VALUES ('name')
    '''))

    # Verify the default value was set
    result = sqlalchemy_sqlite_session.execute(
        text('SELECT test1 FROM test_table')).fetchall()
    assert len(result) == 1
    for row in result:
        assert row[0] == 'default_value'


def test_add_column_with_value_to_replace_existing_entries(
        sqlalchemy_sqlite_session):
    """Test adding a column with a default value for existing entries."""
    # add a test entry
    sqlalchemy_sqlite_session.execute(
        text('''
        INSERT INTO test_table (name) VALUES ('existing_value')
    '''))

    # add a new column with a default value for existing entries
    add_column_to_table_sqlalchemy(
        sqlalchemy_sqlite_session,
        'test_table',
        'test1',
        'TEXT',
        value_to_replace_existing_entries='replacement_value1')

    # Verify the default value was set
    result = sqlalchemy_sqlite_session.execute(
        text('SELECT test1 FROM test_table')).fetchall()
    assert len(result) == 1
    for row in result:
        assert row[0] == 'replacement_value1'

    # add a new column with a different column type
    add_column_to_table_sqlalchemy(sqlalchemy_sqlite_session,
                                   'test_table',
                                   'test2',
                                   'INTEGER',
                                   value_to_replace_existing_entries=1)

    # Verify the default value was set
    result = sqlalchemy_sqlite_session.execute(
        text('SELECT test2 FROM test_table')).fetchall()
    assert len(result) == 1
    for row in result:
        assert row[0] == 1


def test_add_duplicate_column(sqlalchemy_sqlite_session):
    """Test adding a column that already exists."""
    # First add the column
    add_column_to_table_sqlalchemy(sqlalchemy_sqlite_session, 'test_table',
                                   'age', 'INTEGER')

    # Try to add it again - should not raise an error
    add_column_to_table_sqlalchemy(sqlalchemy_sqlite_session, 'test_table',
                                   'age', 'INTEGER')

    # Verify the column still exists
    result = sqlalchemy_sqlite_session.execute(
        text('PRAGMA table_info(test_table)')).fetchall()
    column_names = [row[1] for row in result]
    assert 'age' in column_names
