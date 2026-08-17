from __future__ import annotations

from datetime import date, datetime

import customtkinter as ctk

from app.models import FEE_TYPE_DEPOSIT, FEE_TYPE_RENT
from app.services import AppServices, ValidationError
from app.ui.utils import (
    ask_save_filename,
    ask_yes_no,
    format_date_range,
    format_decimal,
    format_money,
    format_remaining_due_formula,
    parse_date,
    parse_float,
    show_error,
    show_info,
    today_str,
    write_xlsx,
)
from app.ui.widgets import DataTable, DatePickerField, DateRangeField, DecimalEntry, FormDialog


class PaymentFormDialog(FormDialog):
    def __init__(
        self,
        master,
        title: str,
        services: AppServices,
        leases: list[tuple[int, str]],
        initial: dict | None = None,
        lock_lease: bool = False,
        exclude_payment_id: int | None = None,
    ) -> None:
        super().__init__(master, title, width=760, height=520)
        self.services = services
        self.leases = leases
        self.exclude_payment_id = exclude_payment_id
        self._due_amount = 0.0  # 所选连续周期剩余应缴合计（当次缴纳上限）
        self._period_start: date | None = None
        self._period_end: date | None = None
        initial = initial or {}

        lease_labels = [label for _, label in leases]
        self.lease_var = ctk.StringVar(
            value=initial.get("lease_label") or (lease_labels[0] if lease_labels else "")
        )
        # label, start, end, due, paid, free, discount, remaining
        self.period_choices: list[
            tuple[str, date, date, float, float, float, float, float]
        ] = []
        self.start_period_var = ctk.StringVar(value="")
        self.end_period_var = ctk.StringVar(value="")
        self.term_var = ctk.StringVar(value="—")
        self._summary_prefix_var = ctk.StringVar(value="剩余应缴")
        self._summary_remaining_var = ctk.StringVar(value="(—)")
        self._summary_suffix_var = ctk.StringVar(value="")
        init_amount = initial.get("amount", "")
        self.amount_var = ctk.StringVar(value=format_decimal(init_amount))
        self.paid_at_var = ctk.StringVar(value=initial.get("paid_at", today_str()))
        self.note_var = ctk.StringVar(value=initial.get("note", ""))
        self._keep_initial_amount = bool(
            format_decimal(init_amount) and exclude_payment_id is not None
        )
        self._initial_start = (initial.get("period_start") or "").strip()
        self._initial_end = (initial.get("period_end") or "").strip()

        lease_widget = ctk.CTkOptionMenu(
            self.body,
            values=lease_labels or ["无租赁"],
            variable=self.lease_var,
            command=lambda _v: self._reload_periods(),
        )
        if lock_lease or not lease_labels:
            lease_widget.configure(state="disabled")
        self.add_field(0, "租赁合同", lease_widget)

        self.start_period_menu = ctk.CTkOptionMenu(
            self.body,
            values=["暂无未缴应收期"],
            variable=self.start_period_var,
            command=self._on_start_period_selected,
        )
        self.add_field(1, "起始应收期", self.start_period_menu)

        self.end_period_menu = ctk.CTkOptionMenu(
            self.body,
            values=["暂无未缴应收期"],
            variable=self.end_period_var,
            command=self._on_end_period_selected,
        )
        self.add_field(2, "结束应收期", self.end_period_menu)

        readonly_color = "#374151"
        self.term_label = ctk.CTkLabel(
            self.body, textvariable=self.term_var, anchor="w", text_color=readonly_color
        )
        summary_box = ctk.CTkFrame(self.body, fg_color="transparent")
        self._summary_prefix_label = ctk.CTkLabel(
            summary_box,
            textvariable=self._summary_prefix_var,
            anchor="w",
            text_color=readonly_color,
        )
        self._summary_remaining_label = ctk.CTkLabel(
            summary_box,
            textvariable=self._summary_remaining_var,
            anchor="w",
            text_color=readonly_color,
        )
        self._summary_suffix_label = ctk.CTkLabel(
            summary_box,
            textvariable=self._summary_suffix_var,
            anchor="w",
            text_color=readonly_color,
            justify="left",
        )
        self._summary_prefix_label.pack(side="left")
        self._summary_remaining_label.pack(side="left")
        self._summary_suffix_label.pack(side="left", fill="x", expand=True)
        self.add_field(3, "缴费起止", self.term_label)
        self.add_field(4, "费用明细", summary_box)
        self.add_field(5, "当次缴纳", DecimalEntry(self.body, textvariable=self.amount_var))
        self.paid_at_picker = DatePickerField(self.body, textvariable=self.paid_at_var)
        self.add_field(6, "实缴日期", self.paid_at_picker)
        self.add_field(7, "备注", ctk.CTkEntry(self.body, textvariable=self.note_var))

        self.after(80, self._reload_periods)

    @staticmethod
    def _format_amount_summary(
        due: float, paid: float, free: float, discount: float, remaining: float
    ) -> str:
        return format_remaining_due_formula(
            remaining, due, paid, free, discount, with_prefix=True
        )

    def _set_amount_summary(
        self,
        due: float,
        paid: float,
        free: float,
        discount: float,
        remaining: float,
        *,
        period_count: int = 1,
    ) -> None:
        due = round(float(due), 2)
        paid = round(float(paid), 2)
        free = round(float(free), 2)
        discount = round(float(discount), 2)
        remaining = round(float(remaining), 2)
        prefix = "剩余应缴" if period_count <= 1 else f"剩余应缴合计({period_count}期)"
        self._summary_prefix_var.set(prefix)
        self._summary_remaining_var.set(f"({remaining:.2f})")
        suffix = format_remaining_due_formula(
            remaining, due, paid, free, discount, with_prefix=False
        )
        # with_prefix=False 形如 "(x.xx)=应缴..."; 金额已单独标红，这里只保留公式尾部
        amount_token = f"({remaining:.2f})"
        self._summary_suffix_var.set(
            suffix[len(amount_token) :] if suffix.startswith(amount_token) else suffix
        )
        self._summary_remaining_label.configure(
            text_color="#b91c1c" if remaining > 0 else "#374151"
        )

    def _current_lease_id(self) -> int | None:
        if not self.leases:
            return None
        label = self.lease_var.get()
        return next((lid for lid, llabel in self.leases if llabel == label), None)

    def _current_lease(self):
        lease_id = self._current_lease_id()
        if lease_id is None or self.services.leases is None:
            return None
        return self.services.leases.get(lease_id)

    def _choice_by_label(self, label: str):
        for item in self.period_choices:
            if item[0] == label:
                return item
        return None

    def _index_of_choice(self, label: str) -> int:
        for idx, item in enumerate(self.period_choices):
            if item[0] == label:
                return idx
        return -1

    def _apply_range(
        self,
        start_idx: int,
        end_idx: int,
        *,
        sync_amount: bool = True,
    ) -> None:
        if start_idx < 0 or end_idx < start_idx or end_idx >= len(self.period_choices):
            self._clear_period()
            return
        selected = self.period_choices[start_idx : end_idx + 1]
        start = selected[0][1]
        end = selected[-1][2]
        due = round(sum(item[3] for item in selected), 2)
        paid = round(sum(item[4] for item in selected), 2)
        free = round(sum(item[5] for item in selected), 2)
        discount = round(sum(item[6] for item in selected), 2)
        remaining = round(sum(item[7] for item in selected), 2)
        self._period_start = start
        self._period_end = end
        self._due_amount = remaining
        count = len(selected)
        if count == 1:
            self.term_var.set(format_date_range(start, end))
        else:
            self.term_var.set(
                f"{format_date_range(start, end)}（连续 {count} 个应收期）"
            )
        self._set_amount_summary(
            due, paid, free, discount, remaining, period_count=count
        )
        if sync_amount:
            if self._keep_initial_amount:
                self._keep_initial_amount = False
            else:
                self.amount_var.set(
                    f"{self._due_amount:.2f}" if self._due_amount > 0 else ""
                )

    def _clear_period(self) -> None:
        self._period_start = None
        self._period_end = None
        self._due_amount = 0.0
        self.term_var.set("—")
        self._summary_prefix_var.set("")
        self._summary_remaining_var.set("(—)")
        self._summary_suffix_var.set("")
        self._summary_remaining_label.configure(text_color="#374151")
        if not self._keep_initial_amount:
            self.amount_var.set("")

    def _refresh_end_menu(self, start_idx: int, preferred_end_idx: int | None = None) -> None:
        if start_idx < 0 or start_idx >= len(self.period_choices):
            self.end_period_menu.configure(
                state="disabled", values=["暂无未缴应收期"]
            )
            self.end_period_var.set("暂无未缴应收期")
            return
        end_choices = [item[0] for item in self.period_choices[start_idx:]]
        self.end_period_menu.configure(state="normal", values=end_choices)
        if preferred_end_idx is not None and preferred_end_idx >= start_idx:
            end_idx = preferred_end_idx
        else:
            current = self.end_period_var.get()
            end_idx = self._index_of_choice(current)
            if end_idx < start_idx:
                end_idx = start_idx
        self.end_period_var.set(self.period_choices[end_idx][0])

    def _reload_periods(self) -> None:
        lease_id = self._current_lease_id()
        choices: list[str] = []
        self.period_choices = []
        if lease_id is not None and self.services.leases and self.services.reminders:
            lease = self.services.leases.get(lease_id)
            if lease:
                for period in self.services.reminders.generate_rent_periods(lease):
                    if period.fully_free or period.amount <= 0:
                        continue
                    due = self.services.reminders.period_gross_amount(lease, period)
                    free = self.services.reminders.period_free_amount(lease, period)
                    discount = self.services.reminders.period_discount_amount(
                        lease, period
                    )
                    paid = self.services.reminders.paid_amount_for_period(
                        lease.id,
                        period,
                        exclude_payment_id=self.exclude_payment_id,
                    )
                    remaining = round(max(0.0, due - paid - free - discount), 2)
                    if remaining <= 0:
                        continue
                    summary = self._format_amount_summary(
                        due, paid, free, discount, remaining
                    )
                    label = (
                        f"{format_date_range(period.period_start, period.period_end)}"
                        f"　{summary}"
                    )
                    choices.append(label)
                    self.period_choices.append(
                        (
                            label,
                            period.period_start,
                            period.period_end,
                            due,
                            paid,
                            free,
                            discount,
                            remaining,
                        )
                    )

        if not choices:
            self.start_period_menu.configure(
                state="disabled", values=["暂无未缴应收期"]
            )
            self.end_period_menu.configure(
                state="disabled", values=["暂无未缴应收期"]
            )
            self.start_period_var.set("暂无未缴应收期")
            self.end_period_var.set("暂无未缴应收期")
            self._clear_period()
            return

        self.start_period_menu.configure(state="normal", values=choices)

        start_idx = 0
        end_idx = 0
        if self._initial_start and self._initial_end:
            try:
                start = date.fromisoformat(self._initial_start)
                end = date.fromisoformat(self._initial_end)
            except ValueError:
                start = end = None  # type: ignore[assignment]
            if start and end:
                for idx, item in enumerate(self.period_choices):
                    _label, p_start, p_end, *_rest = item
                    if p_start == start:
                        start_idx = idx
                    if p_end == end:
                        end_idx = idx
                if end_idx < start_idx:
                    end_idx = start_idx
            self._initial_start = ""
            self._initial_end = ""

        keep = self._keep_initial_amount
        self.start_period_var.set(self.period_choices[start_idx][0])
        self._refresh_end_menu(start_idx, preferred_end_idx=end_idx)
        self._apply_range(start_idx, self._index_of_choice(self.end_period_var.get()), sync_amount=not keep)
        if keep:
            self._keep_initial_amount = False

    def _on_start_period_selected(self, value: str) -> None:
        start_idx = self._index_of_choice(value)
        self._refresh_end_menu(start_idx)
        end_idx = self._index_of_choice(self.end_period_var.get())
        self._apply_range(start_idx, end_idx, sync_amount=True)

    def _on_end_period_selected(self, value: str) -> None:
        start_idx = self._index_of_choice(self.start_period_var.get())
        end_idx = self._index_of_choice(value)
        if end_idx < start_idx:
            end_idx = start_idx
            if 0 <= start_idx < len(self.period_choices):
                self.end_period_var.set(self.period_choices[start_idx][0])
        self._apply_range(start_idx, end_idx, sync_amount=True)

    def collect(self) -> dict:
        lease_id = self._current_lease_id()
        if lease_id is None:
            raise ValueError("请先创建生效中的租赁")
        if self._period_start is None or self._period_end is None:
            raise ValueError("请选择起止应收期")
        if self._due_amount <= 0:
            raise ValueError("所选周期剩余应缴为 0，无需缴费")
        start_idx = self._index_of_choice(self.start_period_var.get())
        end_idx = self._index_of_choice(self.end_period_var.get())
        if start_idx < 0 or end_idx < start_idx:
            raise ValueError("结束应收期不能早于起始应收期")
        amount = parse_float(self.amount_var.get(), "当次缴纳")
        if amount <= 0 or amount > self._due_amount + 0.009:
            raise ValueError(
                f"当次缴纳须大于 0 且不超过剩余应缴合计 ¥{self._due_amount:.2f}"
            )
        amount = min(amount, self._due_amount)
        return {
            "lease_id": lease_id,
            "fee_type": FEE_TYPE_RENT,
            "period_start": self._period_start,
            "period_end": self._period_end,
            "amount": round(amount, 2),
            "paid_at": parse_date(self.paid_at_var.get(), "实缴日期"),
            "note": self.note_var.get().strip(),
        }


class DepositFormDialog(FormDialog):
    """押金登记：不绑定应收期，金额不超过押金剩余应缴。"""

    def __init__(
        self,
        master,
        title: str,
        services: AppServices,
        leases: list[tuple[int, str]],
        initial: dict | None = None,
        lock_lease: bool = False,
        exclude_payment_id: int | None = None,
    ) -> None:
        super().__init__(master, title, width=560, height=420)
        self.services = services
        self.leases = leases
        self.exclude_payment_id = exclude_payment_id
        self._remaining = 0.0
        initial = initial or {}

        lease_labels = [label for _, label in leases]
        self.lease_var = ctk.StringVar(
            value=initial.get("lease_label") or (lease_labels[0] if lease_labels else "")
        )
        self.agreed_var = ctk.StringVar(value="—")
        self.paid_var = ctk.StringVar(value="—")
        self.remaining_var = ctk.StringVar(value="—")
        init_amount = initial.get("amount", "")
        self.amount_var = ctk.StringVar(value=format_decimal(init_amount))
        self.paid_at_var = ctk.StringVar(value=initial.get("paid_at", today_str()))
        self.note_var = ctk.StringVar(value=initial.get("note", ""))
        self._keep_initial_amount = bool(
            format_decimal(init_amount) and exclude_payment_id is not None
        )

        lease_widget = ctk.CTkOptionMenu(
            self.body,
            values=lease_labels or ["无租赁"],
            variable=self.lease_var,
            command=lambda _v: self._reload_summary(sync_amount=True),
        )
        if lock_lease or not lease_labels:
            lease_widget.configure(state="disabled")
        self.add_field(0, "租赁合同", lease_widget)

        readonly = "#374151"
        self.add_field(
            1,
            "约定押金",
            ctk.CTkLabel(
                self.body, textvariable=self.agreed_var, anchor="w", text_color=readonly
            ),
        )
        self.add_field(
            2,
            "已收押金",
            ctk.CTkLabel(
                self.body, textvariable=self.paid_var, anchor="w", text_color=readonly
            ),
        )
        self.remaining_label = ctk.CTkLabel(
            self.body, textvariable=self.remaining_var, anchor="w", text_color="#b91c1c"
        )
        self.add_field(3, "剩余未收", self.remaining_label)
        self.add_field(4, "当次缴纳", DecimalEntry(self.body, textvariable=self.amount_var))
        self.add_field(5, "实缴日期", DatePickerField(self.body, textvariable=self.paid_at_var))
        self.add_field(6, "备注", ctk.CTkEntry(self.body, textvariable=self.note_var))
        self._reload_summary(sync_amount=not self._keep_initial_amount)

    def _current_lease_id(self) -> int | None:
        label = self.lease_var.get()
        for lease_id, lease_label in self.leases:
            if lease_label == label:
                return lease_id
        return None

    def _reload_summary(self, sync_amount: bool = False) -> None:
        lease_id = self._current_lease_id()
        if lease_id is None or self.services.payments is None or self.services.leases is None:
            self.agreed_var.set("—")
            self.paid_var.set("—")
            self.remaining_var.set("—")
            self._remaining = 0.0
            return
        lease = self.services.leases.get(lease_id)
        if not lease:
            return
        paid = self.services.payments.deposit_paid(
            lease_id, exclude_payment_id=self.exclude_payment_id
        )
        remaining = self.services.payments.deposit_remaining(
            lease_id, exclude_payment_id=self.exclude_payment_id
        )
        self._remaining = remaining
        self.agreed_var.set(format_money(lease.deposit))
        self.paid_var.set(format_money(paid))
        self.remaining_var.set(format_money(remaining))
        self.remaining_label.configure(
            text_color="#b91c1c" if remaining > 0 else "#374151"
        )
        if sync_amount:
            self.amount_var.set(f"{remaining:.2f}" if remaining > 0 else "")

    def collect(self) -> dict:
        lease_id = self._current_lease_id()
        if lease_id is None:
            raise ValueError("请先创建生效中的租赁")
        if self._remaining <= 0:
            raise ValueError("押金已收齐，无需再登记")
        amount = parse_float(self.amount_var.get(), "当次缴纳")
        if amount <= 0 or amount > self._remaining + 0.009:
            raise ValueError(
                f"当次缴纳须大于 0 且不超过剩余未收 ¥{self._remaining:.2f}"
            )
        amount = min(amount, self._remaining)
        paid_at = parse_date(self.paid_at_var.get(), "实缴日期")
        return {
            "lease_id": lease_id,
            "fee_type": FEE_TYPE_DEPOSIT,
            "period_start": paid_at,
            "period_end": paid_at,
            "amount": round(amount, 2),
            "paid_at": paid_at,
            "note": self.note_var.get().strip(),
        }


class PaymentsPage(ctk.CTkFrame):
    def __init__(self, master, services: AppServices, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.services = services
        self.fee_type = FEE_TYPE_RENT
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        # 仅中间空白列伸缩，避免压缩筛选/操作区
        header.grid_columnconfigure(1, weight=1)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_box, text="收费登记", font=ctk.CTkFont(size=22, weight="bold")
        ).pack(side="left")
        self.fee_tabs = ctk.CTkSegmentedButton(
            title_box,
            values=[FEE_TYPE_RENT, FEE_TYPE_DEPOSIT],
            command=self._on_fee_tab_changed,
            width=140,
        )
        self.fee_tabs.set(FEE_TYPE_RENT)
        self.fee_tabs.pack(side="left", padx=(12, 0))

        filter_box = ctk.CTkFrame(header, fg_color="transparent")
        filter_box.grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.project_var = ctk.StringVar(value="全部项目")
        self.project_menu = ctk.CTkOptionMenu(
            filter_box,
            values=["全部项目"],
            variable=self.project_var,
            command=self._on_project_filter_changed,
            width=88,
            dynamic_resizing=False,
        )
        self.project_menu.pack(side="left")
        self.room_var = ctk.StringVar(value="全部房间")
        self.room_menu = ctk.CTkOptionMenu(
            filter_box,
            values=["全部房间"],
            variable=self.room_var,
            command=lambda _v: self.refresh(),
            width=88,
            dynamic_resizing=False,
        )
        self.room_menu.pack(side="left", padx=(8, 0))
        self.paid_from_var = ctk.StringVar(value="")
        self.paid_to_var = ctk.StringVar(value="")
        DateRangeField(
            filter_box,
            startvariable=self.paid_from_var,
            endvariable=self.paid_to_var,
            start_placeholder="实缴开始",
            end_placeholder="实缴截止",
            entry_width=74,
            command=self.refresh,
        ).pack(side="left", padx=(8, 0))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=3, sticky="e", padx=(10, 8))
        self.create_btn = ctk.CTkButton(
            actions, text="登记租金", height=28, width=80, command=self.create_payment
        )
        self.create_btn.pack(side="left", padx=(0, 4))
        ctk.CTkButton(actions, text="编辑", height=28, width=56, command=self.edit_payment).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="导出", height=28, width=56, command=self.export_xlsx
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="删除", height=28, width=56, fg_color="#b91c1c", command=self.delete_payment
        ).pack(side="left", padx=(4, 0))

        self.table = DataTable(
            self,
            columns=[
                ("id", "ID", 48),
                ("project", "项目", 120),
                ("room", "房间", 70),
                ("tenant", "租户", 100),
                ("period", "缴费周期", 150),
                ("amount", "缴纳金额", 90),
                ("paid_at", "实缴日期", 90),
                ("registered_at", "更新时间", 130),
                ("note", "备注", 140),
            ],
            column_anchors={
                "id": "center",
                "project": "w",
                "room": "center",
                "tenant": "w",
                "period": "center",
                "amount": "e",
                "paid_at": "center",
                "registered_at": "center",
                "note": "w",
            },
            style_prefix="TallyPay",
            fit_content_columns=("period", "paid_at", "registered_at"),
        )
        self.table.grid(row=1, column=0, sticky="nsew")

    def _on_fee_tab_changed(self, value: str) -> None:
        self.fee_type = value if value in (FEE_TYPE_RENT, FEE_TYPE_DEPOSIT) else FEE_TYPE_RENT
        self.create_btn.configure(
            text="登记租金" if self.fee_type == FEE_TYPE_RENT else "登记押金"
        )
        self.refresh()

    @staticmethod
    def _format_registered_at(value: str) -> str:
        """展示更新时间，格式 yyyy-MM-dd HH:mm:ss。"""
        text = (value or "").strip().replace("T", " ")
        if not text:
            return "—"
        if len(text) >= 19:
            return text[:19]
        if len(text) == 16:
            return f"{text}:00"
        return text

    def _lease_options(self) -> list[tuple[int, str]]:
        if self.services.leases is None:
            return []
        leases = self.services.leases.list_all(status="生效")
        return [
            (
                lease.id,
                (
                    f"{lease.project_name} / {lease.room_no}"
                    + (f" / {lease.tenant}" if lease.tenant else "")
                    + f" ({lease.start_date}~{lease.end_date})"
                ),
            )
            for lease in leases
        ]

    def _selected_id(self) -> int | None:
        iid = self.table.selected_iid()
        return int(iid) if iid else None

    def _reload_project_filters(self) -> None:
        if self.services.projects is None:
            return
        projects = self.services.projects.list_all()
        current = self.project_var.get()
        values = ["全部项目"] + [p.name for p in projects]
        self.project_menu.configure(values=values)
        if current in values:
            self.project_var.set(current)
        else:
            self.project_var.set("全部项目")
        self._reload_room_filters(preserve_selection=True)

    def _reload_room_filters(self, preserve_selection: bool = True) -> None:
        if self.services.rooms is None:
            return
        project_id = self._current_project_id()
        if project_id is None:
            values = ["全部房间"]
        else:
            rooms = self.services.rooms.list_by_project(project_id)
            room_nos = sorted({r.room_no for r in rooms}, key=lambda x: (len(x), x))
            values = ["全部房间"] + room_nos
        current = self.room_var.get() if preserve_selection else "全部房间"
        self.room_menu.configure(values=values)
        if current in values:
            self.room_var.set(current)
        else:
            self.room_var.set("全部房间")

    def _on_project_filter_changed(self, _value: str) -> None:
        self._reload_room_filters(preserve_selection=False)
        self.refresh()

    def _current_project_id(self) -> int | None:
        if self.services.projects is None:
            return None
        name = self.project_var.get()
        if name == "全部项目":
            return None
        for p in self.services.projects.list_all():
            if p.name == name:
                return p.id
        return None

    def _current_room_no(self) -> str | None:
        room_no = self.room_var.get().strip()
        if not room_no or room_no == "全部房间":
            return None
        return room_no

    @staticmethod
    def _parse_filter_date(text: str) -> date | None:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def _paid_at_range(self) -> tuple[date | None, date | None]:
        return (
            self._parse_filter_date(self.paid_from_var.get()),
            self._parse_filter_date(self.paid_to_var.get()),
        )

    def _filtered_payments(self):
        payments = self.services.payments.list_all(  # type: ignore[union-attr]
            self._current_project_id(), fee_type=self.fee_type
        )
        room_no = self._current_room_no()
        if room_no is not None:
            payments = [p for p in payments if p.room_no == room_no]
        paid_from, paid_to = self._paid_at_range()
        if paid_from is not None and paid_to is not None and paid_from > paid_to:
            paid_from, paid_to = paid_to, paid_from
        if paid_from is not None:
            payments = [p for p in payments if p.paid_at >= paid_from]
        if paid_to is not None:
            payments = [p for p in payments if p.paid_at <= paid_to]
        return payments

    def _payment_period_label(self, payment) -> str:
        """多应收期时按行展示各周期；押金展示完整租期。"""
        if payment.fee_type == FEE_TYPE_DEPOSIT:
            if self.services.leases is None:
                return "—"
            lease = self.services.leases.get(payment.lease_id)
            if not lease:
                return "—"
            return format_date_range(lease.start_date, lease.end_date)
        if self.services.leases is None or self.services.reminders is None:
            return format_date_range(payment.period_start, payment.period_end)
        lease = self.services.leases.get(payment.lease_id)
        if not lease:
            return format_date_range(payment.period_start, payment.period_end)
        covered = self.services.reminders.covered_rent_periods(lease, payment)
        if not covered:
            return format_date_range(payment.period_start, payment.period_end)
        return "\n".join(
            format_date_range(period.period_start, period.period_end)
            for period in covered
        )

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.payments is None:
            return
        self._reload_project_filters()
        payments = self._filtered_payments()
        rows = []
        for p in payments:
            rows.append(
                (
                    p.id,
                    p.project_name,
                    p.room_no,
                    p.tenant or "",
                    self._payment_period_label(p),
                    format_money(p.amount),
                    p.paid_at.isoformat(),
                    self._format_registered_at(p.updated_at or p.created_at),
                    p.note,
                )
            )
        self.table.set_rows(rows, [str(p.id) for p in payments])

    def export_xlsx(self) -> None:
        if not self.services.is_ready or self.services.payments is None:
            return
        payments = self._filtered_payments()
        if not payments:
            show_info("当前没有可导出的缴费记录")
            return
        kind = "租金" if self.fee_type == FEE_TYPE_RENT else "押金"
        path = ask_save_filename(
            title=f"导出{kind}记录",
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
            initialfile=f"{kind}记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        )
        if not path:
            return
        try:
            write_xlsx(
                path,
                [
                    "ID",
                    "费用类型",
                    "项目",
                    "房间",
                    "租户",
                    "缴费起始",
                    "缴费结束",
                    "缴纳金额",
                    "实缴日期",
                    "更新时间",
                    "备注",
                ],
                [
                    [
                        p.id,
                        p.fee_type,
                        p.project_name,
                        p.room_no,
                        p.tenant or "",
                        p.period_start.isoformat(),
                        p.period_end.isoformat(),
                        round(p.amount, 2),
                        p.paid_at.isoformat(),
                        self._format_registered_at(p.updated_at or p.created_at),
                        p.note,
                    ]
                    for p in payments
                ],
                sheet_title=f"{kind}记录",
            )
            show_info(f"已导出 {len(payments)} 条记录")
        except OSError as exc:
            show_error(f"导出失败：{exc}")
        except Exception as exc:
            show_error(f"导出失败：{exc}")

    def create_payment(self) -> None:
        leases = self._lease_options()
        if not leases:
            show_info("请先创建生效中的租赁")
            return
        if self.fee_type == FEE_TYPE_DEPOSIT:
            data = DepositFormDialog(self, "登记押金", self.services, leases).show()
        else:
            data = PaymentFormDialog(self, "登记租金", self.services, leases).show()
        if not data:
            return
        try:
            self.services.payments.create(**data)  # type: ignore[union-attr]
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

    def edit_payment(self) -> None:
        payment_id = self._selected_id()
        if payment_id is None:
            show_info("请先选择一条缴费记录")
            return
        payment = self.services.payments.get(payment_id)  # type: ignore[union-attr]
        if not payment:
            show_error("缴费记录不存在")
            return
        lease = self.services.leases.get(payment.lease_id)  # type: ignore[union-attr]
        if not lease:
            show_error("关联租赁不存在")
            return
        leases = [
            (
                lease.id,
                (
                    f"{lease.project_name} / {lease.room_no}"
                    + (f" / {lease.tenant}" if lease.tenant else "")
                    + f" ({lease.start_date}~{lease.end_date})"
                ),
            )
        ]
        initial = {
            "lease_label": leases[0][1],
            "period_start": payment.period_start.isoformat(),
            "period_end": payment.period_end.isoformat(),
            "amount": payment.amount,
            "paid_at": payment.paid_at.isoformat(),
            "note": payment.note,
        }
        if payment.fee_type == FEE_TYPE_DEPOSIT:
            data = DepositFormDialog(
                self,
                "编辑押金",
                self.services,
                leases,
                initial,
                lock_lease=True,
                exclude_payment_id=payment_id,
            ).show()
        else:
            data = PaymentFormDialog(
                self,
                "编辑租金",
                self.services,
                leases,
                initial,
                lock_lease=True,
                exclude_payment_id=payment_id,
            ).show()
        if not data:
            return
        try:
            self.services.payments.update(  # type: ignore[union-attr]
                payment_id,
                period_start=data["period_start"],
                period_end=data["period_end"],
                amount=data["amount"],
                paid_at=data["paid_at"],
                note=data["note"],
                fee_type=data.get("fee_type"),
            )
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

    def delete_payment(self) -> None:
        payment_id = self._selected_id()
        if payment_id is None:
            show_info("请先选择一条缴费记录")
            return
        if not ask_yes_no("确认删除该缴费记录？"):
            return
        self.services.payments.delete(payment_id)  # type: ignore[union-attr]
        self.refresh()
