"""SQLite 持久化层（默认存储；生产可平滑切换 MySQL/PostgreSQL——保持同样的表结构）。"""
import os
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_local = threading.local()
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
  code TEXT PRIMARY KEY, name TEXT, sector TEXT, sector_idx INT,
  tags TEXT, float_cap REAL, start_price REAL, pe REAL, market TEXT
);
CREATE TABLE IF NOT EXISTS bars (
  date TEXT, code TEXT, open REAL, high REAL, low REAL, close REAL,
  pre_close REAL, pct REAL, turnover REAL, amount REAL, volume INT,
  streak INT, limit_up INT, limit_down INT, one_word INT,
  PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_bars_code ON bars(code, date);
CREATE INDEX IF NOT EXISTS idx_bars_date ON bars(date);
CREATE TABLE IF NOT EXISTS news (
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, code TEXT, sector TEXT,
  title TEXT, sentiment REAL, source TEXT, kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_code ON news(code, date);
CREATE INDEX IF NOT EXISTS idx_news_sector ON news(sector, date);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT
);
"""


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def get_conn():
    con = getattr(_local, "con", None)
    if con is None:
        con = _connect()
        _local.con = con
    return con


@contextmanager
def tx():
    con = get_conn()
    try:
        con.execute("BEGIN")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = _connect()
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def query(sql, params=()):
    con = get_conn()
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    con = get_conn()
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None


def execute(sql, params=()):
    con = get_conn()
    with _lock:
        cur = con.execute(sql, params)
        con.commit()
        return cur.lastrowid


def meta_get(key, default=None):
    r = query_one("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else default


def meta_set(key, value):
    execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))


def close_all():
    con = getattr(_local, "con", None)
    if con is not None:
        con.close()
        _local.con = None
