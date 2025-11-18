import os
import shutil
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "database.db")


class Database:
    def __init__(self):
        self.DB_PATH = DB_PATH  # ← нужно для main.py
        self.conn = sqlite3.connect(self.DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    # ----------------------------------------------------------------------

    def init_db(self):
        cur = self.conn.cursor()

        # --- Создаём таблицы ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user INTEGER,
                to_user INTEGER,
                text TEXT,
                media TEXT,
                created_at TEXT,
                delivered INTEGER DEFAULT 0,
                reply_to INTEGER
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS support (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                created_at TEXT
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY
            );
        """)

        # --- Автомиграция ---
        def ensure_column(table, column, type_):
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r["name"] for r in cur.fetchall()]
            if column not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_}")
                print(f"[DB] Added column {column} to {table}")

        ensure_column("users", "joined", "TEXT")
        ensure_column("users", "last_sent", "TEXT")

        ensure_column("messages", "created_at", "TEXT")
        ensure_column("messages", "media", "TEXT")
        ensure_column("messages", "delivered", "INTEGER")
        ensure_column("messages", "reply_to", "INTEGER")

        ensure_column("support", "created_at", "TEXT")

        self.conn.commit()
        print("[DB] Migration complete — DB is up to date!")

    # ----------------------------------------------------------------------
    #  USER FUNCTIONS
    # ----------------------------------------------------------------------

    def ensure_user(self, user_id, username, first_name):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, joined) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, datetime.now().isoformat())
        )
        self.conn.commit()

    # ----------------------------------------------------------------------
    # BANS
    # ----------------------------------------------------------------------

    def is_user_banned(self, user_id):
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM bans WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None

    def ban_user(self, user_id):
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO bans (user_id) VALUES (?)", (user_id,))
        self.conn.commit()

    def unban_user(self, user_id):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
        self.conn.commit()

    # ----------------------------------------------------------------------
    # RATE LIMIT (last_sent)
    # ----------------------------------------------------------------------

    def get_last_sent(self, user_id):
        cur = self.conn.cursor()
        cur.execute("SELECT last_sent FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return row["last_sent"] if row else None

    def update_last_sent(self, user_id):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE users SET last_sent = ? WHERE user_id = ?",
            (datetime.now().isoformat(), user_id)
        )
        self.conn.commit()

    # ----------------------------------------------------------------------
    # MESSAGES
    # ----------------------------------------------------------------------

    def save_message(self, from_user, to_user, text=None, media=None, delivered=0, reply_to=None):
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO messages (from_user, to_user, text, media, created_at, delivered, reply_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (from_user, to_user, text, media, datetime.now().isoformat(), delivered, reply_to))
        self.conn.commit()
        return cur.lastrowid

    def get_messages_for(self, user_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM messages WHERE to_user = ? ORDER BY id DESC", (user_id,))
        return cur.fetchall()
