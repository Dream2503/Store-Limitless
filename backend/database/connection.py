from os import getenv

from psycopg2 import connect
from psycopg2.extras import RealDictCursor

try:
    CURSOR: RealDictCursor = connect(
            user=getenv("DB_USERNAME"),
            password=getenv("DB_PASSWORD"),
            host=getenv("DB_HOST"),
            port=getenv("DB_PORT"),
            dbname=getenv("DB_DATABASE"),
            sslmode="require",
    ).cursor(cursor_factory=RealDictCursor)

except Exception as e:
    print(f"Failed to connect: {e}")
