# Helpers for DB
from datetime import datetime
import os
import sqlite3
from typing import Dict, Optional
from venv import logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, 'temp')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

# Ensure directories exist
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)



def save_user_data(user_id: int, data: Dict):
    """Save user data to database"""
    conn = None
    try:
        conn = sqlite3.connect(os.path.join(BASE_DIR, "cv_bot.db"))
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO users 
                    (user_id, data, last_updated) 
                    VALUES (?, ?, ?)''',
                 (user_id, str(data), datetime.now().isoformat()))
        conn.commit()
    except Exception as e:
        raise RuntimeError(f"Database error: {str(e)}")
    finally:
        if conn:
            conn.close()

def load_user_data(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(os.path.join(BASE_DIR, "cv_bot.db"))
    c = conn.cursor()
    c.execute("SELECT data FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return eval(row[0])
        except Exception as e:
            logger.error(f"Error evaluating user data: {e}")
            return None
    return None
