from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

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
    FreePeriod,
    Lease,
    PAYMENT_PERIOD_OPTIONS,
    Payment,
    Project,
    ReminderItem,
    RentPeriod,
    Room,
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
                free_start, free_end = item
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

    def _validate(
        self,
        room_id: int,
        deposit: float,
        monthly_rent: float,
        start_date: date,
        end_date: date,
        free_periods: list[tuple[date, date]] | list[FreePeriod],
        payment_period: str,
        exclude_id: Optional[int] = None,
    ) -> tuple[list[tuple[date, date]], str]:
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
        overlap = self.repo.find_overlap(room_id, start_date, end_date, exclude_id)
        if overlap:
            raise ValidationError(
                f"与已有生效合同时间重叠（{overlap.start_date} ~ {overlap.end_date}）"
            )
        return normalized, period

    def create(
        self,
        room_id: int,
        deposit: float,
        monthly_rent: float,
        start_date: date,
        end_date: date,
        free_periods: list[tuple[date, date]] | None = None,
        payment_period: str = DEFAULT_PAYMENT_PERIOD,
    ) -> int:
        normalized, period = self._validate(
            room_id,
            deposit,
            monthly_rent,
            start_date,
            end_date,
            free_periods or [],
            payment_period,
        )
        lease_id = self.repo.create(
            room_id,
            deposit,
            monthly_rent,
            start_date,
            end_date,
            normalized,
            period,
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
    ) -> None:
        lease = self.repo.get(lease_id)
        if not lease:
            raise ValidationError("租赁不存在")
        if status not in {"生效", "结束"}:
            raise ValidationError("租赁状态无效")
        periods = free_periods if free_periods is not None else (lease.free_periods or [])
        if status == "生效":
            normalized, period = self._validate(
                lease.room_id,
                deposit,
                monthly_rent,
                start_date,
                end_date,
                periods,
                payment_period,
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
        self.repo.update(
            lease_id,
            deposit,
            monthly_rent,
            start_date,
            end_date,
            normalized,
            status,
            period,
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
        self.repo = PaymentRepository(db)
        self.lease_repo = LeaseRepository(db)

    def list_all(self, project_id: Optional[int] = None) -> list[Payment]:
        return self.repo.list_all(project_id)

    def list_by_lease(self, lease_id: int) -> list[Payment]:
        return self.repo.list_by_lease(lease_id)

    def get(self, payment_id: int) -> Optional[Payment]:
        return self.repo.get(payment_id)

    def _validate(
        self,
        lease_id: int,
        period_start: date,
        period_end: date,
        amount: float,
    ) -> None:
        if amount <= 0:
            raise ValidationError("缴费金额必须大于 0")
        if period_end < period_start:
            raise ValidationError("缴费对应结束时间不能早于起始时间")
        lease = self.lease_repo.get(lease_id)
        if not lease:
            raise ValidationError("租赁不存在")
        if period_start < lease.start_date or period_end > lease.end_date:
            raise ValidationError("缴费周期必须落在租赁期内")

    def create(
        self,
        lease_id: int,
        period_start: date,
        period_end: date,
        amount: float,
        paid_at: date,
        note: str = "",
    ) -> int:
        self._validate(lease_id, period_start, period_end, amount)
        return self.repo.create(
            lease_id, period_start, period_end, amount, paid_at, note.strip()
        )

    def update(
        self,
        payment_id: int,
        period_start: date,
        period_end: date,
        amount: float,
        paid_at: date,
        note: str,
    ) -> None:
        payment = self.repo.get(payment_id)
        if not payment:
            raise ValidationError("缴费记录不存在")
        self._validate(payment.lease_id, period_start, period_end, amount)
        self.repo.update(
            payment_id, period_start, period_end, amount, paid_at, note.strip()
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
    ) -> float:
        """按月切片累计应收：整月免租不计，其余按月租金计。"""
        if _range_fully_covered(period_start, period_end, free_ranges):
            return 0.0
        total = 0.0
        month_idx = 0
        while True:
            slice_start = _add_months(lease.start_date, month_idx)
            if slice_start > period_end:
                break
            slice_end = min(
                _add_months(lease.start_date, month_idx + 1) - timedelta(days=1),
                lease.end_date,
            )
            overlap_start = max(slice_start, period_start)
            overlap_end = min(slice_end, period_end)
            if overlap_end >= overlap_start:
                if not _range_fully_covered(overlap_start, overlap_end, free_ranges):
                    total += float(lease.monthly_rent)
            month_idx += 1
            if month_idx > 600:
                break
        return round(total, 2)

    def is_period_paid(self, lease_id: int, period: RentPeriod) -> bool:
        payments = self.payment_repo.list_by_lease(lease_id)
        for pay in payments:
            if (
                pay.period_start == period.period_start
                and pay.period_end == period.period_end
            ):
                return True
            if _fully_covers(
                pay.period_start, pay.period_end, period.period_start, period.period_end
            ):
                return True
        return False

    def unpaid_periods(self, lease: Lease, today: Optional[date] = None) -> list[RentPeriod]:
        today = today or date.today()
        result: list[RentPeriod] = []
        for period in self.generate_rent_periods(lease):
            if period.fully_free or period.amount <= 0:
                continue
            # 只关心已进入提醒窗口或已开始的周期（避免列出遥远未来）
            if period.period_start > today + timedelta(days=366):
                continue
            if not self.is_period_paid(lease.id, period):
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

            # 合同到期提醒
            days_to_end = (lease.end_date - today).days
            if 0 <= days_to_end <= expire_days:
                reminders.append(
                    ReminderItem(
                        kind="合同即将到期",
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
                    )
                )
            elif days_to_end < 0:
                reminders.append(
                    ReminderItem(
                        kind="合同已到期",
                        project_id=lease.project_id,
                        project_name=project_name,
                        room_id=lease.room_id,
                        room_no=lease.room_no,
                        lease_id=lease.id,
                        period_start=lease.start_date,
                        period_end=lease.end_date,
                        amount=lease.monthly_rent,
                        days_delta=days_to_end,
                        detail=f"合同已于 {lease.end_date.isoformat()} 到期",
                    )
                )

            # 按租赁缴费周期生成应收提醒
            for period in self.unpaid_periods(lease, today):
                remind_from = period.period_start - timedelta(days=rent_days)
                if today < remind_from:
                    continue
                days_delta = (period.period_start - today).days
                if days_delta < 0:
                    kind = "已逾期"
                    detail = (
                        f"应收期 {period.period_start} ~ {period.period_end} "
                        f"已逾期 {abs(days_delta)} 天"
                    )
                else:
                    kind = "应收提醒"
                    detail = (
                        f"应收期 {period.period_start} ~ {period.period_end}"
                        + ("（含免租区间）" if period.partial_free else "")
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
                        amount=period.amount,
                        days_delta=days_delta,
                        detail=detail,
                    )
                )

        kind_order = {"已逾期": 0, "应收提醒": 1, "合同已到期": 2, "合同即将到期": 3}
        reminders.sort(key=lambda r: (kind_order.get(r.kind, 9), r.days_delta, r.project_name))
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
