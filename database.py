import sqlite3
from datetime import datetime

DB_NAME = "events.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Таблица пользователей (подписчиков)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица событий (текущее расписание)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            time_start TEXT,
            time_end TEXT,
            title TEXT NOT NULL,
            location TEXT,
            description TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

# Добавление/удаление подписчиков
def add_user(user_id, username):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO users (user_id, username, is_active)
        VALUES (?, ?, 1)
    """, (user_id, username))
    conn.commit()
    conn.close()

def remove_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_active_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE is_active = 1")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

# Очистка старых событий и загрузка новых
def clear_events():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM events")
    conn.commit()
    conn.close()

def insert_events(events_list):
    """
    events_list - список кортежей:
    (start_date, end_date, time_start, time_end, title, location, description)
    Даты в формате строки 'YYYY-MM-DD', время 'HH:MM' или None
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO events (start_date, end_date, time_start, time_end, title, location, description)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, events_list)
    conn.commit()
    conn.close()

# Получение событий на дату (сегодня)
def get_events_for_date(date_obj):
    """date_obj - объект date"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT start_date, end_date, time_start, time_end, title, location, description
        FROM events
        WHERE date(?) BETWEEN start_date AND end_date
        ORDER BY start_date, time_start
    """, (date_obj.isoformat(),))
    rows = cur.fetchall()
    conn.close()
    return rows

# Получение событий на неделю (с сегодня по конец недели)
def get_events_for_week(start_date, end_date):
    """start_date, end_date - объекты date"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT start_date, end_date, time_start, time_end, title, location, description
        FROM events
        WHERE start_date <= ? AND end_date >= ?
        ORDER BY start_date, time_start
    """, (end_date.isoformat(), start_date.isoformat()))
    # Условие: интервал события пересекается с [start_date, end_date]
    rows = cur.fetchall()
    conn.close()
    return rows