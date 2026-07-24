import sqlite3
import re


def _norm_city(value) -> str:
    """Normalize city for route matching: trim, drop flags/emoji, casefold."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    # Drop leading emoji / flag / bullets (e.g. "🇷🇺 Москва")
    s = re.sub(r"^[^\wА-Яа-яЁё]+", "", s, flags=re.UNICODE).strip()
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def init_db():
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()

    # Drivers table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER UNIQUE NOT NULL,
            contact_info TEXT
        )
    ''')

    # Logisticians table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logisticians (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER UNIQUE NOT NULL,
            contact_info TEXT
        )
    ''')

    # Cargo table (for logisticians to post)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cargo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logistician_id INTEGER NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            cargo_type TEXT,
            weight REAL,
            volume REAL,
            price TEXT,
            date TEXT,
            contact TEXT,
            FOREIGN KEY (logistician_id) REFERENCES logisticians(id)
        )
    ''')

    # Vehicles table (for drivers to post)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            body_type TEXT,
            capacity REAL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            date TEXT,
            contact TEXT,
            FOREIGN KEY (driver_id) REFERENCES drivers(id)
        )
    ''')

    # Subscriptions table (drivers subscribing to routes)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            FOREIGN KEY (driver_id) REFERENCES drivers(id)
        )
    ''')

    conn.commit()
    conn.close()

def add_user(telegram_id, role, contact_info=None):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    if role == 'driver':
        cursor.execute('INSERT OR IGNORE INTO drivers (telegram_id, contact_info) VALUES (?, ?)', (telegram_id, contact_info))
    elif role == 'logistician':
        cursor.execute('INSERT OR IGNORE INTO logisticians (telegram_id, contact_info) VALUES (?, ?)', (telegram_id, contact_info))
    conn.commit()
    conn.close()

def get_user_role(telegram_id):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM drivers WHERE telegram_id = ?', (telegram_id,))
    driver = cursor.fetchone()
    if driver:
        conn.close()
        return 'driver'
    cursor.execute('SELECT id FROM logisticians WHERE telegram_id = ?', (telegram_id,))
    logistician = cursor.fetchone()
    if logistician:
        conn.close()
        return 'logistician'
    conn.close()
    return None

def get_driver_id(telegram_id):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM drivers WHERE telegram_id = ?', (telegram_id,))
    driver_id = cursor.fetchone()
    conn.close()
    return driver_id[0] if driver_id else None

def get_logistician_id(telegram_id):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM logisticians WHERE telegram_id = ?', (telegram_id,))
    logistician_id = cursor.fetchone()
    conn.close()
    return logistician_id[0] if logistician_id else None

def add_cargo(logistician_id, origin, destination, cargo_type, weight, volume, price, date, contact):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO cargo (logistician_id, origin, destination, cargo_type, weight, volume, price, date, contact) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (logistician_id, origin, destination, cargo_type, weight, volume, price, date, contact)
    )
    conn.commit()
    conn.close()

def get_cargo_by_route(origin, destination):
    """Match cargo by route; city compare is case-insensitive and trims flags/spaces."""
    want_o, want_d = _norm_city(origin), _norm_city(destination)
    if not want_o or not want_d:
        return []
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cargo')
    rows = cursor.fetchall()
    conn.close()
    return [
        row for row in rows
        if _norm_city(row[2]) == want_o and _norm_city(row[3]) == want_d
    ]

def get_all_cargo():
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cargo')
    cargo = cursor.fetchall()
    conn.close()
    return cargo

def get_logistician_cargo(logistician_id):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cargo WHERE logistician_id = ?', (logistician_id,))
    cargo = cursor.fetchall()
    conn.close()
    return cargo

def add_vehicle(driver_id, body_type, capacity, origin, destination, date, contact):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO vehicles (driver_id, body_type, capacity, origin, destination, date, contact) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (driver_id, body_type, capacity, origin, destination, date, contact)
    )
    conn.commit()
    conn.close()

def get_vehicles_by_route(origin, destination):
    """Match vehicles by route; city compare is case-insensitive and trims flags/spaces."""
    want_o, want_d = _norm_city(origin), _norm_city(destination)
    if not want_o or not want_d:
        return []
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM vehicles')
    rows = cursor.fetchall()
    conn.close()
    return [
        row for row in rows
        if _norm_city(row[4]) == want_o and _norm_city(row[5]) == want_d
    ]


def count_vehicles():
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM vehicles')
    n = cursor.fetchone()[0]
    conn.close()
    return n


def get_all_vehicles():
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM vehicles')
    vehicles = cursor.fetchall()
    conn.close()
    return vehicles

def get_driver_vehicles(driver_id):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM vehicles WHERE driver_id = ?', (driver_id,))
    vehicles = cursor.fetchall()
    conn.close()
    return vehicles

def add_subscription(driver_id, origin, destination):
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO subscriptions (driver_id, origin, destination) VALUES (?, ? ,?)', (driver_id, origin, destination))
    conn.commit()
    conn.close()

def get_subscribers_for_route(origin, destination):
    """Subscribers for a route; same city normalization as search."""
    want_o, want_d = _norm_city(origin), _norm_city(destination)
    if not want_o or not want_d:
        return []
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT d.telegram_id, s.origin, s.destination FROM subscriptions s
        JOIN drivers d ON s.driver_id = d.id
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [
        row[0] for row in rows
        if _norm_city(row[1]) == want_o and _norm_city(row[2]) == want_d
    ]
    
