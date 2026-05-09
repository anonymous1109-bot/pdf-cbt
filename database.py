import os
import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

# Fetch the Supabase URL from Render Environment Variables
DB_URL = os.environ.get('DATABASE_URL')

def get_db():
    if not DB_URL:
        raise ValueError("DATABASE_URL environment variable is not set!")
    # RealDictCursor makes rows behave like dictionaries, matching your old SQLite setup
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # 1. Users table (AUTOINCREMENT becomes SERIAL in Postgres)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            created_at TEXT
        )
    ''')
    
    # 2. Tests table 
    c.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            id TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            name TEXT,
            data_json TEXT,
            created_at TEXT
        )
    ''')
    
    # 3. Attempts table
    c.execute('''
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            test_id TEXT REFERENCES tests(id) ON DELETE CASCADE,
            test_name TEXT,
            score REAL,
            max_score REAL,
            result_json TEXT,
            submitted_at TEXT
        )
    ''')

    conn.commit()
    conn.close()

# ======================================================================
# USER FUNCTIONS
# ======================================================================
def create_user(email, password_hash, name):
    conn = get_db()
    c = conn.cursor()
    try:
        # Postgres uses %s instead of ?
        c.execute('''
            INSERT INTO users (email, password_hash, name, created_at)
            VALUES (%s, %s, %s, %s) RETURNING id
        ''', (email.lower().strip(), password_hash, name, datetime.now().isoformat()))
        user_id = c.fetchone()['id']
        conn.commit()
        return user_id
    except psycopg2.IntegrityError:
        conn.rollback()
        return None
    finally:
        conn.close()

def get_user_by_email(email):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE email = %s', (email.lower().strip(),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_id(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id = %s', (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

# ======================================================================
# TEST FUNCTIONS (user-scoped)
# ======================================================================
def save_test(test_id, name, data, user_id=None):
    conn = get_db()
    c = conn.cursor()
    # Postgres uses ON CONFLICT DO UPDATE instead of INSERT OR REPLACE
    c.execute('''
        INSERT INTO tests (id, user_id, name, data_json, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET 
            name = EXCLUDED.name, 
            data_json = EXCLUDED.data_json
    ''', (test_id, user_id, name, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_test(test_id, user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT data_json FROM tests WHERE id = %s AND (user_id = %s OR user_id IS NULL)', (test_id, user_id))
    else:
        c.execute('SELECT data_json FROM tests WHERE id = %s', (test_id,))
    row = c.fetchone()
    conn.close()
    return json.loads(row['data_json']) if row else None

def get_all_tests(user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT id, name, created_at, data_json FROM tests WHERE user_id = %s OR user_id IS NULL ORDER BY created_at DESC', (user_id,))
    else:
        c.execute('SELECT id, name, created_at, data_json FROM tests ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    results = []
    for r in rows:
        try:
            data = json.loads(r['data_json'])
        except Exception:
            data = {}
        results.append({
            'id': r['id'],
            'name': r['name'],
            'created_at': r['created_at'],
            'status': data.get('status', 'ready'),
            'total_questions': data.get('total_questions') or len(data.get('questions', []))
        })
    return results

def delete_test(test_id, user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('DELETE FROM tests WHERE id = %s AND user_id = %s', (test_id, user_id))
    else:
        c.execute('DELETE FROM tests WHERE id = %s', (test_id,))
    conn.commit()
    conn.close()

# ======================================================================
# ATTEMPT FUNCTIONS (user-scoped)
# ======================================================================
def save_attempt(attempt_id, test_id, test_name, score, max_score, data, user_id=None):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO attempts (id, user_id, test_id, test_name, score, max_score, result_json, submitted_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET 
            score = EXCLUDED.score, 
            result_json = EXCLUDED.result_json
    ''', (attempt_id, user_id, test_id, test_name, score, max_score, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_attempt(attempt_id, user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT result_json FROM attempts WHERE id = %s AND (user_id = %s OR user_id IS NULL)', (attempt_id, user_id))
    else:
        c.execute('SELECT result_json FROM attempts WHERE id = %s', (attempt_id,))
    row = c.fetchone()
    conn.close()
    return json.loads(row['result_json']) if row else None

def get_all_attempts(user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT id, test_id, test_name, score, max_score, submitted_at FROM attempts WHERE user_id = %s OR user_id IS NULL ORDER BY submitted_at DESC', (user_id,))
    else:
        c.execute('SELECT id, test_id, test_name, score, max_score, submitted_at FROM attempts ORDER BY submitted_at DESC')
    rows = c.fetchall()
    conn.close()
    return [{'id': r['id'], 'test_id': r['test_id'], 'test_name': r['test_name'],
             'score': r['score'], 'max_score': r['max_score'], 'submitted_at': r['submitted_at']} for r in rows]

def delete_attempt(attempt_id, user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('DELETE FROM attempts WHERE id = %s AND user_id = %s', (attempt_id, user_id))
    else:
        c.execute('DELETE FROM attempts WHERE id = %s', (attempt_id,))
    conn.commit()
    conn.close()

def get_all_mistakes(user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT result_json FROM attempts WHERE user_id = %s OR user_id IS NULL', (user_id,))
    else:
        c.execute('SELECT result_json FROM attempts')
    rows = c.fetchall()
    conn.close()

    mistakes = []
    for row in rows:
        data = json.loads(row['result_json'])
        for q in data.get('questions', []):
            if q.get('status') in ['incorrect', 'unattempted']:
                q['test_name'] = data.get('test_name', 'Unknown Test')
                q['test_id'] = data.get('test_id', '')
                q['attempt_id'] = data.get('result_id', '')
                mistakes.append(q)
    return mistakes