from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    remind_days INTEGER NOT NULL DEFAULT 7,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    room_no TEXT NOT NULL,
    area REAL NOT NULL DEFAULT 0,
    lease_status TEXT NOT NULL DEFAULT '空置',
    UNIQUE(project_id, room_no),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    deposit REAL NOT NULL DEFAULT 0,
    monthly_rent REAL NOT NULL DEFAULT 0,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    free_start TEXT,
    free_end TEXT,
    status TEXT NOT NULL DEFAULT '生效',
    payment_period TEXT NOT NULL DEFAULT '季度',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lease_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    amount REAL NOT NULL,
    paid_at TEXT NOT NULL DEFAULT (date('now', 'localtime')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY(lease_id) REFERENCES leases(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS lease_free_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lease_id INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    FOREIGN KEY(lease_id) REFERENCES leases(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rooms_project ON rooms(project_id);
CREATE INDEX IF NOT EXISTS idx_leases_room ON leases(room_id);
CREATE INDEX IF NOT EXISTS idx_leases_status ON leases(status);
CREATE INDEX IF NOT EXISTS idx_payments_lease ON payments(lease_id);
CREATE INDEX IF NOT EXISTS idx_free_periods_lease ON lease_free_periods(lease_id);
"""

DEFAULT_SETTINGS = {
    "lease_expire_remind_days": "7",
    "rent_due_remind_days": "7",
}


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._seed_settings(conn)
            self._migrate_free_periods(conn)
            self._migrate_drop_project_address(conn)
            self._migrate_lease_payment_period(conn)

    def _migrate_lease_payment_period(self, conn: sqlite3.Connection) -> None:
        """为已有租赁补充缴费周期（默认季度）。"""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(leases)").fetchall()}
        if "payment_period" in cols:
            return
        conn.execute(
            "ALTER TABLE leases ADD COLUMN payment_period TEXT NOT NULL DEFAULT '季度'"
        )

    def _migrate_drop_project_address(self, conn: sqlite3.Connection) -> None:
        """移除已废弃的 projects.address 列。"""
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        if "address" not in cols:
            return
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            CREATE TABLE projects_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                remind_days INTEGER NOT NULL DEFAULT 7,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            INSERT INTO projects_new (id, name, remind_days, created_at)
            SELECT id, name, remind_days, created_at FROM projects;
            DROP TABLE projects;
            ALTER TABLE projects_new RENAME TO projects;
            """
        )
        conn.execute("PRAGMA foreign_keys = ON")

    def _migrate_free_periods(self, conn: sqlite3.Connection) -> None:
        """将旧版单段免租期迁移到 lease_free_periods。"""
        rows = conn.execute(
            """
            SELECT id, free_start, free_end
            FROM leases
            WHERE free_start IS NOT NULL
              AND free_end IS NOT NULL
              AND TRIM(free_start) != ''
              AND TRIM(free_end) != ''
            """
        ).fetchall()
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM lease_free_periods WHERE lease_id = ? LIMIT 1",
                (row["id"],),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO lease_free_periods (lease_id, start_date, end_date)
                VALUES (?, ?, ?)
                """,
                (row["id"], row["free_start"], row["free_end"]),
            )

    def _seed_settings(self, conn: sqlite3.Connection) -> None:
        defaults = dict(DEFAULT_SETTINGS)

        legacy = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", ("remind_days",)
        ).fetchone()
        if legacy is not None:
            defaults["lease_expire_remind_days"] = legacy["value"]
            defaults["rent_due_remind_days"] = legacy["value"]
        else:
            migrated_remind = conn.execute(
                "SELECT remind_days FROM projects ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if migrated_remind is not None:
                value = str(migrated_remind["remind_days"])
                defaults["lease_expire_remind_days"] = value
                defaults["rent_due_remind_days"] = value

        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


REQUIRED_TABLES = {
    "projects",
    "rooms",
    "leases",
    "payments",
    "lease_free_periods",
    "app_settings",
}


def validate_tally_database(path: Path) -> None:
    """校验是否为可用的 Tally SQLite 数据库。"""
    db_path = Path(path)
    if not db_path.is_file():
        raise ValueError("所选文件不存在")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"无法打开数据库文件：{exc}") from exc
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    except sqlite3.Error as exc:
        raise ValueError(f"数据库文件无效：{exc}") from exc
    finally:
        conn.close()
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise ValueError("不是有效的物业收费登记数据库（缺少表：" + "、".join(missing) + "）")


def export_database(source: Path, dest: Path) -> None:
    """导出数据库到目标路径（使用 SQLite backup，含 WAL 内容）。"""
    src = Path(source)
    if not src.is_file():
        raise ValueError("当前数据库文件不存在，请先保存配置并初始化数据")
    dst = Path(dest)
    if dst.resolve() == src.resolve():
        raise ValueError("导出路径不能与当前数据库相同")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(src)
    try:
        # 先写临时文件，再原子替换，避免导出中断留下残缺文件
        tmp = dst.with_name(dst.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        dst_conn = sqlite3.connect(tmp)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
        tmp.replace(dst)
    except sqlite3.Error as exc:
        raise ValueError(f"导出失败：{exc}") from exc
    finally:
        src_conn.close()


def import_database(source: Path, dest: Path) -> None:
    """用所选数据库覆盖当前库；覆盖前会生成 .bak 备份。"""
    src = Path(source)
    dst = Path(dest)
    validate_tally_database(src)
    if dst.exists() and src.resolve() == dst.resolve():
        raise ValueError("导入文件不能与当前数据库相同")
    dst.parent.mkdir(parents=True, exist_ok=True)

    tmp = dst.with_name(dst.name + ".importing")
    if tmp.exists():
        tmp.unlink()
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(tmp)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    except sqlite3.Error as exc:
        raise ValueError(f"导入失败：{exc}") from exc
    finally:
        src_conn.close()

    if dst.exists():
        bak = dst.with_suffix(dst.suffix + ".bak")
        if bak.exists():
            bak.unlink()
        dst.replace(bak)
    tmp.replace(dst)
    # 清理可能残留的 WAL / SHM，避免读到旧日志
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(dst) + suffix)
        if sidecar.exists():
            sidecar.unlink()