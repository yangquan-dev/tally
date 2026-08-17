from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

# 项目名称、租户等短文本上限
NAME_MAX_LENGTH = 100


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
    amount: float = 0.0  # 兼容旧库字段；免租费用按起止日期自动计算
    id: int = 0

    def label(self) -> str:
        # 使用不间断空格，保证同一起止区间不中途折行
        return f"{self.start_date.isoformat()}\u00a0~\u00a0{self.end_date.isoformat()}"


DISCOUNT_KIND_RATE = "rate"
DISCOUNT_KIND_AMOUNT = "amount"
DISCOUNT_KIND_OPTIONS = (DISCOUNT_KIND_RATE, DISCOUNT_KIND_AMOUNT)
DISCOUNT_KIND_LABELS = {
    DISCOUNT_KIND_RATE: "折扣",
    DISCOUNT_KIND_AMOUNT: "立减",
}

FEE_TYPE_RENT = "租金"
FEE_TYPE_DEPOSIT = "押金"
FEE_TYPE_OPTIONS = (FEE_TYPE_RENT, FEE_TYPE_DEPOSIT)


def normalize_fee_type(value: str | None) -> str:
    text = (value or "").strip()
    if text in FEE_TYPE_OPTIONS:
        return text
    return FEE_TYPE_RENT


@dataclass
class LeaseDiscount:
    start_date: date
    end_date: date
    kind: str  # "rate" | "amount"
    value: float
    id: int = 0

    @staticmethod
    def period_text(start: date, end: date) -> str:
        """月周期展示到天：YYYY-MM-DD ~ YYYY-MM-DD。"""
        # 不间断空格，避免起止区间中途折行
        return f"{start.isoformat()}\u00a0~\u00a0{end.isoformat()}"

    def value_text(self) -> str:
        return f"{self.value:.2f}"

    def label(self) -> str:
        kind_label = DISCOUNT_KIND_LABELS.get(self.kind, self.kind)
        return f"{self.period_text(self.start_date, self.end_date)} {kind_label} {self.value_text()}"


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
    tenant: str = ""
    free_periods: list[FreePeriod] | None = None
    discounts: list[LeaseDiscount] | None = None
    room_no: str = ""
    project_id: int = 0
    project_name: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.free_periods is None:
            self.free_periods = []
        if self.discounts is None:
            self.discounts = []
        self.payment_period = normalize_payment_period(self.payment_period)
        self.tenant = (self.tenant or "").strip()

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
            tenant=row["tenant"] if "tenant" in keys else "",
            free_periods=[],
            discounts=[],
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

    def discounts_label(self) -> str:
        """按租赁月添加的折/减：连续同规则合并，起止显示到天。"""
        from datetime import timedelta

        items = sorted(self.discounts or [], key=lambda d: d.start_date)
        if not items:
            return ""

        groups: list[tuple[date, date, LeaseDiscount]] = []
        for item in items:
            if groups:
                g_start, g_end, sample = groups[-1]
                same_rule = item.kind == sample.kind and float(item.value) == float(
                    sample.value
                )
                contiguous = item.start_date == g_end + timedelta(days=1)
                if same_rule and contiguous:
                    groups[-1] = (g_start, item.end_date, sample)
                    continue
            groups.append((item.start_date, item.end_date, item))

        lines: list[str] = []
        for start, end, sample in groups:
            kind_label = DISCOUNT_KIND_LABELS.get(sample.kind, sample.kind)
            period = LeaseDiscount.period_text(start, end)
            lines.append(f"{period} {kind_label} {sample.value_text()}")
        return "\n".join(lines)


@dataclass
class Payment:
    id: int
    lease_id: int
    period_start: date
    period_end: date
    amount: float
    paid_at: date
    note: str
    fee_type: str = FEE_TYPE_RENT
    room_no: str = ""
    project_name: str = ""
    project_id: int = 0
    tenant: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.fee_type = normalize_fee_type(self.fee_type)
        self.tenant = (self.tenant or "").strip()

    @classmethod
    def from_row(cls, row: Any) -> "Payment":
        keys = row.keys()
        created_at = row["created_at"] if "created_at" in keys else ""
        updated_at = row["updated_at"] if "updated_at" in keys else ""
        return cls(
            id=row["id"],
            lease_id=row["lease_id"],
            period_start=_parse_date(row["period_start"]),  # type: ignore[arg-type]
            period_end=_parse_date(row["period_end"]),  # type: ignore[arg-type]
            amount=row["amount"],
            paid_at=_parse_date(row["paid_at"]),  # type: ignore[arg-type]
            note=row["note"] or "",
            fee_type=normalize_fee_type(
                row["fee_type"] if "fee_type" in keys else None
            ),
            room_no=row["room_no"] if "room_no" in keys else "",
            project_name=row["project_name"] if "project_name" in keys else "",
            project_id=row["project_id"] if "project_id" in keys else 0,
            tenant=row["tenant"] if "tenant" in keys else "",
            created_at=created_at or "",
            updated_at=updated_at or created_at or "",
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
    kind: str  # 押金应收 / 租金逾期 / 租金应收 / 合同到期（将到期）
    project_id: int
    project_name: str
    room_id: int
    room_no: str
    lease_id: int
    period_start: Optional[date]
    period_end: Optional[date]
    amount: float  # 剩余应缴；合同类仅作占位（看板展示续签提示）
    days_delta: int
    detail: str
    tenant: str = ""  # 租户
    due_amount: float = 0.0  # 应缴（折减前）
    paid_amount: float = 0.0  # 已缴
    discount_amount: float = 0.0  # 折/减
    free_amount: float = 0.0  # 免租（按免租期起止计算）


REMINDER_KIND_DEPOSIT = "押金应收"
REMINDER_KIND_RENT_OVERDUE = "租金逾期"
REMINDER_KIND_RENT_DUE = "租金应收"
REMINDER_KIND_CONTRACT_EXPIRED = "合同到期"
REMINDER_KIND_ORDER = (
    REMINDER_KIND_DEPOSIT,
    REMINDER_KIND_RENT_OVERDUE,
    REMINDER_KIND_RENT_DUE,
    REMINDER_KIND_CONTRACT_EXPIRED,
)
REMINDER_KIND_RANK = {kind: idx for idx, kind in enumerate(REMINDER_KIND_ORDER)}
