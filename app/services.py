from __future__ import annotations

from calendar import monthrange
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

from app.bootstrap import BootstrapConfig
from app.database import Database
from app.dingtalk import (
    format_last_push_at,
    format_reminders_markdown,
    parse_push_time,
    send_markdown,
    should_clear_last_push_on_save,
    should_push_today,
)
from app.license import (
    LicenseInfo,
    check_license_file,
    import_license_file,
    remove_license_file,
)
from app.models import (
    AppSettings,
    DEFAULT_PAYMENT_PERIOD,
    DISCOUNT_KIND_AMOUNT,
    DISCOUNT_KIND_OPTIONS,
    DISCOUNT_KIND_RATE,
    FEE_TYPE_DEPOSIT,
    FEE_TYPE_RENT,
    FreePeriod,
    Lease,
    LeaseDiscount,
    PAYMENT_PERIOD_OPTIONS,
    Payment,
    Project,
    REMINDER_KIND_CONTRACT_EXPIRED,
    REMINDER_KIND_DEPOSIT,
    REMINDER_KIND_RANK,
    REMINDER_KIND_RENT_DUE,
    REMINDER_KIND_RENT_OVERDUE,
    ReminderItem,
    RentPeriod,
    Room,
    normalize_fee_type,
)
from app.repositories import (
    LeaseRepository,
    PaymentRepository,
    ProjectRepository,
    RoomRepository,
    SettingsRepository,
)


class ValidationError(Exception):
    pass


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _iter_lease_billing_months(
    lease_start: date, lease_end: date
) -> list[tuple[date, date]]:
    """按起租日对齐的租赁月周期切片（含首尾，末月裁切到到期日）。"""
    if lease_end < lease_start:
        return []
    months: list[tuple[date, date]] = []
    idx = 0
    while idx <= 600:
        slice_start = _add_months(lease_start, idx)
        if slice_start > lease_end:
            break
        slice_end = min(
            _add_months(lease_start, idx + 1) - timedelta(days=1),
            lease_end,
        )
        months.append((slice_start, slice_end))
        if slice_end >= lease_end:
            break
        idx += 1
    return months


def _billing_month_gross_rent(
    monthly_rent: float,
    lease_start: date,
    slice_start: date,
    slice_end: date,
) -> float:
    """租赁月周期应缴基数：完整月为月租，非完整月按天比例折算。"""
    rent = float(monthly_rent)
    if rent <= 0 or slice_end < slice_start:
        return 0.0
    idx = 0
    while idx <= 600:
        start = _add_months(lease_start, idx)
        if start == slice_start:
            full_end = _add_months(lease_start, idx + 1) - timedelta(days=1)
            full_days = (full_end - slice_start).days + 1
            charge_days = (slice_end - slice_start).days + 1
            if full_days <= 0 or charge_days <= 0:
                return 0.0
            return round(rent * charge_days / full_days, 2)
        if start > slice_start:
            break
        idx += 1
    return round(rent, 2)


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def _fully_covers(outer_start: date, outer_end: date, inner_start: date, inner_end: date) -> bool:
    return outer_start <= inner_start and outer_end >= inner_end


def _merge_date_ranges(periods: list[tuple[date, date]]) -> list[tuple[date, date]]:
    if not periods:
        return []
    ordered = sorted(periods, key=lambda item: item[0])
    merged: list[tuple[date, date]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + timedelta(days=1):
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _range_fully_covered(
    start: date, end: date, periods: list[tuple[date, date]]
) -> bool:
    if not periods or end < start:
        return False
    cursor = start
    for seg_start, seg_end in _merge_date_ranges(periods):
        if seg_end < cursor:
            continue
        if seg_start > cursor:
            return False
        if seg_end >= end:
            return True
        cursor = seg_end + timedelta(days=1)
    return cursor > end


def _range_any_overlap(
    start: date, end: date, periods: list[tuple[date, date]]
) -> bool:
    return any(_overlaps(start, end, p_start, p_end) for p_start, p_end in periods)


def _covered_days(start: date, end: date, periods: list[tuple[date, date]]) -> int:
    """计算 [start, end] 内被 periods 覆盖的天数（periods 先合并）。"""
    if end < start or not periods:
        return 0
    total = 0
    for seg_start, seg_end in _merge_date_ranges(periods):
        ov_start = max(start, seg_start)
        ov_end = min(end, seg_end)
        if ov_end >= ov_start:
            total += (ov_end - ov_start).days + 1
    return total


class SettingsService:
    def __init__(
        self, bootstrap: BootstrapConfig, db: Database | None = None
    ) -> None:
        self.bootstrap = bootstrap
        self.repo = SettingsRepository(db) if db is not None else None

    def attach_db(self, db: Database) -> None:
        self.repo = SettingsRepository(db)

    def is_ready(self) -> bool:
        return self.bootstrap.is_storage_configured() and self.repo is not None

    def get(self) -> AppSettings:
        storage = self.bootstrap.get_data_storage_path()
        remind = AppSettings()
        if self.repo is not None:
            remind = self.repo.get_settings()
        return AppSettings(
            app_name=self.bootstrap.app_name,
            data_storage_path=str(storage) if storage else "",
            storage_locked=self.bootstrap.is_storage_configured(),
            lease_expire_remind_days=remind.lease_expire_remind_days,
            rent_due_remind_days=remind.rent_due_remind_days,
            dingtalk_enabled=remind.dingtalk_enabled,
            dingtalk_webhook=remind.dingtalk_webhook,
            dingtalk_secret=remind.dingtalk_secret,
            dingtalk_push_time=remind.dingtalk_push_time,
            dingtalk_last_push_date=remind.dingtalk_last_push_date,
        )

    def update(
        self,
        app_name: str,
        data_storage_path: str | None,
        lease_expire_remind_days: int | None = None,
        rent_due_remind_days: int | None = None,
        dingtalk_enabled: bool | None = None,
        dingtalk_webhook: str | None = None,
        dingtalk_secret: str | None = None,
        dingtalk_push_time: str | None = None,
    ) -> bool:
        """更新配置。返回是否新配置了数据存储位置。"""
        try:
            self.bootstrap.set_app_name(app_name)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        storage_just_set = False
        if self.bootstrap.is_portable():
            # 打包版存储路径由程序固定，忽略界面传入值
            pass
        elif not self.bootstrap.is_storage_configured():
            path = (data_storage_path or "").strip()
            if not path:
                raise ValidationError("请先配置数据存储位置")
            try:
                self.bootstrap.set_data_storage_path(path)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            storage_just_set = True
        elif data_storage_path is not None:
            current = self.bootstrap.get_data_storage_path()
            incoming = str(Path(data_storage_path).expanduser().resolve())
            if current and incoming != str(current):
                raise ValidationError("数据存储位置已配置，不可修改")

        if self.repo is None:
            return storage_just_set

        if lease_expire_remind_days is None or rent_due_remind_days is None:
            raise ValidationError("请填写提醒天数")
        if lease_expire_remind_days < 0:
            raise ValidationError("合同到期提前提醒天数不能为负数")
        if rent_due_remind_days < 0:
            raise ValidationError("按月应收提前提醒天数不能为负数")

        current = self.repo.get_settings()
        enabled = (
            current.dingtalk_enabled
            if dingtalk_enabled is None
            else bool(dingtalk_enabled)
        )
        webhook = (
            current.dingtalk_webhook
            if dingtalk_webhook is None
            else dingtalk_webhook.strip()
        )
        secret = (
            current.dingtalk_secret
            if dingtalk_secret is None
            else dingtalk_secret.strip()
        )
        push_time = (
            current.dingtalk_push_time
            if dingtalk_push_time is None
            else dingtalk_push_time.strip()
        ) or "09:00"
        try:
            parse_push_time(push_time)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if enabled and not webhook:
            raise ValidationError("启用钉钉推送时请填写 Webhook 地址")
        if enabled and not secret:
            raise ValidationError("启用钉钉推送时请填写加签密钥")
        if webhook and not webhook.startswith(("http://", "https://")):
            raise ValidationError("Webhook 地址应以 http:// 或 https:// 开头")

        from datetime import datetime

        last_push = current.dingtalk_last_push_date
        now = datetime.now()
        # 推送时刻仍在今日未来：清除今日已推标记，到点后再次触发
        if should_clear_last_push_on_save(
            enabled=enabled,
            push_time=push_time,
            now_hour=now.hour,
            now_minute=now.minute,
        ):
            last_push = ""

        self.repo.save_settings(
            AppSettings(
                lease_expire_remind_days=lease_expire_remind_days,
                rent_due_remind_days=rent_due_remind_days,
                dingtalk_enabled=enabled,
                dingtalk_webhook=webhook,
                dingtalk_secret=secret,
                dingtalk_push_time=push_time,
                dingtalk_last_push_date=last_push,
            )
        )
        # 清除上次失败信息（配置已更新，等待下次调度）
        if self.repo is not None:
            self.repo.set_value("dingtalk_last_error", "")
        return storage_just_set

    def mark_dingtalk_pushed(self, when: Optional[datetime] = None) -> None:
        if self.repo is None:
            return
        self.repo.set_value("dingtalk_last_push_date", format_last_push_at(when))
        self.repo.set_value("dingtalk_last_error", "")

    def mark_dingtalk_error(self, message: str) -> None:
        if self.repo is None:
            return
        self.repo.set_value("dingtalk_last_error", (message or "").strip()[:500])

    def dingtalk_last_error(self) -> str:
        if self.repo is None:
            return ""
        return self.repo.get_value("dingtalk_last_error", "")


class ProjectService:
    def __init__(self, db: Database) -> None:
        self.repo = ProjectRepository(db)

    def list_all(self) -> list[Project]:
        return self.repo.list_all()

    def get(self, project_id: int) -> Optional[Project]:
        return self.repo.get(project_id)

    def create(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValidationError("项目名称不能为空")
        return self.repo.create(name)

    def update(self, project_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValidationError("项目名称不能为空")
        self.repo.update(project_id, name)

    def delete(self, project_id: int) -> None:
        self.repo.delete(project_id)


class RoomService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = RoomRepository(db)
        self.lease_repo = LeaseRepository(db)
        self.settings_repo = SettingsRepository(db)

    def list_by_project(self, project_id: int) -> list[Room]:
        return self.repo.list_by_project(project_id)

    def list_all(self) -> list[Room]:
        return self.repo.list_all()

    def get(self, room_id: int) -> Optional[Room]:
        return self.repo.get(room_id)

    def create(self, project_id: int, room_no: str, area: float) -> int:
        room_no = room_no.strip()
        if not room_no:
            raise ValidationError("房间号不能为空")
        if area < 0:
            raise ValidationError("房间面积不能为负数")
        try:
            return self.repo.create(project_id, room_no, area)
        except Exception as exc:
            raise ValidationError(f"创建房间失败，可能房间号重复：{exc}") from exc

    def update(self, room_id: int, room_no: str, area: float) -> None:
        room_no = room_no.strip()
        if not room_no:
            raise ValidationError("房间号不能为空")
        if area < 0:
            raise ValidationError("房间面积不能为负数")
        try:
            self.repo.update(room_id, room_no, area)
        except Exception as exc:
            raise ValidationError(f"更新房间失败，可能房间号重复：{exc}") from exc

    def delete(self, room_id: int) -> None:
        self.repo.delete(room_id)

    def refresh_status(self, room_id: int, today: Optional[date] = None) -> None:
        today = today or date.today()
        leases = self.lease_repo.list_by_room(room_id)
        active = [l for l in leases if l.status == "生效"]
        if not active:
            self.repo.set_status(room_id, "空置")
            return

        # 取当前覆盖 today 的合同，否则取最近到期的生效合同
        current = next(
            (l for l in active if l.start_date <= today <= l.end_date),
            sorted(active, key=lambda l: l.end_date)[0],
        )
        expire_days = self.settings_repo.get_settings().lease_expire_remind_days
        days_left = (current.end_date - today).days
        if days_left < 0:
            self.repo.set_status(room_id, "已到期")
        elif days_left <= expire_days:
            self.repo.set_status(room_id, "即将到期")
        else:
            self.repo.set_status(room_id, "在租")


class LeaseService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = LeaseRepository(db)
        self.room_service = RoomService(db)

    def list_all(
        self,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> list[Lease]:
        return self.repo.list_all(status, project_id)

    def list_by_room(self, room_id: int) -> list[Lease]:
        return self.repo.list_by_room(room_id)

    def get(self, lease_id: int) -> Optional[Lease]:
        return self.repo.get(lease_id)

    def _normalize_free_periods(
        self,
        free_periods: list[tuple[date, date]] | list[FreePeriod],
        start_date: date,
        end_date: date,
    ) -> list[tuple[date, date]]:
        normalized: list[tuple[date, date]] = []
        for idx, item in enumerate(free_periods, start=1):
            if isinstance(item, FreePeriod):
                free_start, free_end = item.start_date, item.end_date
            else:
                free_start, free_end = item[0], item[1]
            if free_end < free_start:
                raise ValidationError(f"第 {idx} 段免租期止不能早于免租期起")
            if free_start < start_date or free_end > end_date:
                raise ValidationError(f"第 {idx} 段免租期必须落在租赁期内")
            normalized.append((free_start, free_end))

        ordered = sorted(normalized, key=lambda item: item[0])
        for i in range(1, len(ordered)):
            prev_start, prev_end = ordered[i - 1]
            curr_start, curr_end = ordered[i]
            if _overlaps(prev_start, prev_end, curr_start, curr_end):
                raise ValidationError("免租期时段不能互相重叠")
        return ordered

    def _normalize_discounts(
        self,
        discounts: list[tuple[date, date, str, float]] | list[LeaseDiscount],
        start_date: date,
        end_date: date,
        monthly_rent: float,
    ) -> list[tuple[date, date, str, float]]:
        normalized: list[tuple[date, date, str, float]] = []
        rent = float(monthly_rent)
        lease_months = {
            (s, e): _billing_month_gross_rent(rent, start_date, s, e)
            for s, e in _iter_lease_billing_months(start_date, end_date)
        }
        for idx, item in enumerate(discounts, start=1):
            if isinstance(item, LeaseDiscount):
                d_start, d_end, kind, value = (
                    item.start_date,
                    item.end_date,
                    item.kind,
                    item.value,
                )
            else:
                d_start, d_end, kind, value = item
            kind = (kind or "").strip()
            if kind not in DISCOUNT_KIND_OPTIONS:
                raise ValidationError(f"第 {idx} 段折/减类型无效")
            if d_end < d_start:
                raise ValidationError(f"第 {idx} 段折/减结束日不能早于开始日")
            if d_start < start_date or d_end > end_date:
                raise ValidationError(f"第 {idx} 段折/减必须落在租赁期内")
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"第 {idx} 段折/减数值无效") from exc
            if kind == DISCOUNT_KIND_RATE:
                if not (0 < numeric < 1):
                    raise ValidationError(
                        f"第 {idx} 段折扣须大于 0 且小于 1（如 0.85 表示实付 85%）"
                    )
            elif numeric <= 0:
                raise ValidationError(f"第 {idx} 段立减金额须大于 0")
            if (d_start, d_end) not in lease_months:
                raise ValidationError(
                    f"第 {idx} 段折/减须按租赁月周期设置"
                    f"（例如起租 {start_date} 的首月为 "
                    f"{start_date} ~ "
                    f"{min(_add_months(start_date, 1) - timedelta(days=1), end_date)}）"
                )
            if kind == DISCOUNT_KIND_AMOUNT:
                cap = lease_months[(d_start, d_end)]
                if numeric > cap + 1e-9:
                    raise ValidationError(
                        f"第 {idx} 段立减金额不能超过该月周期应缴"
                        f"（{d_start} ~ {d_end} 应缴 {cap:g}）"
                    )
            normalized.append((d_start, d_end, kind, numeric))

        ordered = sorted(normalized, key=lambda item: item[0])
        for i in range(1, len(ordered)):
            prev_start, prev_end = ordered[i - 1][0], ordered[i - 1][1]
            curr_start, curr_end = ordered[i][0], ordered[i][1]
            if _overlaps(prev_start, prev_end, curr_start, curr_end):
                raise ValidationError("折/减月份不能互相重叠")
        return ordered

    def _validate(
        self,
        room_id: int,
        deposit: float,
        monthly_rent: float,
        start_date: date,
        end_date: date,
        free_periods: list[tuple[date, date]] | list[FreePeriod],
        payment_period: str,
        discounts: list[tuple[date, date, str, float]] | list[LeaseDiscount],
        exclude_id: Optional[int] = None,
    ) -> tuple[list[tuple[date, date]], str, list[tuple[date, date, str, float]]]:
        if deposit < 0 or monthly_rent < 0:
            raise ValidationError("押金和月租金不能为负数")
        if end_date < start_date:
            raise ValidationError("到期时间不能早于起租时间")
        period = (payment_period or "").strip()
        if not period:
            raise ValidationError("缴费周期不能为空")
        if period not in PAYMENT_PERIOD_OPTIONS:
            raise ValidationError("缴费周期无效")
        normalized = self._normalize_free_periods(free_periods, start_date, end_date)
        normalized_discounts = self._normalize_discounts(
            discounts, start_date, end_date, monthly_rent
        )
        overlap = self.repo.find_overlap(room_id, start_date, end_date, exclude_id)
        if overlap:
            raise ValidationError(
                f"与已有生效合同时间重叠（{overlap.start_date} ~ {overlap.end_date}）"
            )
        return normalized, period, normalized_discounts

    def create(
        self,
        room_id: int,
        deposit: float,
        monthly_rent: float,
        start_date: date,
        end_date: date,
        free_periods: list[tuple[date, date]] | None = None,
        payment_period: str = DEFAULT_PAYMENT_PERIOD,
        discounts: list[tuple[date, date, str, float]] | list[LeaseDiscount]
        | None = None,
        tenant: str = "",
    ) -> int:
        tenant_name = (tenant or "").strip()
        if not tenant_name:
            raise ValidationError("租户不能为空")
        normalized, period, normalized_discounts = self._validate(
            room_id,
            deposit,
            monthly_rent,
            start_date,
            end_date,
            free_periods or [],
            payment_period,
            discounts or [],
        )
        lease_id = self.repo.create(
            room_id,
            deposit,
            monthly_rent,
            start_date,
            end_date,
            normalized,
            period,
            normalized_discounts,
            tenant=tenant_name,
        )
        self.room_service.refresh_status(room_id)
        return lease_id

    def update(
        self,
        lease_id: int,
        deposit: float,
        monthly_rent: float,
        start_date: date,
        end_date: date,
        free_periods: list[tuple[date, date]] | list[FreePeriod] | None,
        status: str,
        payment_period: str = DEFAULT_PAYMENT_PERIOD,
        discounts: list[tuple[date, date, str, float]] | list[LeaseDiscount]
        | None = None,
        tenant: str | None = None,
    ) -> None:
        lease = self.repo.get(lease_id)
        if not lease:
            raise ValidationError("租赁不存在")
        if status not in {"生效", "结束"}:
            raise ValidationError("租赁状态无效")
        tenant_name = (
            (tenant if tenant is not None else lease.tenant) or ""
        ).strip()
        # 生效合同必须填写租户；结束旧数据可保留空值以便兼容迁移
        if status == "生效" and not tenant_name:
            raise ValidationError("租户不能为空")
        periods = free_periods if free_periods is not None else (lease.free_periods or [])
        discount_items = (
            discounts if discounts is not None else (lease.discounts or [])
        )
        if status == "生效":
            normalized, period, normalized_discounts = self._validate(
                lease.room_id,
                deposit,
                monthly_rent,
                start_date,
                end_date,
                periods,
                payment_period,
                discount_items,
                exclude_id=lease_id,
            )
        else:
            if end_date < start_date:
                raise ValidationError("到期时间不能早于起租时间")
            period = (payment_period or "").strip()
            if not period:
                raise ValidationError("缴费周期不能为空")
            if period not in PAYMENT_PERIOD_OPTIONS:
                raise ValidationError("缴费周期无效")
            normalized = self._normalize_free_periods(periods, start_date, end_date)
            normalized_discounts = self._normalize_discounts(
                discount_items, start_date, end_date, monthly_rent
            )
        self.repo.update(
            lease_id,
            deposit,
            monthly_rent,
            start_date,
            end_date,
            normalized,
            status,
            period,
            normalized_discounts,
            tenant=tenant_name,
        )
        self.room_service.refresh_status(lease.room_id)

    def delete(self, lease_id: int) -> None:
        lease = self.repo.get(lease_id)
        if not lease:
            return
        self.repo.delete(lease_id)
        self.room_service.refresh_status(lease.room_id)


class PaymentService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = PaymentRepository(db)
        self.lease_repo = LeaseRepository(db)

    def list_all(
        self,
        project_id: Optional[int] = None,
        fee_type: Optional[str] = None,
    ) -> list[Payment]:
        return self.repo.list_all(project_id, fee_type=fee_type)

    def list_by_lease(
        self, lease_id: int, fee_type: Optional[str] = None
    ) -> list[Payment]:
        return self.repo.list_by_lease(lease_id, fee_type=fee_type)

    def get(self, payment_id: int) -> Optional[Payment]:
        return self.repo.get(payment_id)

    def deposit_paid(
        self, lease_id: int, *, exclude_payment_id: Optional[int] = None
    ) -> float:
        total = 0.0
        for pay in self.repo.list_by_lease(lease_id, fee_type=FEE_TYPE_DEPOSIT):
            if exclude_payment_id is not None and pay.id == exclude_payment_id:
                continue
            total += float(pay.amount)
        return round(total, 2)

    def deposit_remaining(
        self, lease_id: int, *, exclude_payment_id: Optional[int] = None
    ) -> float:
        lease = self.lease_repo.get(lease_id)
        if not lease:
            raise ValidationError("租赁不存在")
        paid = self.deposit_paid(lease_id, exclude_payment_id=exclude_payment_id)
        return round(max(0.0, float(lease.deposit) - paid), 2)

    def _validate(
        self,
        lease_id: int,
        period_start: date,
        period_end: date,
        amount: float,
        *,
        exclude_payment_id: Optional[int] = None,
        fee_type: str = FEE_TYPE_RENT,
    ) -> str:
        fee = normalize_fee_type(fee_type)
        if amount <= 0:
            raise ValidationError("缴费金额必须大于 0")
        lease = self.lease_repo.get(lease_id)
        if not lease:
            raise ValidationError("租赁不存在")
        if fee == FEE_TYPE_DEPOSIT:
            remaining = self.deposit_remaining(
                lease_id, exclude_payment_id=exclude_payment_id
            )
            if amount > remaining + 0.009:
                raise ValidationError(
                    f"押金剩余应缴 ¥{remaining:.2f}，当次缴纳不能超过该金额"
                )
            return fee
        if period_end < period_start:
            raise ValidationError("缴费对应结束时间不能早于起始时间")
        if period_start < lease.start_date or period_end > lease.end_date:
            raise ValidationError("缴费周期必须落在租赁期内")
        self._validate_period_cap(
            lease,
            period_start,
            period_end,
            amount,
            exclude_payment_id=exclude_payment_id,
        )
        return fee

    def _validate_period_cap(
        self,
        lease: Lease,
        period_start: date,
        period_end: date,
        amount: float,
        *,
        exclude_payment_id: Optional[int] = None,
    ) -> None:
        """缴费可覆盖连续多个应收期；按 FIFO 分摊后，各期累计不得超过该期应缴。"""
        reminder_svc = ReminderService(self.db)
        candidate = Payment(
            id=exclude_payment_id or 0,
            lease_id=lease.id,
            period_start=period_start,
            period_end=period_end,
            amount=amount,
            paid_at=period_start,
            note="",
            fee_type=FEE_TYPE_RENT,
        )
        periods = reminder_svc._chargeable_periods(lease)
        covered = [
            period
            for period in periods
            if ReminderService._payment_overlaps_period(candidate, period)
        ]
        if not covered:
            raise ValidationError("缴费起止未覆盖任何应收期")

        paid_before = reminder_svc.paid_map_for_lease(
            lease, exclude_payment_id=exclude_payment_id
        )
        room_total = 0.0
        for period in covered:
            key = ReminderService._period_key(period)
            due = round(float(period.amount), 2)
            already = round(paid_before.get(key, 0.0), 2)
            room_total += max(0.0, due - already)
        room_total = round(room_total, 2)
        if amount > room_total + 0.009:
            raise ValidationError(
                f"所选连续应收期剩余应缴合计 ¥{room_total:.2f}，"
                f"当次缴纳不能超过该合计"
            )

        paid_after = reminder_svc.paid_map_for_lease(
            lease,
            exclude_payment_id=exclude_payment_id,
            extra_payment=candidate,
        )
        for period in covered:
            key = ReminderService._period_key(period)
            due = round(float(period.amount), 2)
            total = round(paid_after.get(key, 0.0), 2)
            if total > due + 0.009:
                already = round(paid_before.get(key, 0.0), 2)
                remaining = round(max(0.0, due - already), 2)
                raise ValidationError(
                    f"应收期 {period.period_start} ~ {period.period_end} "
                    f"应缴 ¥{due:.2f}，已缴 ¥{already:.2f}，"
                    f"本次最多可缴 ¥{remaining:.2f}"
                )

    def create(
        self,
        lease_id: int,
        period_start: date,
        period_end: date,
        amount: float,
        paid_at: date,
        note: str = "",
        fee_type: str = FEE_TYPE_RENT,
    ) -> int:
        fee = self._validate(
            lease_id,
            period_start,
            period_end,
            amount,
            fee_type=fee_type,
        )
        if fee == FEE_TYPE_DEPOSIT:
            # 押金不参与应收期；起止存实缴日占位
            period_start = paid_at
            period_end = paid_at
        return self.repo.create(
            lease_id,
            period_start,
            period_end,
            amount,
            paid_at,
            note.strip(),
            fee_type=fee,
        )

    def update(
        self,
        payment_id: int,
        period_start: date,
        period_end: date,
        amount: float,
        paid_at: date,
        note: str,
        fee_type: str | None = None,
    ) -> None:
        payment = self.repo.get(payment_id)
        if not payment:
            raise ValidationError("缴费记录不存在")
        fee = normalize_fee_type(
            fee_type if fee_type is not None else payment.fee_type
        )
        fee = self._validate(
            payment.lease_id,
            period_start,
            period_end,
            amount,
            exclude_payment_id=payment_id,
            fee_type=fee,
        )
        if fee == FEE_TYPE_DEPOSIT:
            period_start = paid_at
            period_end = paid_at
        self.repo.update(
            payment_id,
            period_start,
            period_end,
            amount,
            paid_at,
            note.strip(),
            fee_type=fee,
        )

    def delete(self, payment_id: int) -> None:
        self.repo.delete(payment_id)


class ReminderService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.lease_repo = LeaseRepository(db)
        self.payment_repo = PaymentRepository(db)
        self.settings_repo = SettingsRepository(db)

    def generate_rent_periods(self, lease: Lease) -> list[RentPeriod]:
        periods: list[RentPeriod] = []
        step = lease.period_months
        free_ranges = [(p.start_date, p.end_date) for p in (lease.free_periods or [])]
        cursor = lease.start_date
        index = 0
        while cursor <= lease.end_date:
            next_start = _add_months(lease.start_date, (index + 1) * step)
            period_end = min(next_start - timedelta(days=1), lease.end_date)
            if period_end < cursor:
                break

            fully_free = _range_fully_covered(cursor, period_end, free_ranges)
            partial_free = (not fully_free) and _range_any_overlap(
                cursor, period_end, free_ranges
            )
            amount = self._period_amount(lease, cursor, period_end, free_ranges)
            periods.append(
                RentPeriod(
                    lease_id=lease.id,
                    period_start=cursor,
                    period_end=period_end,
                    amount=amount,
                    fully_free=fully_free,
                    partial_free=partial_free,
                )
            )
            index += 1
            cursor = next_start
        return periods

    def _period_amount(
        self,
        lease: Lease,
        period_start: date,
        period_end: date,
        free_ranges: list[tuple[date, date]],
        *,
        apply_discounts: bool = True,
    ) -> float:
        """按月切片累计应收（非完整月按天比例）。"""
        _free, _disc, net = self._period_charge_totals(
            lease,
            period_start,
            period_end,
            free_ranges,
            apply_discounts=apply_discounts,
        )
        return net

    def period_gross_amount(self, lease: Lease, period: RentPeriod) -> float:
        """应收期应缴（未含免租与折/减；非完整月按天比例）。"""
        return self._period_amount(
            lease,
            period.period_start,
            period.period_end,
            [],
            apply_discounts=False,
        )

    def period_free_amount(self, lease: Lease, period: RentPeriod) -> float:
        """免租费用：整段免租按应缴基数；部分免租按折后金额×免租天数比例。"""
        free_ranges = [(p.start_date, p.end_date) for p in (lease.free_periods or [])]
        free_amt, _disc, _net = self._period_charge_totals(
            lease,
            period.period_start,
            period.period_end,
            free_ranges,
            apply_discounts=True,
        )
        return free_amt

    def period_discount_amount(self, lease: Lease, period: RentPeriod) -> float:
        """折/减合计：按当月应缴基数计算；整段免租的月份不计折/减。"""
        free_ranges = [(p.start_date, p.end_date) for p in (lease.free_periods or [])]
        _free, disc_amt, _net = self._period_charge_totals(
            lease,
            period.period_start,
            period.period_end,
            free_ranges,
            apply_discounts=True,
        )
        return disc_amt

    def _period_charge_totals(
        self,
        lease: Lease,
        period_start: date,
        period_end: date,
        free_ranges: list[tuple[date, date]],
        *,
        apply_discounts: bool,
    ) -> tuple[float, float, float]:
        """累计某应收期内的（免租, 折/减, 净应收）。"""
        if period_end < period_start:
            return 0.0, 0.0, 0.0
        if free_ranges and _range_fully_covered(period_start, period_end, free_ranges):
            _f, _d, gross = self._period_charge_totals(
                lease,
                period_start,
                period_end,
                [],
                apply_discounts=False,
            )
            return round(gross, 2), 0.0, 0.0

        discounts = list(lease.discounts or []) if apply_discounts else []
        free_total = 0.0
        disc_total = 0.0
        net_total = 0.0
        month_idx = 0
        while True:
            slice_start = _add_months(lease.start_date, month_idx)
            if slice_start > period_end:
                break
            full_end = _add_months(lease.start_date, month_idx + 1) - timedelta(days=1)
            lease_slice_end = min(full_end, lease.end_date)
            overlap_start = max(slice_start, period_start)
            overlap_end = min(lease_slice_end, period_end)
            if overlap_end >= overlap_start:
                full_days = (full_end - slice_start).days + 1
                free_amt, disc_amt, net = self._month_charge_components(
                    lease.monthly_rent,
                    overlap_start,
                    overlap_end,
                    slice_start,
                    full_days,
                    free_ranges,
                    discounts,
                )
                free_total += free_amt
                disc_total += disc_amt
                net_total += net
            month_idx += 1
            if month_idx > 600:
                break
        return (
            round(max(0.0, free_total), 2),
            round(max(0.0, disc_total), 2),
            round(max(0.0, net_total), 2),
        )

    @classmethod
    def _month_charge_components(
        cls,
        monthly_rent: float,
        charge_start: date,
        charge_end: date,
        full_month_start: date,
        full_month_days: int,
        free_ranges: list[tuple[date, date]],
        discounts: list[LeaseDiscount],
    ) -> tuple[float, float, float]:
        """单月计费（免租额, 折/减额, 净应收）。

        非完整月：应缴基数 = 月租 × 计费天数 ÷ 完整租赁月天数。
        - 计费区间全免租：免租=应缴基数，折/减=0，净=0
        - 否则先按应缴基数套折/减，再对折后金额按免租天数比例计免租与净应收
        """
        charge_days = (charge_end - charge_start).days + 1
        if charge_days <= 0 or full_month_days <= 0 or monthly_rent <= 0:
            return 0.0, 0.0, 0.0
        rent = round(float(monthly_rent) * charge_days / full_month_days, 2)
        if rent <= 0:
            return 0.0, 0.0, 0.0

        free_days = min(
            charge_days, _covered_days(charge_start, charge_end, free_ranges)
        )
        if free_days >= charge_days:
            return rent, 0.0, 0.0

        match_end = full_month_start + timedelta(days=full_month_days - 1)
        after_disc = cls._apply_month_discount(
            rent, full_month_start, match_end, discounts
        )
        disc_amt = round(max(0.0, rent - after_disc), 2)
        payable_ratio = (charge_days - free_days) / charge_days
        free_amt = round(after_disc * (1.0 - payable_ratio), 2)
        net = round(after_disc * payable_ratio, 2)
        drift = round(rent - free_amt - disc_amt - net, 2)
        if drift:
            net = round(net + drift, 2)
        return free_amt, disc_amt, net

    @staticmethod
    def _apply_month_discount(
        monthly_rent: float,
        slice_start: date,
        slice_end: date,
        discounts: list[LeaseDiscount],
    ) -> float:
        """对应缴基数套用折/减，返回折后金额（立减封顶为基数）。"""
        base = float(monthly_rent)
        for item in discounts:
            if not _overlaps(slice_start, slice_end, item.start_date, item.end_date):
                continue
            if item.kind == DISCOUNT_KIND_RATE:
                return round(base * float(item.value), 2)
            if item.kind == DISCOUNT_KIND_AMOUNT:
                return round(max(0.0, base - float(item.value)), 2)
            break
        return round(base, 2)

    @staticmethod
    def _month_amount_with_discount(
        base_amount: float,
        slice_start: date,
        slice_end: date,
        discounts: list[LeaseDiscount],
    ) -> float:
        """兼容旧调用：对给定基数套用折/减。"""
        base = float(base_amount)
        for item in discounts:
            if not _overlaps(slice_start, slice_end, item.start_date, item.end_date):
                continue
            if item.kind == DISCOUNT_KIND_RATE:
                return round(base * float(item.value), 2)
            if item.kind == DISCOUNT_KIND_AMOUNT:
                return round(max(0.0, base - float(item.value)), 2)
            break
        return round(base, 2)

    @staticmethod
    def _period_key(period: RentPeriod) -> tuple[date, date]:
        return (period.period_start, period.period_end)

    @staticmethod
    def _payment_overlaps_period(payment: Payment, period: RentPeriod) -> bool:
        return not (
            payment.period_end < period.period_start
            or payment.period_start > period.period_end
        )

    def _chargeable_periods(self, lease: Lease) -> list[RentPeriod]:
        return [
            period
            for period in self.generate_rent_periods(lease)
            if (not period.fully_free) and float(period.amount) > 0
        ]

    def _allocate_payments_fifo(
        self,
        lease: Lease,
        payments: Sequence[Payment],
    ) -> dict[tuple[date, date], float]:
        """按应收期时间顺序，将各笔缴费依次填满覆盖区间内的应缴（一对多）。"""
        periods = self._chargeable_periods(lease)
        paid: dict[tuple[date, date], float] = {
            self._period_key(period): 0.0 for period in periods
        }
        if not periods:
            return paid
        ordered = sorted(payments, key=lambda pay: (pay.paid_at, pay.id))
        for payment in ordered:
            left = round(float(payment.amount), 2)
            if left <= 0:
                continue
            for period in periods:
                if left <= 0.009:
                    break
                if not self._payment_overlaps_period(payment, period):
                    continue
                key = self._period_key(period)
                room = round(float(period.amount) - paid[key], 2)
                if room <= 0.009:
                    continue
                take = round(min(left, room), 2)
                paid[key] = round(paid[key] + take, 2)
                left = round(left - take, 2)
        return paid

    def covered_rent_periods(
        self, lease: Lease, payment: Payment
    ) -> list[RentPeriod]:
        """返回一笔缴费覆盖的应收期（按时间顺序，用于列表分行展示）。"""
        return [
            period
            for period in self._chargeable_periods(lease)
            if self._payment_overlaps_period(payment, period)
        ]

    def paid_map_for_lease(
        self,
        lease: Lease,
        *,
        exclude_payment_id: Optional[int] = None,
        extra_payment: Optional[Payment] = None,
    ) -> dict[tuple[date, date], float]:
        payments = [
            pay
            for pay in self.payment_repo.list_by_lease(lease.id, fee_type=FEE_TYPE_RENT)
            if exclude_payment_id is None or pay.id != exclude_payment_id
        ]
        if extra_payment is not None:
            if normalize_fee_type(extra_payment.fee_type) == FEE_TYPE_RENT:
                payments.append(extra_payment)
        return self._allocate_payments_fifo(lease, payments)

    @staticmethod
    def _payment_credit_for_period(payment: Payment, period: RentPeriod) -> float:
        """单笔缴费在不与其他缴费竞争时，按 FIFO 分摊到指定应收期的金额。

        多笔累计请使用 paid_map_for_lease / paid_amount_for_period。
        """
        if not ReminderService._payment_overlaps_period(payment, period):
            return 0.0
        # 仅覆盖单期时整笔计入；跨多期时需结合租约全部应收期顺序，由 paid_map 处理
        if (
            payment.period_start == period.period_start
            and payment.period_end == period.period_end
        ):
            return round(min(float(payment.amount), float(period.amount)), 2)
        pay_days = (payment.period_end - payment.period_start).days + 1
        if pay_days <= 0:
            return 0.0
        overlap_start = max(payment.period_start, period.period_start)
        overlap_end = min(payment.period_end, period.period_end)
        overlap_days = (overlap_end - overlap_start).days + 1
        if overlap_days <= 0:
            return 0.0
        # 兼容旧数据中非完整期对齐的跨段记录：仍按重叠天数估算
        return round(float(payment.amount) * overlap_days / pay_days, 2)

    def paid_amount_for_period(
        self,
        lease_id: int,
        period: RentPeriod,
        *,
        exclude_payment_id: Optional[int] = None,
    ) -> float:
        lease = self.lease_repo.get(lease_id)
        if not lease:
            return 0.0
        paid_map = self.paid_map_for_lease(
            lease, exclude_payment_id=exclude_payment_id
        )
        return round(paid_map.get(self._period_key(period), 0.0), 2)

    def remaining_amount_for_period(
        self,
        lease_id: int,
        period: RentPeriod,
        *,
        exclude_payment_id: Optional[int] = None,
    ) -> float:
        remaining = float(period.amount) - self.paid_amount_for_period(
            lease_id, period, exclude_payment_id=exclude_payment_id
        )
        return round(max(0.0, remaining), 2)

    def is_period_paid(self, lease_id: int, period: RentPeriod) -> bool:
        return self.remaining_amount_for_period(lease_id, period) <= 0.0

    def unpaid_periods(self, lease: Lease, today: Optional[date] = None) -> list[RentPeriod]:
        today = today or date.today()
        result: list[RentPeriod] = []
        for period in self.generate_rent_periods(lease):
            if period.fully_free or period.amount <= 0:
                continue
            # 只关心已进入提醒窗口或已开始的周期（避免列出遥远未来）
            if period.period_start > today + timedelta(days=366):
                continue
            remaining = self.remaining_amount_for_period(lease.id, period)
            if remaining <= 0:
                continue
            if remaining < float(period.amount):
                result.append(replace(period, amount=remaining))
            else:
                result.append(period)
        return result

    def list_reminders(self, today: Optional[date] = None) -> list[ReminderItem]:
        today = today or date.today()
        reminders: list[ReminderItem] = []
        leases = self.lease_repo.list_all(status="生效")
        settings = self.settings_repo.get_settings()
        expire_days = settings.lease_expire_remind_days
        rent_days = settings.rent_due_remind_days

        for lease in leases:
            project_name = lease.project_name

            # 合同到期提醒（将到期：进入提前提醒窗口，含到期当天）
            days_to_end = (lease.end_date - today).days
            if 0 <= days_to_end <= expire_days:
                reminders.append(
                    ReminderItem(
                        kind=REMINDER_KIND_CONTRACT_EXPIRED,
                        project_id=lease.project_id,
                        project_name=project_name,
                        room_id=lease.room_id,
                        room_no=lease.room_no,
                        lease_id=lease.id,
                        period_start=lease.start_date,
                        period_end=lease.end_date,
                        amount=lease.monthly_rent,
                        days_delta=days_to_end,
                        detail=f"合同将于 {lease.end_date.isoformat()} 到期",
                        tenant=lease.tenant or "",
                    )
                )

            # 按租赁缴费周期生成租金应收 / 逾期提醒
            for period in self.unpaid_periods(lease, today):
                remind_from = period.period_start - timedelta(days=rent_days)
                if today < remind_from:
                    continue
                days_delta = (period.period_start - today).days
                due = self.period_gross_amount(lease, period)
                free = self.period_free_amount(lease, period)
                discount = self.period_discount_amount(lease, period)
                paid = self.paid_amount_for_period(lease.id, period)
                remaining = round(max(0.0, due - paid - free - discount), 2)
                partial_note = (
                    "（已部分缴费）" if paid > 0.009 and remaining > 0 else ""
                )
                if days_delta < 0:
                    kind = REMINDER_KIND_RENT_OVERDUE
                    detail = (
                        f"应收期 {period.period_start} ~ {period.period_end} "
                        f"已逾期 {abs(days_delta)} 天{partial_note}"
                    )
                else:
                    kind = REMINDER_KIND_RENT_DUE
                    detail = (
                        f"应收期 {period.period_start} ~ {period.period_end}"
                        + ("（含免租区间）" if period.partial_free else "")
                        + partial_note
                    )
                reminders.append(
                    ReminderItem(
                        kind=kind,
                        project_id=lease.project_id,
                        project_name=project_name,
                        room_id=lease.room_id,
                        room_no=lease.room_no,
                        lease_id=lease.id,
                        period_start=period.period_start,
                        period_end=period.period_end,
                        amount=remaining,
                        days_delta=days_delta,
                        detail=detail,
                        tenant=lease.tenant or "",
                        due_amount=due,
                        paid_amount=paid,
                        discount_amount=discount,
                        free_amount=free,
                    )
                )

            # 押金应收提醒（约定押金 > 0 且仍有剩余）
            if float(lease.deposit) > 0.009:
                deposit_paid = round(
                    sum(
                        float(pay.amount)
                        for pay in self.payment_repo.list_by_lease(
                            lease.id, fee_type=FEE_TYPE_DEPOSIT
                        )
                    ),
                    2,
                )
                deposit_remaining = round(
                    max(0.0, float(lease.deposit) - deposit_paid), 2
                )
                if deposit_remaining > 0.009:
                    days_delta = (lease.start_date - today).days
                    partial = (
                        "（已部分缴纳）"
                        if deposit_paid > 0.009
                        else ""
                    )
                    reminders.append(
                        ReminderItem(
                            kind=REMINDER_KIND_DEPOSIT,
                            project_id=lease.project_id,
                            project_name=project_name,
                            room_id=lease.room_id,
                            room_no=lease.room_no,
                            lease_id=lease.id,
                            period_start=lease.start_date,
                            period_end=lease.end_date,
                            amount=deposit_remaining,
                            days_delta=days_delta,
                            detail=(
                                f"约定押金 ¥{float(lease.deposit):.2f}，"
                                f"已收 ¥{deposit_paid:.2f}，"
                                f"剩余 ¥{deposit_remaining:.2f}{partial}"
                            ),
                            tenant=lease.tenant or "",
                            due_amount=round(float(lease.deposit), 2),
                            paid_amount=deposit_paid,
                        )
                    )

        reminders.sort(
            key=lambda r: (
                REMINDER_KIND_RANK.get(r.kind, 9),
                r.days_delta,
                r.project_name,
            )
        )
        return reminders


class LicenseService:
    """离线授权校验与导入。"""

    def check(self, today: Optional[date] = None) -> LicenseInfo:
        return check_license_file(today=today)

    def import_file(self, source: Path) -> LicenseInfo:
        return import_license_file(Path(source))

    def remove(self) -> LicenseInfo:
        return remove_license_file()

    @property
    def allow_business(self) -> bool:
        return self.check().allow_business


class DingTalkPushService:
    """提醒看板 → 钉钉机器人推送。"""

    def __init__(self, services: "AppServices") -> None:
        self.services = services

    def push_reminders(self, *, force: bool = False) -> str:
        """推送当前提醒看板。返回结果说明文案。

        force=True 时忽略「每天一次」与推送时刻限制（用于手动测试）。
        """
        if not self.services.can_use_business:
            raise ValidationError("请先完成授权并配置数据存储后再推送")
        if self.services.reminders is None:
            raise ValidationError("提醒服务未就绪")
        settings = self.services.settings.get()
        if not force and not settings.dingtalk_enabled:
            raise ValidationError("请先在通用配置中启用钉钉推送")
        webhook = settings.dingtalk_webhook.strip()
        if not webhook:
            raise ValidationError("请先填写钉钉 Webhook 地址")
        secret = settings.dingtalk_secret.strip()
        if not secret:
            raise ValidationError("请先填写加签密钥")

        items = self.services.reminders.list_reminders()
        title, text = format_reminders_markdown(
            items,
            app_name=settings.app_name or "本地记账",
        )
        try:
            send_markdown(
                webhook,
                title,
                text,
                secret=secret,
            )
        except ValueError as exc:
            self.services.settings.mark_dingtalk_error(str(exc))
            raise
        self.services.settings.mark_dingtalk_pushed()
        return f"已推送 {len(items)} 条提醒到钉钉"

    def maybe_auto_push(self) -> Optional[str]:
        """到达设定时刻且今日未成功推送时自动推送；不满足条件返回 None。

        到点推送失败时不记成功时间，恢复后可补推一次。
        """
        if not self.services.can_use_business or self.services.reminders is None:
            return None
        settings = self.services.settings.get()
        now = datetime.now()
        try:
            due = should_push_today(
                enabled=settings.dingtalk_enabled,
                push_time=settings.dingtalk_push_time,
                last_push_date=settings.dingtalk_last_push_date,
                now_date=now.date(),
                now_hour=now.hour,
                now_minute=now.minute,
            )
        except ValueError as exc:
            self.services.settings.mark_dingtalk_error(str(exc))
            return None
        if not due:
            return None
        try:
            return self.push_reminders(force=False)
        except ValidationError as exc:
            self.services.settings.mark_dingtalk_error(str(exc))
            return None
        except ValueError:
            # 已在 push_reminders 写入 last_error；不标记成功，稍后重试
            return None


class AppServices:
    def __init__(
        self, bootstrap: BootstrapConfig, db: Database | None = None
    ) -> None:
        self.bootstrap = bootstrap
        self.db = db
        self.settings = SettingsService(bootstrap, db)
        self.license = LicenseService()
        self.dingtalk = DingTalkPushService(self)
        self.projects: ProjectService | None = None
        self.rooms: RoomService | None = None
        self.leases: LeaseService | None = None
        self.payments: PaymentService | None = None
        self.reminders: ReminderService | None = None
        if db is not None:
            self._init_domain_services(db)

    @property
    def is_ready(self) -> bool:
        return self.db is not None and self.bootstrap.is_storage_configured()

    @property
    def can_use_business(self) -> bool:
        """存储已就绪且授权处于有效或宽限期。"""
        return self.is_ready and self.license.allow_business

    def attach_database(self, db: Database) -> None:
        self.db = db
        self.settings.attach_db(db)
        self._init_domain_services(db)

    def _init_domain_services(self, db: Database) -> None:
        self.projects = ProjectService(db)
        self.rooms = RoomService(db)
        self.leases = LeaseService(db)
        self.payments = PaymentService(db)
        self.reminders = ReminderService(db)

    def require_ready(self) -> None:
        if not self.is_ready:
            raise ValidationError("请先在「通用配置」中设置数据存储位置")
        if not self.license.allow_business:
            info = self.license.check()
            raise ValidationError(info.message)
