import sqlite3
import json
import os
from datetime import datetime

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(DATA_DIR, 'tests.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # Tests table
    c.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            id TEXT PRIMARY KEY,
            name TEXT,
            data_json TEXT,
            created_at TEXT
        )
    ''')
    # Attempts table
    c.execute('''
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY,
            test_id TEXT,
            test_name TEXT,
            score REAL,
            max_score REAL,
            result_json TEXT,
            submitted_at TEXT,
            FOREIGN KEY (test_id) REFERENCES tests (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

def save_test(test_id, name, data):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO tests (id, name, data_json, created_at)
        VALUES (?, ?, ?, ?)
    ''', (test_id, name, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_test(test_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT data_json FROM tests WHERE id = ?', (test_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row['data_json'])
    return None

def get_all_tests():
    conn = get_db()
    c = conn.cursor()
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

def delete_test(test_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM tests WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()

def save_attempt(attempt_id, test_id, test_name, score, max_score, data):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO attempts (id, test_id, test_name, score, max_score, result_json, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (attempt_id, test_id, test_name, score, max_score, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_attempt(attempt_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT result_json FROM attempts WHERE id = ?', (attempt_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row['result_json'])
    return None

def get_all_attempts():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, test_id, test_name, score, max_score, submitted_at FROM attempts ORDER BY submitted_at DESC')
    rows = c.fetchall()
    conn.close()
    return [{'id': r['id'], 'test_id': r['test_id'], 'test_name': r['test_name'], 
             'score': r['score'], 'max_score': r['max_score'], 'submitted_at': r['submitted_at']} for r in rows]

def delete_attempt(attempt_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM attempts WHERE id = ?', (attempt_id,))
    conn.commit()
    conn.close()

def get_all_mistakes():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT result_json FROM attempts')
    rows = c.fetchall()
    conn.close()
    
    mistakes = []
    for row in rows:
        data = json.loads(row['result_json'])
        for q in data.get('questions', []):
            if q.get('status') in ['incorrect', 'unattempted']:
                # Add metadata to the mistake
                q['test_name'] = data.get('test_name', 'Unknown Test')
                q['test_id'] = data.get('test_id', '')
                q['attempt_id'] = data.get('result_id', '')
                mistakes.append(q)
    return mistakes
