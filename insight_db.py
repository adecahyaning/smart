import sqlite3
from datetime import datetime

DB_PATH = "upload_stats.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            upload_time TIMESTAMP,
            ip TEXT
        )
    ''')
    conn.commit()
    conn.close()

def log_upload(filename, ip_address):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO uploads (filename, upload_time, ip) VALUES (?, ?, ?)",
                   (filename, datetime.now(), ip_address))
    conn.commit()
    conn.close()

def get_insight():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), MAX(upload_time) FROM uploads")
    total, latest = cursor.fetchone()

    cursor.execute("SELECT filename, upload_time, ip FROM uploads ORDER BY upload_time DESC LIMIT 10")
    recent = cursor.fetchall()

    conn.close()
    return total, latest, recent
