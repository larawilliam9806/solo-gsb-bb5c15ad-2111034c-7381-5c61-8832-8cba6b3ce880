"""SQLite 持久化层。

位姿约定：每片陶片保存其局部轮廓 -> 世界坐标的刚体变换
(x_mm, y_mm, theta_rad)。连通组内各片位姿即其世界位姿；
移动整组时给组内每片复合同一个刚体变换。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS fragments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    contour     TEXT NOT NULL,           -- JSON [[x,y],...] 毫米制闭合轮廓
    pos_x       REAL NOT NULL DEFAULT 0, -- 世界位姿：平移（毫米）
    pos_y       REAL NOT NULL DEFAULT 0,
    theta       REAL NOT NULL DEFAULT 0, -- 世界位姿：旋转（弧度）
    group_id    INTEGER,                 -- 录入后立即置为 id（自成一组）
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS markers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fragment_id INTEGER NOT NULL REFERENCES fragments(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    x           REAL NOT NULL,           -- 轮廓局部坐标（毫米）
    y           REAL NOT NULL
);

-- 候选拼接关系：proposed 可预览；adopted 即生效约束
CREATE TABLE IF NOT EXISTS relations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    frag_a_id   INTEGER NOT NULL REFERENCES fragments(id),
    frag_b_id   INTEGER NOT NULL REFERENCES fragments(id),
    pairs       TEXT NOT NULL,           -- JSON [[marker_a_id, marker_b_id], ...]
    status      TEXT NOT NULL DEFAULT 'proposed',
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 每次采纳/撤销自增；前端/调用方据此判断预览版本是否过期
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_lock = threading.RLock()


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    with _lock, conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('state_version','1')"
        )


def bump_state_version(conn: sqlite3.Connection) -> int:
    with _lock:
        conn.execute("UPDATE meta SET value = value + 1 WHERE key='state_version'")
        row = conn.execute(
            "SELECT value FROM meta WHERE key='state_version'"
        ).fetchone()
        return int(row["value"])


def get_state_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key='state_version'"
    ).fetchone()
    return int(row["value"]) if row else 1


# ---------------------------------------------------------------------------
# 序列化助手
# ---------------------------------------------------------------------------

def contour_of(frag: sqlite3.Row) -> list[list[float]]:
    return json.loads(frag["contour"])


def pairs_of(rel: sqlite3.Row) -> list[list[int]]:
    return json.loads(rel["pairs"])
