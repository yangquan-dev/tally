from __future__ import annotations

from datetime import date
from typing import Optional

from app.database import Database
from app.models import AppSettings, FreePeriod, Lease, Payment, Project, Room


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_all(self) -> dict[str, str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def get_value(self, key: str, default: str = "") -> str:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_value(self, key: str, value: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_settings(self) -> AppSettings:
        values = self.get_all()
        # 兼容旧版单一 remind_days
        legacy = values.get("remind_days")
        expire_default = legacy or "7"
        rent_default = legacy or "7"
        try:
            lease_expire_remind_days = int(
                values.get("lease_expire_remind_days", expire_default)
            )
        except ValueError:
            lease_expire_remind_days = 7
        try:
            rent_due_remind_days = int(values.get("rent_due_remind_days", rent_default))
        except ValueError:
            rent_due_remind_days = 7
        enabled_raw = (values.get("dingtalk_enabled") or "0").strip().lower()
        dingtalk_enabled = enabled_raw in {"1", "true", "yes", "on"}
        push_time = (values.get("dingtalk_push_time") or "09:00").strip() or "09:00"
        return AppSettings(
            lease_expire_remind_days=lease_expire_remind_days,
            rent_due_remind_days=rent_due_remind_days,
            dingtalk_enabled=dingtalk_enabled,
            dingtalk_webhook=(values.get("dingtalk_webhook") or "").strip(),
            dingtalk_secret=(values.get("dingtalk_secret") or "").strip(),
            dingtalk_push_time=push_time,
            dingtalk_last_push_date=(values.get("dingtalk_last_push_date") or "").strip(),
        )

    def save_settings(self, settings: AppSettings) -> None:
        self.set_value(
            "lease_expire_remind_days", str(settings.lease_expire_remind_days)
        )
        self.set_value("rent_due_remind_days", str(settings.rent_due_remind_days))
        self.set_value("dingtalk_enabled", "1" if settings.dingtalk_enabled else "0")
        self.set_value("dingtalk_webhook", settings.dingtalk_webhook or "")
        self.set_value("dingtalk_secret", settings.dingtalk_secret or "")
        self.set_value("dingtalk_push_time", settings.dingtalk_push_time or "09:00")
        # 必须允许写入空字符串，否则「清除今日已推」无法生效
        self.set_value(
            "dingtalk_last_push_date", settings.dingtalk_last_push_date or ""
        )


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> list[Project]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY id ASC"
            ).fetchall()
        return [Project.from_row(r) for r in rows]

    def get(self, project_id: int) -> Optional[Project]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return Project.from_row(row) if row else None

    def create(self, name: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO projects (name) VALUES (?)",
                (name,),
            )
            return int(cur.lastrowid)

    def update(self, project_id: int, name: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE projects
                SET name = ?
                WHERE id = ?
                """,
                (name, project_id),
            )

    def delete(self, project_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


class RoomRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_by_project(self, project_id: int) -> list[Room]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, p.name AS project_name
                FROM rooms r
                JOIN projects p ON p.id = r.project_id
                WHERE r.project_id = ?
                ORDER BY r.room_no
                """,
                (project_id,),
            ).fetchall()
        return [Room.from_row(r) for r in rows]

    def list_all(self) -> list[Room]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, p.name AS project_name
                FROM rooms r
                JOIN projects p ON p.id = r.project_id
                ORDER BY p.id ASC, r.room_no ASC
                """
            ).fetchall()
        return [Room.from_row(r) for r in rows]

    def get(self, room_id: int) -> Optional[Room]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*, p.name AS project_name
                FROM rooms r
                JOIN projects p ON p.id = r.project_id
                WHERE r.id = ?
                """,
                (room_id,),
            ).fetchone()
        return Room.from_row(row) if row else None

    def create(self, project_id: int, room_no: str, area: float) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO rooms (project_id, room_no, area, lease_status)
                VALUES (?, ?, ?, '空置')
                """,
                (project_id, room_no, area),
            )
            return int(cur.lastrowid)

    def update(self, room_id: int, room_no: str, area: float) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE rooms SET room_no = ?, area = ? WHERE id = ?",
                (room_no, area, room_id),
            )

    def set_status(self, room_id: int, status: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE rooms SET lease_status = ? WHERE id = ?",
                (status, room_id),
            )

    def delete(self, room_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))


class LeaseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _load_free_periods(self, lease_ids: list[int]) -> dict[int, list[FreePeriod]]:
        if not lease_ids:
            return {}
        placeholders = ",".join("?" for _ in lease_ids)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, lease_id, start_date, end_date
                FROM lease_free_periods
                WHERE lease_id IN ({placeholders})
                ORDER BY start_date ASC, id ASC
                """,
                lease_ids,
            ).fetchall()
        result: dict[int, list[FreePeriod]] = {lid: [] for lid in lease_ids}
        for row in rows:
            result[row["lease_id"]].append(
                FreePeriod(
                    id=row["id"],
                    start_date=date.fromisoformat(row["start_date"]),
                    end_date=date.fromisoformat(row["end_date"]),
                )
            )
        return result

    def _attach_free_periods(self, leases: list[Lease]) -> list[Lease]:
        mapping = self._load_free_periods([lease.id for lease in leases])
        for lease in leases:
            lease.free_periods = mapping.get(lease.id, [])
        return leases

    def list_all(
        self,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> list[Lease]:
        sql = """
            SELECT l.*, r.room_no, r.project_id, p.name AS project_name
            FROM leases l
            JOIN rooms r ON r.id = l.room_id
            JOIN projects p ON p.id = r.project_id
        """
        conditions: list[str] = []
        params: list[object] = []
        if status:
            conditions.append("l.status = ?")
            params.append(status)
        if project_id is not None:
            conditions.append("r.project_id = ?")
            params.append(project_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY l.end_date ASC, l.id DESC"
        with self.db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return self._attach_free_periods([Lease.from_row(r) for r in rows])

    def list_by_room(self, room_id: int) -> list[Lease]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT l.*, r.room_no, r.project_id, p.name AS project_name
                FROM leases l
                JOIN rooms r ON r.id = l.room_id
                JOIN projects p ON p.id = r.project_id
                WHERE l.room_id = ?
                ORDER BY l.start_date DESC
                """,
                (room_id,),
            ).fetchall()
        return self._attach_free_periods([Lease.from_row(r) for r in rows])

    def get(self, lease_id: int) -> Optional[Lease]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT l.*, r.room_no, r.project_id, p.name AS project_name
                FROM leases l
                JOIN rooms r ON r.id = l.room_id
                JOIN projects p ON p.id = r.project_id
                WHERE l.id = ?
                """,
                (lease_id,),
            ).fetchone()
        if not row:
            return None
        return self._attach_free_periods([Lease.from_row(row)])[0]

    def find_overlap(
        self,
        room_id: int,
        start_date: date,
        end_date: date,
        exclude_id: Optional[int] = None,
    ) -> Optional[Lease]:
        sql = """
            SELECT l.*, r.room_no, r.project_id, p.name AS project_name
            FROM leases l
            JOIN rooms r ON r.id = l.room_id
            JOIN projects p ON p.id = r.project_id
            WHERE l.room_id = ?
              AND l.status = '生效'
              AND date(l.start_date) <= date(?)
              AND date(l.end_date) >= date(?)
        """
        params: list = [room_id, end_date.isoformat(), start_date.isoformat()]
        if exclude_id is not None:
            sql += " AND l.id != ?"
            params.append(exclude_id)
        with self.db.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        return self._attach_free_periods([Lease.from_row(row)])[0]

    def replace_free_periods(
        self, lease_id: int, free_periods: list[tuple[date, date]]
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM lease_free_periods WHERE lease_id = ?", (lease_id,)
            )
            for start_date, end_date in free_periods:
                conn.execute(
                    """
                    INSERT INTO lease_free_periods (lease_id, start_date, end_date)
                    VALUES (?, ?, ?)
                    """,
                    (lease_id, start_date.isoformat(), end_date.isoformat()),
                )

    def create(
        self,
        room_id: int,
        deposit: float,
        monthly_rent: float,
        start_date: date,
        end_date: date,
        free_periods: list[tuple[date, date]],
        payment_period: str,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO leases (
                    room_id, deposit, monthly_rent, start_date, end_date,
                    status, payment_period
                ) VALUES (?, ?, ?, ?, ?, '生效', ?)
                """,
                (
                    room_id,
                    deposit,
                    monthly_rent,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    payment_period,
                ),
            )
            lease_id = int(cur.lastrowid)
        self.replace_free_periods(lease_id, free_periods)
        return lease_id

    def update(
        self,
        lease_id: int,
        deposit: float,
        monthly_rent: float,
        start_date: date,
        end_date: date,
        free_periods: list[tuple[date, date]],
        status: str,
        payment_period: str,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE leases
                SET deposit = ?, monthly_rent = ?, start_date = ?, end_date = ?,
                    status = ?, payment_period = ?
                WHERE id = ?
                """,
                (
                    deposit,
                    monthly_rent,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    status,
                    payment_period,
                    lease_id,
                ),
            )
        self.replace_free_periods(lease_id, free_periods)

    def delete(self, lease_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM leases WHERE id = ?", (lease_id,))


class PaymentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self, project_id: Optional[int] = None) -> list[Payment]:
        sql = """
            SELECT pay.*, r.room_no, r.project_id, p.name AS project_name
            FROM payments pay
            JOIN leases l ON l.id = pay.lease_id
            JOIN rooms r ON r.id = l.room_id
            JOIN projects p ON p.id = r.project_id
        """
        params: tuple = ()
        if project_id is not None:
            sql += " WHERE r.project_id = ?"
            params = (project_id,)
        sql += " ORDER BY pay.period_start DESC, pay.id DESC"
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Payment.from_row(r) for r in rows]

    def list_by_lease(self, lease_id: int) -> list[Payment]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT pay.*, r.room_no, r.project_id, p.name AS project_name
                FROM payments pay
                JOIN leases l ON l.id = pay.lease_id
                JOIN rooms r ON r.id = l.room_id
                JOIN projects p ON p.id = r.project_id
                WHERE pay.lease_id = ?
                ORDER BY pay.period_start DESC
                """,
                (lease_id,),
            ).fetchall()
        return [Payment.from_row(r) for r in rows]

    def get(self, payment_id: int) -> Optional[Payment]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT pay.*, r.room_no, r.project_id, p.name AS project_name
                FROM payments pay
                JOIN leases l ON l.id = pay.lease_id
                JOIN rooms r ON r.id = l.room_id
                JOIN projects p ON p.id = r.project_id
                WHERE pay.id = ?
                """,
                (payment_id,),
            ).fetchone()
        return Payment.from_row(row) if row else None

    def create(
        self,
        lease_id: int,
        period_start: date,
        period_end: date,
        amount: float,
        paid_at: date,
        note: str = "",
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO payments (
                    lease_id, period_start, period_end, amount, paid_at, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    lease_id,
                    period_start.isoformat(),
                    period_end.isoformat(),
                    amount,
                    paid_at.isoformat(),
                    note,
                ),
            )
            return int(cur.lastrowid)

    def update(
        self,
        payment_id: int,
        period_start: date,
        period_end: date,
        amount: float,
        paid_at: date,
        note: str,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE payments
                SET period_start = ?, period_end = ?, amount = ?, paid_at = ?, note = ?
                WHERE id = ?
                """,
                (
                    period_start.isoformat(),
                    period_end.isoformat(),
                    amount,
                    paid_at.isoformat(),
                    note,
                    payment_id,
                ),
            )

    def delete(self, payment_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
