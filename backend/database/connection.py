from os import getenv
from dotenv import load_dotenv
from psycopg2 import connect, OperationalError
from psycopg2.extras import RealDictCursor

load_dotenv()
print("USERNAME:", getenv("USERNAME"))
print("PASSWORD:", getenv("PASSWORD"))
print("HOST:", getenv("HOST"))
print("PORT:", getenv("PORT"))
def get_connection():
    return connect(
        user=getenv("DB_USERNAME"),
        password=getenv("PASSWORD"),
        host=getenv("HOST"),
        port=getenv("PORT"),
        dbname=getenv("DATABASE"),
        sslmode="require",
    )

try:
    CONN = get_connection()
    CURSOR: RealDictCursor = CONN.cursor(cursor_factory=RealDictCursor)
except (OperationalError, ValueError) as e:
    print(f"Failed to connect: {e}")
    raise