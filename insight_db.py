import psycopg2
from datetime import datetime
import os

DB_CONFIG = {
    "host": os.getenv("PGHOST"),
    "port": os.getenv("PGPORT"),
    "dbname": os.getenv("PGDATABASE"),
    "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def init_db():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS uploads (
                    id SERIAL PRIMARY KEY,
                    filename TEXT,
                    upload_time TIMESTAMP,
                    ip TEXT
                )
            ''')
        conn.commit()

def log_upload(filename, ip_address):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO uploads (filename, upload_time, ip) VALUES (%s, %s, %s)",
                (filename, datetime.now(), ip_address)
            )
        conn.commit()

def get_insight():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), MAX(upload_time) FROM uploads")
            total, latest = cursor.fetchone()

            cursor.execute("SELECT filename, upload_time, ip FROM uploads ORDER BY upload_time DESC LIMIT 10")
            recent = cursor.fetchall()

    return total, latest, recent
