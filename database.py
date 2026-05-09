import os
import json
import sqlite3
import uuid
from datetime import datetime
from urllib.parse import urlparse

# DATABASE SELECTION
DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith('postgres')

def get_db():
    if IS_POSTGRES:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        # Fix for Render/Supabase needing sslmode
        conn = psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=RealDictCursor)
        return conn
    else:
        DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(__file__))
        DB_PATH = os.path.join(DATA_DIR, 'tests.db')
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

def execute_query(query, params=(), commit=False):
    conn = get_db()
    # Handle dialect differences for parameter markers (? vs %s)
    if IS_POSTGRES:
        query = query.replace('?', '%s')
        # Handle SQLite specific INSERT OR REPLACE
        if 'INSERT OR REPLACE' in query:
            # This is a very basic translation, specific to our schema
            if 'INTO tests' in query:
                query = query.replace('INSERT OR REPLACE INTO tests', 'INSERT INTO tests')
                query += ' ON CONFLICT (id) DO UPDATE SET user_id=EXCLUDED.user_id, name=EXCLUDED.name, data_json=EXCLUDED.data_json, created_at=EXCLUDED.created_at'
            elif 'INTO attempts' in query:
                query = query.replace('INSERT OR REPLACE INTO attempts', 'INSERT INTO attempts')
                query += ' ON CONFLICT (id) DO UPDATE SET user_id=EXCLUDED.user_id, test_id=EXCLUDED.test_id, test_name=EXCLUDED.test_name, score=EXCLUDED.score, max_score=EXCLUDED.max_score, result_json=EXCLUDED.result_json, submitted_at=EXCLUDED.submitted_at'
    
    cur = conn.cursor()
    try:
        cur.execute(query, params)
        if commit:
            conn.commit()
            if not IS_POSTGRES:
                res = cur.lastrowid
            else:
                res = True # Postgres usually needs RETURNING for ID
        else:
            res = cur.fetchall()
            # Convert to list of dicts for SQLite/Postgres consistency
            res = [dict(r) for r in res]
    finally:
        conn.close()
    return res

def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    if not IS_POSTGRES:
        cur.execute('PRAGMA journal_mode=WAL;')
        id_type = "INTEGER PRIMARY KEY AUTOINCREMENT"
    else:
        id_type = "SERIAL PRIMARY KEY"

    # Users table
    cur.execute(f'''
        CREATE TABLE IF NOT EXISTS users (
            id {id_type},
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            created_at TEXT
        )
    ''')
    # Tests table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            data_json TEXT,
            created_at TEXT
        )
    ''')
    # Attempts table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            test_id TEXT,
            test_name TEXT,
            score REAL,
            max_score REAL,
            result_json TEXT,
            submitted_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

def create_user(email, password_hash, name):
    query = 'INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?) RETURNING id' if IS_POSTGRES else 'INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)'
    params = (email.lower().strip(), password_hash, name, datetime.now().isoformat())
    
    conn = get_db()
    cur = conn.cursor()
    try:
        if IS_POSTGRES:
            cur.execute(query.replace('?', '%s'), params)
            user_id = cur.fetchone()['id']
        else:
            cur.execute(query, params)
            user_id = cur.lastrowid
        conn.commit()
        return user_id
    except Exception:
        return None
    finally:
        conn.close()

def get_user_by_email(email):
    res = execute_query('SELECT * FROM users WHERE email = ?', (email.lower().strip(),))
    return res[0] if res else None

def get_user_by_id(user_id):
    res = execute_query('SELECT * FROM users WHERE id = ?', (user_id,))
    return res[0] if res else None

def save_test(test_id, name, data, user_id=None):
    query = 'INSERT OR REPLACE INTO tests (id, user_id, name, data_json, created_at) VALUES (?, ?, ?, ?, ?)'
    params = (test_id, user_id, name, json.dumps(data), datetime.now().isoformat())
    execute_query(query, params, commit=True)

def get_test(test_id, user_id=None):
    if user_id is not None:
        query = 'SELECT data_json FROM tests WHERE id = ? AND (user_id = ? OR user_id IS NULL)'
        params = (test_id, user_id)
    else:
        query = 'SELECT data_json FROM tests WHERE id = ?'
        params = (test_id,)
    res = execute_query(query, params)
    return json.loads(res[0]['data_json']) if res else None

def get_all_tests(user_id=None):
    if user_id is not None:
        query = 'SELECT id, name, created_at, data_json FROM tests WHERE user_id = ? OR user_id IS NULL ORDER BY created_at DESC'
        params = (user_id,)
    else:
        query = 'SELECT id, name, created_at, data_json FROM tests ORDER BY created_at DESC'
        params = ()
    
    rows = execute_query(query, params)
    results = []
    for r in rows:
        try:
            data = json.loads(r['data_json'])
        except Exception: data = {}
        results.append({
            'id': r['id'], 'name': r['name'], 'created_at': r['created_at'],
            'status': data.get('status', 'ready'),
            'total_questions': data.get('total_questions') or len(data.get('questions', [])),
            'duration_minutes': data.get('duration_minutes', 180)
        })
    return results

def delete_test(test_id, user_id=None):
    if user_id is not None:
        execute_query('DELETE FROM tests WHERE id = ? AND user_id = ?', (test_id, user_id), commit=True)
    else:
        execute_query('DELETE FROM tests WHERE id = ?', (test_id,), commit=True)

def save_attempt(attempt_id, test_id, test_name, score, max_score, data, user_id=None):
    query = 'INSERT OR REPLACE INTO attempts (id, user_id, test_id, test_name, score, max_score, result_json, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
    params = (attempt_id, user_id, test_id, test_name, score, max_score, json.dumps(data), datetime.now().isoformat())
    execute_query(query, params, commit=True)

def get_attempt(attempt_id, user_id=None):
    if user_id is not None:
        query = 'SELECT result_json FROM attempts WHERE id = ? AND (user_id = ? OR user_id IS NULL)'
        params = (attempt_id, user_id)
    else:
        query = 'SELECT result_json FROM attempts WHERE id = ?'
        params = (attempt_id,)
    res = execute_query(query, params)
    return json.loads(res[0]['result_json']) if res else None

def get_all_attempts(user_id=None):
    if user_id is not None:
        query = 'SELECT id, test_id, test_name, score, max_score, submitted_at FROM attempts WHERE user_id = ? OR user_id IS NULL ORDER BY submitted_at DESC'
        params = (user_id,)
    else:
        query = 'SELECT id, test_id, test_name, score, max_score, submitted_at FROM attempts ORDER BY submitted_at DESC'
        params = ()
    return execute_query(query, params)

def delete_attempt(attempt_id, user_id=None):
    if user_id is not None:
        execute_query('DELETE FROM attempts WHERE id = ? AND user_id = ?', (attempt_id, user_id), commit=True)
    else:
        execute_query('DELETE FROM attempts WHERE id = ?', (attempt_id,), commit=True)

def get_all_mistakes(user_id=None):
    if user_id is not None:
        rows = execute_query('SELECT result_json FROM attempts WHERE user_id = ? OR user_id IS NULL', (user_id,))
    else:
        rows = execute_query('SELECT result_json FROM attempts')
    
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
