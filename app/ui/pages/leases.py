from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime

import customtkinter as ctk

from app.models import (
    DEFAULT_PAYMENT_PERIOD,
    NAME_MAX_LENGTH,
    PAYMENT_PERIOD_OPTIONS,
    Lease,
    Room,
)
from app.services import AppServices
from app.ui.utils import (
    ask_save_filename,
    ask_yes_no,
    format_date_range,
    format_decimal,
    format_money,
    parse_date,
    parse_float,
    show_error,
    show_info,
    write_xlsx,
)
from app.ui.widgets import (
    FORM_LABEL_WIDTH,
    DataTable,
    DatePickerField,
    DateRangeField,
    DecimalEntry,
    DiscountPeriodsEditor,
    FormDialog,
    FreePeriodsEditor,
    bind_entry_max_length,
)


class LeaseFormDialog(FormDialog):
    def __init__(
        self,
        master,
        title: str,
        rooms: list[Room],
        initial: dict | None = None,
        lock_room: bool = False,
        allow_status: bool = False,
    ) -> None:
        super().__init__(
            master,
            title,
            width=600,
            height=720,
            scrollable=True,
            label_width=FORM_LABEL_WIDTH,
        )
        initial = initial or {}
        self.rooms = rooms
        self.lock_room = lock_room

        # 项目顺序保持房间列表中的出现顺序
        self.project_rooms: OrderedDict[str, list[Room]] = OrderedDict()
        for room in rooms:
            self.project_rooms.setdefault(room.project_name, []).append(room)
        project_names = list(self.project_rooms.keys())

        init_project = initial.get("project_name") or (project_names[0] if project_names else "")
        init_room = initial.get("room_no") or ""
        if init_project in self.project_rooms:
            room_nos = [r.room_no for r in self.project_rooms[init_project]]
            if init_room not in room_nos:
                init_room = room_nos[0] if room_nos else ""
        else:
            init_room = ""

        self.project_var = ctk.StringVar(value=init_project)
        self.room_var = ctk.StringVar(value=init_room)
        self.tenant_var = ctk.StringVar(value=str(initial.get("tenant", "")))
        self.deposit_var = ctk.StringVar(value=format_decimal(initial.get("deposit", "")))
        self.rent_var = ctk.StringVar(value=format_decimal(initial.get("monthly_rent", "")))
        init_period = initial.get("payment_period") or DEFAULT_PAYMENT_PERIOD
        if init_period not in PAYMENT_PERIOD_OPTIONS:
            init_period = DEFAULT_PAYMENT_PERIOD
        self.payment_period_var = ctk.StringVar(value=init_period)
        self.start_var = ctk.StringVar(value=initial.get("start_date", ""))
        self.end_var = ctk.StringVar(value=initial.get("end_date", ""))
        self.status_var = ctk.StringVar(value=initial.get("status", "生效"))

        self.project_menu = ctk.CTkOptionMenu(
            self.body,
            values=project_names or ["无项目"],
            variable=self.project_var,
            command=self._on_project_changed,
            width=200,
            dynamic_resizing=False,
        )
        self.room_menu = ctk.CTkOptionMenu(
            self.body,
            values=self._room_values_for(init_project),
            variable=self.room_var,
            width=200,
            dynamic_resizing=False,
        )
        if lock_room or not project_names:
            self.project_menu.configure(state="disabled")
            self.room_menu.configure(state="disabled")

        self.add_field(0, "项目", self.project_menu)
        self.add_field(1, "房间", self.room_menu)
        tenant_entry = ctk.CTkEntry(self.body, textvariable=self.tenant_var)
        bind_entry_max_length(tenant_entry, NAME_MAX_LENGTH)
        self.add_field(2, "租户", tenant_entry)
        self.add_field(3, "押金（元）", DecimalEntry(self.body, textvariable=self.deposit_var))
        self.add_field(4, "月租金（元）", DecimalEntry(self.body, textvariable=self.rent_var))
        self.add_field(
            5,
            "缴费周期",
            ctk.CTkOptionMenu(
                self.body,
                values=list(PAYMENT_PERIOD_OPTIONS),
                variable=self.payment_period_var,
            ),
        )
        self.add_field(
            6,
            "起租时间",
            DatePickerField(
                self.body,
                textvariable=self.start_var,
                max_date_getter=lambda: self._parse_bound_date(self.end_var.get()),
            ),
        )
        self.add_field(
            7,
            "到期时间",
            DatePickerField(
                self.body,
                textvariable=self.end_var,
                min_date_getter=lambda: self._parse_bound_date(self.start_var.get()),
            ),
        )

        free_initial = initial.get("free_periods") or []
        self.free_editor = FreePeriodsEditor(self.body, initial=free_initial)
        self.free_editor.grid(row=8, column=0, columnspan=2, sticky="ew", pady=8)

        discount_initial = initial.get("discounts") or []
        self.discount_editor = DiscountPeriodsEditor(
            self.body,
            initial=discount_initial,
            lease_start_var=self.start_var,
            lease_end_var=self.end_var,
            monthly_rent_var=self.rent_var,
        )
        self.discount_editor.grid(row=9, column=0, columnspan=2, sticky="ew", pady=8)

        next_row = 10
        if allow_status:
            self.add_field(
                next_row,
                "状态",
                ctk.CTkOptionMenu(
                    self.body, values=["生效", "结束"], variable=self.status_var
                ),
            )

    def _room_values_for(self, project_name: str) -> list[str]:
        rooms = self.project_rooms.get(project_name, [])
        return [r.room_no for r in rooms] or ["无房间"]

    def _on_project_changed(self, project_name: str) -> None:
        values = self._room_values_for(project_name)
        self.room_menu.configure(values=values)
        self.room_var.set(values[0])

    @staticmethod
    def _parse_bound_date(text: str):
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            from datetime import date as _date

            return _date.fromisoformat(raw)
        except ValueError:
            return None

    def _selected_room_id(self) -> int:
        if not self.rooms:
            raise ValueError("请先创建房间")
        project_name = self.project_var.get()
        room_no = self.room_var.get()
        for room in self.project_rooms.get(project_name, []):
            if room.room_no == room_no:
                return room.id
        raise ValueError("请选择有效的项目和房间")

    def collect(self) -> dict:
        tenant = self.tenant_var.get().strip()
        if not tenant:
            raise ValueError("租户不能为空")
        if len(tenant) > NAME_MAX_LENGTH:
            raise ValueError(f"租户不能超过{NAME_MAX_LENGTH}个字符")
        period = self.payment_period_var.get().strip()
        if not period:
            raise ValueError("缴费周期不能为空")
        if period not in PAYMENT_PERIOD_OPTIONS:
            raise ValueError("缴费周期无效")
        return {
            "room_id": self._selected_room_id(),
            "tenant": tenant,
            "deposit": parse_float(self.deposit_var.get(), "押金"),
            "monthly_rent": parse_float(self.rent_var.get(), "月租金"),
            "payment_period": period,
            "start_date": parse_date(self.start_var.get(), "起租时间"),
            "end_date": parse_date(self.end_var.get(), "到期时间"),
            "free_periods": self.free_editor.get_periods(),
            "discounts": self.discount_editor.get_discounts(),
            "status": self.status_var.get(),
        }


class LeasesPage(ctk.CTkFrame):
    def __init__(self, master, services: AppServices, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.services = services
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header, text="租赁管理", font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, sticky="w")

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
        self.status_var = ctk.StringVar(value="全部状态")
        ctk.CTkOptionMenu(
            filter_box,
            values=["全部状态", "生效", "结束"],
            variable=self.status_var,
            command=lambda _v: self.refresh(),
            width=88,
            dynamic_resizing=False,
        ).pack(side="left", padx=(8, 0))
        self.term_from_var = ctk.StringVar(value="")
        self.term_to_var = ctk.StringVar(value="")
        DateRangeField(
            filter_box,
            startvariable=self.term_from_var,
            endvariable=self.term_to_var,
            start_placeholder="租期开始",
            end_placeholder="租期截止",
            entry_width=76,
            command=self.refresh,
        ).pack(side="left", padx=(8, 0))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=3, sticky="e", padx=(10, 8))
        ctk.CTkButton(actions, text="新建租赁", height=28, width=80, command=self.create_lease).pack(
            side="left", padx=(0, 4)
        )
        ctk.CTkButton(actions, text="编辑", height=28, width=56, command=self.edit_lease).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="导出", height=28, width=56, command=self.export_xlsx
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="删除", height=28, width=56, fg_color="#b91c1c", command=self.delete_lease
        ).pack(side="left", padx=(4, 0))

        self.table = DataTable(
            self,
            columns=[
                ("id", "ID", 48),
                ("project", "项目", 110),
                ("room", "房间", 70),
                ("tenant", "租户", 100),
                ("deposit", "押金（元）", 108),
                ("rent", "月租金（元）", 116),
                ("period", "缴费周期", 72),
                ("term", "租期", 150),
                ("free", "免租期", 150),
                ("discount", "折/减（月周期）", 220),
                ("status", "状态", 60),
            ],
            column_anchors={
                "id": "center",
                "project": "w",
                "room": "center",
                "tenant": "w",
                "deposit": "e",
                "rent": "e",
                "period": "center",
                "term": "center",
                "free": "center",
                "discount": "w",
                "status": "center",
            },
            style_prefix="TallyLease",
            rowheight=28,
            cell_pady=2,
            fit_content_columns=("deposit", "rent", "term", "free"),
        )
        self.table.grid(row=1, column=0, sticky="nsew")

    def _rooms(self) -> list[Room]:
        return self.services.rooms.list_all()  # type: ignore[union-attr]

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

    def _parse_filter_date(self, text: str) -> date | None:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def _term_range(self) -> tuple[date | None, date | None]:
        return (
            self._parse_filter_date(self.term_from_var.get()),
            self._parse_filter_date(self.term_to_var.get()),
        )

    def _filtered_leases(self) -> list[Lease]:
        if not self.services.is_ready or self.services.leases is None:
            return []
        status = self.status_var.get().strip()
        # 仅「生效」「结束」作为状态条件；「全部状态」等一律不过滤
        status_filter = status if status in {"生效", "结束"} else None
        leases = self.services.leases.list_all(
            status_filter,
            self._current_project_id(),
        )
        room_no = self._current_room_no()
        if room_no is not None:
            leases = [lease for lease in leases if lease.room_no == room_no]
        term_from, term_to = self._term_range()
        if term_from is not None and term_to is not None and term_from > term_to:
            term_from, term_to = term_to, term_from
        # 与筛选区间有交集的租期
        if term_from is not None:
            leases = [lease for lease in leases if lease.end_date >= term_from]
        if term_to is not None:
            leases = [lease for lease in leases if lease.start_date <= term_to]
        return leases

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.leases is None:
            return
        self._reload_project_filters()
        leases = self._filtered_leases()
        rows = []
        for lease in leases:
            rows.append(
                (
                    lease.id,
                    lease.project_name,
                    lease.room_no,
                    lease.tenant or "",
                    format_money(lease.deposit),
                    format_money(lease.monthly_rent),
                    lease.payment_period,
                    format_date_range(lease.start_date, lease.end_date),
                    lease.free_periods_label(),
                    lease.discounts_label(),
                    lease.status,
                )
            )
        self.table.set_rows(rows, [str(l.id) for l in leases])

    def export_xlsx(self) -> None:
        if not self.services.is_ready or self.services.leases is None:
            return
        leases = self._filtered_leases()
        if not leases:
            show_info("当前没有可导出的租赁记录")
            return
        path = ask_save_filename(
            title="导出租赁记录",
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
            initialfile=f"租赁记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        )
        if not path:
            return
        try:
            write_xlsx(
                path,
                [
                    "ID",
                    "项目",
                    "房间",
                    "租户",
                    "押金（元）",
                    "月租金（元）",
                    "缴费周期",
                    "起租时间",
                    "到期时间",
                    "免租期",
                    "折/减（月周期）",
                    "状态",
                ],
                [
                    [
                        lease.id,
                        lease.project_name,
                        lease.room_no,
                        lease.tenant or "",
                        round(lease.deposit, 2),
                        round(lease.monthly_rent, 2),
                        lease.payment_period,
                        lease.start_date.isoformat(),
                        lease.end_date.isoformat(),
                        lease.free_periods_label(),
                        lease.discounts_label(),
                        lease.status,
                    ]
                    for lease in leases
                ],
                sheet_title="租赁记录",
            )
            show_info(f"已导出 {len(leases)} 条记录")
        except OSError as exc:
            show_error(f"导出失败：{exc}")
        except Exception as exc:
            show_error(f"导出失败：{exc}")

    def create_lease(self) -> None:
        rooms = self._rooms()
        if not rooms:
            show_info("请先创建房间")
            return

        def save(data: dict) -> None:
            self.services.leases.create(  # type: ignore[union-attr]
                room_id=data["room_id"],
                tenant=data["tenant"],
                deposit=data["deposit"],
                monthly_rent=data["monthly_rent"],
                start_date=data["start_date"],
                end_date=data["end_date"],
                free_periods=data["free_periods"],
                payment_period=data["payment_period"],
                discounts=data["discounts"],
            )
            self.refresh()

        LeaseFormDialog(self, "新建租赁", rooms).show(on_submit=save)

    def edit_lease(self) -> None:
        lease_id = self._selected_id()
        if lease_id is None:
            show_info("请先选择一条租赁")
            return
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        if not lease:
            show_error("租赁不存在")
            return
        rooms = self._rooms()
        free_periods = [
            (p.start_date.isoformat(), p.end_date.isoformat())
            for p in (lease.free_periods or [])
        ]
        discounts = [
            (
                d.start_date.isoformat(),
                d.end_date.isoformat(),
                d.kind,
                f"{d.value:.2f}",
            )
            for d in (lease.discounts or [])
        ]

        def save(data: dict) -> None:
            self.services.leases.update(  # type: ignore[union-attr]
                lease_id,
                tenant=data["tenant"],
                deposit=data["deposit"],
                monthly_rent=data["monthly_rent"],
                start_date=data["start_date"],
                end_date=data["end_date"],
                free_periods=data["free_periods"],
                status=data["status"],
                payment_period=data["payment_period"],
                discounts=data["discounts"],
            )
            self.refresh()

        LeaseFormDialog(
            self,
            "编辑租赁",
            rooms,
            {
                "project_name": lease.project_name,
                "room_no": lease.room_no,
                "tenant": lease.tenant,
                "deposit": lease.deposit,
                "monthly_rent": lease.monthly_rent,
                "payment_period": lease.payment_period,
                "start_date": lease.start_date.isoformat(),
                "end_date": lease.end_date.isoformat(),
                "free_periods": free_periods,
                "discounts": discounts,
                "status": lease.status,
            },
            lock_room=True,
            allow_status=True,
        ).show(on_submit=save)

    def delete_lease(self) -> None:
        lease_id = self._selected_id()
        if lease_id is None:
            show_info("请先选择一条租赁")
            return
        if not ask_yes_no("删除租赁将同时删除其缴费记录，确认继续？"):
            return
        self.services.leases.delete(lease_id)  # type: ignore[union-attr]
        self.refresh()
