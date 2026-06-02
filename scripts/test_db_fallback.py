import sys
from sqlalchemy import text

sys.path.insert(0, 'waypoint')

from backend.db.database import create_db_and_tables, get_session

print('initializing tables...')
create_db_and_tables()

with get_session() as session:
    value = session.exec(text('SELECT 1')).one()
    print('session ok:', value)
