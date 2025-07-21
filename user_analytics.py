import os
import logging
import psycopg2
from datetime import datetime
from typing import Dict, Any, Union
import json
from contextlib import contextmanager
from telegram import Update

# Initialize logger
logger = logging.getLogger(__name__)

class UserAnalytics:
    def __init__(self):
        self.db_url = os.getenv('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable not set")
        self._init_db()

    @contextmanager
    def _get_cursor(self):
        conn = psycopg2.connect(self.db_url)
        try:
            cursor = conn.cursor()
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}", exc_info=True)
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database tables with username support"""
        with self._get_cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_actions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id),
                    action_type TEXT NOT NULL,
                    action_data JSONB,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_feedback (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id),
                    username TEXT,
                    rating INTEGER,
                    comments TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
    CREATE TABLE IF NOT EXISTS comments (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        username TEXT,
        comment TEXT,
        timestamp TIMESTAMP
    )
''')

            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id),
                    username TEXT,
                    session_start TIMESTAMP NOT NULL,
                    session_end TIMESTAMP,
                    actions_count INTEGER DEFAULT 0
                )
            ''')

    def _update_user_info(self, user_id: int, username: str = None, 
                         first_name: str = None, last_name: str = None):
        """Update or create user record"""
        with self._get_cursor() as cursor:
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    username = COALESCE(EXCLUDED.username, users.username),
                    first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                    last_name = COALESCE(EXCLUDED.last_name, users.last_name),
                    last_seen = CURRENT_TIMESTAMP
            ''', (user_id, username, first_name, last_name))

    def _get_user_info(self, user_source: Union[Update, int]):
        """Extract user info from either Update object or user_id"""
        if isinstance(user_source, Update):
            user = user_source.effective_user
            return user.id, user.username, user.first_name, user.last_name
        return user_source, None, None, None

    def log_action(self, user_source: Union[Update, int], action_type: str, action_data: Dict[str, Any] = None):
        """Log a user action with username"""
        user_id, username, first_name, last_name = self._get_user_info(user_source)
        try:
            self._update_user_info(user_id, username, first_name, last_name)
            
            with self._get_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO user_actions (user_id, action_type, action_data)
                    VALUES (%s, %s, %s)
                ''', (user_id, action_type, json.dumps(action_data) if action_data else None))
        except Exception as e:
            logger.error(f"Error logging action: {e}", exc_info=True)

    def start_session(self, user_source: Union[Update, int]):
        """Record session start with username"""
        user_id, username, first_name, last_name = self._get_user_info(user_source)
        try:
            self._update_user_info(user_id, username, first_name, last_name)
            
            with self._get_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO user_sessions (user_id, username, session_start)
                    VALUES (%s, %s, %s)
                ''', (user_id, username, datetime.now()))
        except Exception as e:
            logger.error(f"Error starting session: {e}", exc_info=True)

    def record_feedback(self, user_source: Union[Update, int], rating: int = None, comments: str = None):
        """Store user feedback with username"""
        try:
            user_id, username, first_name, last_name = self._get_user_info(user_source)
            logger.info(f"Recording feedback for user {user_id}, rating: {rating}")
            
            self._update_user_info(user_id, username, first_name, last_name)
            
            with self._get_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO user_feedback (user_id, username, rating, comments)
                    VALUES (%s, %s, %s, %s)
                ''', (user_id, username, rating, comments))
                logger.info("Feedback successfully recorded")
        except Exception as e:
            logger.error(f"Error recording feedback: {e}", exc_info=True)

analytics = UserAnalytics()