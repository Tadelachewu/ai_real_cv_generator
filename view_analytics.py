import os
import psycopg2
from tabulate import tabulate

def get_user_activity():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cursor = conn.cursor()
    
    print("=== Most Active Users ===")
    cursor.execute('''
        SELECT u.user_id, u.username, COUNT(a.id) as action_count
        FROM users u
        JOIN user_actions a ON u.user_id = a.user_id
        GROUP BY u.user_id, u.username
        ORDER BY action_count DESC
        LIMIT 10
    ''')
    print(tabulate(cursor.fetchall(), headers=['User ID', 'Username', 'Actions']))
    
    print("\n=== Recent Feedback ===")
    cursor.execute('''
        SELECT username, rating, comments, timestamp 
        FROM user_feedback 
        ORDER BY timestamp DESC 
        LIMIT 5
    ''')
    print(tabulate(cursor.fetchall(), headers=['Username', 'Rating', 'Comments', 'Timestamp']))
    
    conn.close()

if __name__ == '__main__':
    get_user_activity()