import json
import sqlite3
import re
from datetime import datetime, timezone


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

    # Pending cargo ads awaiting manual payment confirmation by admin
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            logistician_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            ad_count INTEGER NOT NULL DEFAULT 1,
            amount_label TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (logistician_id) REFERENCES logisticians(id)
        )
    ''')

    # Personal channel invite links (one per user) for tracking referrals
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_invite_links (
            telegram_id INTEGER PRIMARY KEY,
            invite_link TEXT NOT NULL,
            link_name TEXT,
            created_at TEXT NOT NULL
        )
    ''')

    # Channel joins credited to an inviter (each joiner counted once)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channel_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_telegram_id INTEGER NOT NULL,
            invited_telegram_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_channel_invites_inviter '
        'ON channel_invites(inviter_telegram_id)'
    )

    conn.commit()
    conn.close()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_pending_ad(telegram_id, logistician_id, cargo_items, amount_label):
    """Store cargo payload until user pays and admin confirms.

    cargo_items: list of dicts (JSON-serializable). status starts as awaiting_payment.
    """
    if not cargo_items:
        raise ValueError("cargo_items must not be empty")
    now = _utcnow_iso()
    payload = json.dumps(cargo_items, ensure_ascii=False)
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO pending_ads
            (telegram_id, logistician_id, payload, ad_count, amount_label, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            telegram_id,
            logistician_id,
            payload,
            len(cargo_items),
            amount_label,
            'awaiting_payment',
            now,
            now,
        ),
    )
    pending_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return pending_id


def get_pending_ad(pending_id):
    """Return dict or None. payload is already parsed to list of dicts."""
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT id, telegram_id, logistician_id, payload, ad_count, amount_label, status, created_at, updated_at
        FROM pending_ads WHERE id = ?
        ''',
        (pending_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    try:
        items = json.loads(row[3])
    except (TypeError, json.JSONDecodeError):
        items = []
    return {
        'id': row[0],
        'telegram_id': row[1],
        'logistician_id': row[2],
        'items': items,
        'ad_count': row[4],
        'amount_label': row[5],
        'status': row[6],
        'created_at': row[7],
        'updated_at': row[8],
    }


def update_pending_ad_status(pending_id, new_status, expected_statuses=None):
    """Update status if current status is in expected_statuses (or any if None).

    Returns True if a row was updated.
    """
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    now = _utcnow_iso()
    if expected_statuses:
        placeholders = ','.join('?' for _ in expected_statuses)
        cursor.execute(
            f'''
            UPDATE pending_ads
            SET status = ?, updated_at = ?
            WHERE id = ? AND status IN ({placeholders})
            ''',
            (new_status, now, pending_id, *expected_statuses),
        )
    else:
        cursor.execute(
            '''
            UPDATE pending_ads
            SET status = ?, updated_at = ?
            WHERE id = ?
            ''',
            (new_status, now, pending_id),
        )
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def list_pending_ads_for_admin(limit=20):
    """Recent pending ads waiting for admin (awaiting_admin)."""
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT id, telegram_id, logistician_id, payload, ad_count, amount_label, status, created_at
        FROM pending_ads
        WHERE status = 'awaiting_admin'
        ORDER BY id DESC
        LIMIT ?
        ''',
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        try:
            items = json.loads(row[3])
        except (TypeError, json.JSONDecodeError):
            items = []
        result.append({
            'id': row[0],
            'telegram_id': row[1],
            'logistician_id': row[2],
            'items': items,
            'ad_count': row[4],
            'amount_label': row[5],
            'status': row[6],
            'created_at': row[7],
        })
    return result


# --- Channel invite tracking (for post-ad requirements) ---

def get_user_invite_link_row(telegram_id):
    """Return {invite_link, link_name, created_at} or None."""
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT invite_link, link_name, created_at
        FROM user_invite_links WHERE telegram_id = ?
        ''',
        (telegram_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        'invite_link': row[0],
        'link_name': row[1],
        'created_at': row[2],
    }


def save_user_invite_link(telegram_id, invite_link, link_name=None):
    """Upsert personal invite link for a user."""
    now = _utcnow_iso()
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO user_invite_links (telegram_id, invite_link, link_name, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            invite_link = excluded.invite_link,
            link_name = excluded.link_name,
            created_at = excluded.created_at
        ''',
        (telegram_id, invite_link, link_name, now),
    )
    conn.commit()
    conn.close()


def get_inviter_by_link_name(link_name):
    """Resolve inviter telegram_id by invite link name, or None."""
    if not link_name:
        return None
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT telegram_id FROM user_invite_links WHERE link_name = ?',
        (link_name,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def get_invite_count(telegram_id) -> int:
    """How many unique people this user invited to the channel."""
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT COUNT(*) FROM channel_invites WHERE inviter_telegram_id = ?',
        (telegram_id,),
    )
    n = cursor.fetchone()[0]
    conn.close()
    return int(n or 0)


def record_channel_invite(inviter_telegram_id, invited_telegram_id) -> bool:
    """Credit a channel join to inviter. Each invited user counts once globally.

    Returns True if a new invite was recorded, False if duplicate/self/invalid.
    """
    if not inviter_telegram_id or not invited_telegram_id:
        return False
    if inviter_telegram_id == invited_telegram_id:
        return False
    now = _utcnow_iso()
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            '''
            INSERT INTO channel_invites
                (inviter_telegram_id, invited_telegram_id, created_at)
            VALUES (?, ?, ?)
            ''',
            (inviter_telegram_id, invited_telegram_id, now),
        )
        conn.commit()
        changed = cursor.rowcount > 0
    except sqlite3.IntegrityError:
        # invited_telegram_id already credited to someone
        changed = False
    finally:
        conn.close()
    return changed


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

def _norm_text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _norm_contact(value) -> str:
    return _norm_text(value).lstrip("@")


def _norm_number(value):
    """Normalize weight/volume for comparison (24, 24.0, '24.0' → same)."""
    if value is None or value == "":
        return 0.0
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return _norm_text(value)


def _cargo_content_key(row) -> tuple:
    """Identity of an ad without contact: route + type + size + price + date.

    row: id, logistician_id, origin, destination, cargo_type, weight, volume, price, date, contact
    """
    return (
        _norm_city(row[2]),
        _norm_city(row[3]),
        _norm_text(row[4]),
        _norm_number(row[5]),
        _norm_number(row[6]),
        _norm_text(row[7]),
        _norm_text(row[8]),
    )


def _dedupe_cargo_rows(rows):
    """Collapse duplicate search hits into one card; merge different contacts.

    Same route/type/weight/price/date posted twice (or by two accounts) used to
    show as two almost identical blocks — that looked like repetition.
    """
    groups = {}
    order = []
    for row in rows:
        key = _cargo_content_key(row)
        contact = str(row[9]).strip() if row[9] is not None else ""
        if key not in groups:
            groups[key] = [list(row), []]
            order.append(key)
        base, contacts = groups[key]
        # Keep the newest row as the display base
        if row[0] is not None and (base[0] is None or row[0] > base[0]):
            groups[key][0] = list(row)
            base = groups[key][0]
        if contact:
            seen = {_norm_contact(c) for c in contacts}
            if _norm_contact(contact) not in seen:
                contacts.append(contact)
    result = []
    for key in order:
        base, contacts = groups[key]
        if contacts:
            base[9] = ", ".join(contacts)
        result.append(tuple(base))
    return result


def add_cargo(logistician_id, origin, destination, cargo_type, weight, volume, price, date, contact):
    """Insert cargo; skip exact duplicates from the same logistician (double-tap / re-post)."""
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    new_row = (
        None,
        logistician_id,
        origin,
        destination,
        cargo_type,
        weight,
        volume,
        price,
        date,
        contact,
    )
    new_key = _cargo_content_key(new_row)
    new_contact = _norm_contact(contact)
    cursor.execute('SELECT * FROM cargo WHERE logistician_id = ?', (logistician_id,))
    for row in cursor.fetchall():
        if _cargo_content_key(row) == new_key and _norm_contact(row[9]) == new_contact:
            conn.close()
            return row[0]
    cursor.execute(
        'INSERT INTO cargo (logistician_id, origin, destination, cargo_type, weight, volume, price, date, contact) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (logistician_id, origin, destination, cargo_type, weight, volume, price, date, contact)
    )
    cargo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return cargo_id


def get_cargo_by_route(origin, destination):
    """Match cargo by route; city compare is case-insensitive and trims flags/spaces.

    Near-duplicate ads (same content, different contacts) are merged into one row
    so «Найти груз» does not print the same card twice.
    """
    want_o, want_d = _norm_city(origin), _norm_city(destination)
    if not want_o or not want_d:
        return []
    conn = sqlite3.connect('cargo_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cargo ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    matched = [
        row for row in rows
        if _norm_city(row[2]) == want_o and _norm_city(row[3]) == want_d
    ]
    return _dedupe_cargo_rows(matched)

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
    
