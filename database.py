import sqlite3
import json
import os
from datetime import datetime

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(DATA_DIR, 'tests.db')

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            created_at TEXT
        )
    ''')
    # Tests table — scoped by user_id
    c.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            data_json TEXT,
            created_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    # Attempts table — scoped by user_id
    c.execute('''
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            test_id TEXT,
            test_name TEXT,
            score REAL,
            max_score REAL,
            result_json TEXT,
            submitted_at TEXT,
            FOREIGN KEY (test_id) REFERENCES tests (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    # Migrate existing tables to add user_id column if missing
    try:
        c.execute('ALTER TABLE tests ADD COLUMN user_id INTEGER')
    except: pass
    try:
        c.execute('ALTER TABLE attempts ADD COLUMN user_id INTEGER')
    except: pass

    conn.commit()
    conn.close()

# ======================================================================
# USER FUNCTIONS
# ======================================================================
def create_user(email, password_hash, name):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO users (email, password_hash, name, created_at)
            VALUES (?, ?, ?, ?)
        ''', (email.lower().strip(), password_hash, name, datetime.now().isoformat()))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        return None  # Email already exists

def get_user_by_email(email):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE email = ?', (email.lower().strip(),))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_user_by_id(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

# ======================================================================
# TEST FUNCTIONS (user-scoped)
# ======================================================================
def save_test(test_id, name, data, user_id=None):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO tests (id, user_id, name, data_json, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (test_id, user_id, name, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_test(test_id, user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT data_json FROM tests WHERE id = ? AND (user_id = ? OR user_id IS NULL)', (test_id, user_id))
    else:
        c.execute('SELECT data_json FROM tests WHERE id = ?', (test_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row['data_json'])
    return None

def get_all_tests(user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT id, name, created_at, data_json FROM tests WHERE user_id = ? OR user_id IS NULL ORDER BY created_at DESC', (user_id,))
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
        c.execute('DELETE FROM tests WHERE id = ? AND user_id = ?', (test_id, user_id))
    else:
        c.execute('DELETE FROM tests WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()

# ======================================================================
# ATTEMPT FUNCTIONS (user-scoped)
# ======================================================================
def save_attempt(attempt_id, test_id, test_name, score, max_score, data, user_id=None):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO attempts (id, user_id, test_id, test_name, score, max_score, result_json, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (attempt_id, user_id, test_id, test_name, score, max_score, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_attempt(attempt_id, user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT result_json FROM attempts WHERE id = ? AND (user_id = ? OR user_id IS NULL)', (attempt_id, user_id))
    else:
        c.execute('SELECT result_json FROM attempts WHERE id = ?', (attempt_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row['result_json'])
    return None

def get_all_attempts(user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT id, test_id, test_name, score, max_score, submitted_at FROM attempts WHERE user_id = ? OR user_id IS NULL ORDER BY submitted_at DESC', (user_id,))
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
        c.execute('DELETE FROM attempts WHERE id = ? AND user_id = ?', (attempt_id, user_id))
    else:
        c.execute('DELETE FROM attempts WHERE id = ?', (attempt_id,))
    conn.commit()
    conn.close()

def get_all_mistakes(user_id=None):
    conn = get_db()
    c = conn.cursor()
    if user_id is not None:
        c.execute('SELECT result_json FROM attempts WHERE user_id = ? OR user_id IS NULL', (user_id,))
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
