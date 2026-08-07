from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional


def _parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass
class Project:
    id: int
    name: str
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Project":
        return cls(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"] if "created_at" in row.keys() else "",
        )


@dataclass
class AppSettings:
    app_name: str = "本地记账"
    data_storage_path: str = ""
    storage_locked: bool = False
    lease_expire_remind_days: int = 7
    rent_due_remind_days: int = 7
    dingtalk_enabled: bool = False
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    dingtalk_push_time: str = "09:00"
    dingtalk_last_push_date: str = ""


@dataclass
class Room:
    id: int
    project_id: int
    room_no: str
    area: float
    lease_status: str
    project_name: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Room":
        keys = row.keys()
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            room_no=row["room_no"],
            area=row["area"],
            lease_status=row["lease_status"],
            project_name=row["project_name"] if "project_name" in keys else "",
        )


@dataclass
class FreePeriod:
    start_date: date
    end_date: date
    id: int = 0

    def label(self) -> str:
        return f"{self.start_date.isoformat()} ~ {self.end_date.isoformat()}"


PAYMENT_PERIOD_OPTIONS = ("季度", "半年", "年")
DEFAULT_PAYMENT_PERIOD = "季度"
PAYMENT_PERIOD_MONTHS = {"季度": 3, "半年": 6, "年": 12}


def normalize_payment_period(value: str | None) -> str:
    text = (value or "").strip()
    if text not in PAYMENT_PERIOD_OPTIONS:
        return DEFAULT_PAYMENT_PERIOD
    return text


def payment_period_months(value: str | None) -> int:
    return PAYMENT_PERIOD_MONTHS[normalize_payment_period(value)]


@dataclass
class Lease:
    id: int
    room_id: int
    deposit: float
    monthly_rent: float
    start_date: date
    end_date: date
    status: str
    payment_period: str = DEFAULT_PAYMENT_PERIOD
    free_periods: list[FreePeriod] | None = None
    room_no: str = ""
    project_id: int = 0
    project_name: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.free_periods is None:
            self.free_periods = []
        self.payment_period = normalize_payment_period(self.payment_period)

    @property
    def period_months(self) -> int:
        return payment_period_months(self.payment_period)

    @classmethod
    def from_row(cls, row: Any) -> "Lease":
        keys = row.keys()
        return cls(
            id=row["id"],
            room_id=row["room_id"],
            deposit=row["deposit"],
            monthly_rent=row["monthly_rent"],
            start_date=_parse_date(row["start_date"]),  # type: ignore[arg-type]
            end_date=_parse_date(row["end_date"]),  # type: ignore[arg-type]
            status=row["status"],
            payment_period=normalize_payment_period(
                row["payment_period"] if "payment_period" in keys else None
            ),
            free_periods=[],
            room_no=row["room_no"] if "room_no" in keys else "",
            project_id=row["project_id"] if "project_id" in keys else 0,
            project_name=row["project_name"] if "project_name" in keys else "",
            created_at=row["created_at"] if "created_at" in keys else "",
        )

    def free_periods_label(self) -> str:
        periods = self.free_periods or []
        if not periods:
            return ""
        return "\n".join(p.label() for p in periods)


@dataclass
class Payment:
    id: int
    lease_id: int
    period_start: date
    period_end: date
    amount: float
    paid_at: date
    note: str
    room_no: str = ""
    project_name: str = ""
    project_id: int = 0

    @classmethod
    def from_row(cls, row: Any) -> "Payment":
        keys = row.keys()
        return cls(
            id=row["id"],
            lease_id=row["lease_id"],
            period_start=_parse_date(row["period_start"]),  # type: ignore[arg-type]
            period_end=_parse_date(row["period_end"]),  # type: ignore[arg-type]
            amount=row["amount"],
            paid_at=_parse_date(row["paid_at"]),  # type: ignore[arg-type]
            note=row["note"] or "",
            room_no=row["room_no"] if "room_no" in keys else "",
            project_name=row["project_name"] if "project_name" in keys else "",
            project_id=row["project_id"] if "project_id" in keys else 0,
        )


@dataclass
class RentPeriod:
    lease_id: int
    period_start: date
    period_end: date
    amount: float
    fully_free: bool = False
    partial_free: bool = False


@dataclass
class ReminderItem:
    kind: str  # 应收提醒 / 已逾期 / 合同即将到期
    project_id: int
    project_name: str
    room_id: int
    room_no: str
    lease_id: int
    period_start: Optional[date]
    period_end: Optional[date]
    amount: float
    days_delta: int
    detail: str
