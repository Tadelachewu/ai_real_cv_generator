import psycopg2
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

def connect_db():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def save_user_comment(user_id: int, username: str, comment: str):
    try:
        conn = connect_db()
        cursor = conn.cursor()

        timestamp = datetime.now()
        cursor.execute("""
            INSERT INTO comments (user_id, username, comment, timestamp)
            VALUES (%s, %s, %s, %s)
        """, (user_id, username, comment, timestamp))

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Comment saved to database.")
    except Exception as e:
        print("❌ Error saving comment:", e)
