import os
import psycopg2
from typing import List, Dict
from datetime import datetime, timedelta

class AnalyticsQueries:
    def __init__(self):
        self.db_url = os.getenv('DATABASE_URL')

    def get_connection(self):
        return psycopg2.connect(self.db_url)

    def get_daily_active_users(self, days: int = 7) -> List[Dict]:
        """Get daily active users for the last N days"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    SELECT 
                        DATE(timestamp) as day,
                        COUNT(DISTINCT user_id) as active_users
                    FROM user_actions
                    WHERE timestamp >= %s
                    GROUP BY day
                    ORDER BY day DESC
                    LIMIT %s
                ''', (datetime.now() - timedelta(days=days), days))
                return [
                    {'date': row[0], 'users': row[1]}
                    for row in cursor.fetchall()
                ]

    def get_conversion_funnel(self) -> Dict:
        """Get conversion funnel metrics"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Example funnel: started -> completed profile -> generated CV
                cursor.execute('''
                    SELECT 
                        COUNT(DISTINCT CASE WHEN action_type = 'session_started' THEN user_id END) as started,
                        COUNT(DISTINCT CASE WHEN action_type = 'profile_completed' THEN user_id END) as completed_profile,
                        COUNT(DISTINCT CASE WHEN action_type = 'cv_generated' THEN user_id END) as generated_cv
                    FROM user_actions
                ''')
                row = cursor.fetchone()
                return {
                    'started': row[0],
                    'completed_profile': row[1],
                    'generated_cv': row[2]
                }

    def get_feedback_stats(self) -> Dict:
        """Get feedback statistics"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total,
                        AVG(rating) as avg_rating,
                        COUNT(comments) as with_comments
                    FROM user_feedback
                ''')
                row = cursor.fetchone()
                return {
                    'total_feedback': row[0],
                    'average_rating': float(row[1]) if row[1] else None,
                    'with_comments': row[2]
                }