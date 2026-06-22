import sqlite3
import threading

_lock = threading.Lock()
DB_NAME = "tranzit_pro.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                role TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logisticians (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cargo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logistician_id INTEGER,
                origin TEXT,
                destination TEXT,
                cargo_type TEXT,
                weight TEXT,
                volume TEXT,
                price TEXT,
                date TEXT,
                contact TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER,
                origin TEXT,
                destination TEXT,
                body_type TEXT,
                capacity TEXT,
                date TEXT,
                contact TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                origin TEXT,
                destination TEXT
            )
        """)
        conn.commit()
        conn.close()

def add_user(user_id, username, role=None):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users (user_id, username, role) VALUES (?, ?, ?)", (user_id, username, role))
        if role:
            cur.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            if role == "driver":
                cur.execute("SELECT id FROM drivers WHERE user_id = ?", (user_id,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO drivers (user_id) VALUES (?)", (user_id,))
            elif role == "logistician":
                cur.execute("SELECT id FROM logisticians WHERE user_id = ?", (user_id,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO logisticians (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()

def get_user_role(user_id):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return row["role"] if row else None

def get_driver_id(user_id):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM drivers WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return row["id"] if row else None

def get_logistician_id(user_id):
    w…
