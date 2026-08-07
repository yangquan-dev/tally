from __future__ import annotations

from collections import OrderedDict

import customtkinter as ctk

from app.models import (
    DEFAULT_PAYMENT_PERIOD,
    PAYMENT_PERIOD_OPTIONS,
    Room,
)
from app.services import AppServices, ValidationError
from app.ui.utils import (
    ask_yes_no,
    format_money,
    parse_date,
    parse_float,
    show_error,
    show_info,
)
from app.ui.widgets import DataTable, DatePickerField, FormDialog, FreePeriodsEditor


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
        super().__init__(master, title, width=680, height=760)
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
        self.deposit_var = ctk.StringVar(value=str(initial.get("deposit", "")))
        self.rent_var = ctk.StringVar(value=str(initial.get("monthly_rent", "")))
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
        )
        self.room_menu = ctk.CTkOptionMenu(
            self.body,
            values=self._room_values_for(init_project),
            variable=self.room_var,
        )
        if lock_room or not project_names:
            self.project_menu.configure(state="disabled")
            self.room_menu.configure(state="disabled")

        self.add_field(0, "项目", self.project_menu)
        self.add_field(1, "房间", self.room_menu)
        self.add_field(2, "押金", ctk.CTkEntry(self.body, textvariable=self.deposit_var))
        self.add_field(3, "月租金", ctk.CTkEntry(self.body, textvariable=self.rent_var))
        self.add_field(
            4,
            "缴费周期",
            ctk.CTkOptionMenu(
                self.body,
                values=list(PAYMENT_PERIOD_OPTIONS),
                variable=self.payment_period_var,
            ),
        )
        self.add_field(5, "起租时间", DatePickerField(self.body, textvariable=self.start_var))
        self.add_field(6, "到期时间", DatePickerField(self.body, textvariable=self.end_var))

        free_initial = initial.get("free_periods") or []
        self.free_editor = FreePeriodsEditor(self.body, initial=free_initial)
        self.free_editor.grid(row=7, column=0, columnspan=2, sticky="ew", pady=8)

        next_row = 8
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
        period = self.payment_period_var.get().strip()
        if not period:
            raise ValueError("缴费周期不能为空")
        if period not in PAYMENT_PERIOD_OPTIONS:
            raise ValueError("缴费周期无效")
        return {
            "room_id": self._selected_room_id(),
            "deposit": parse_float(self.deposit_var.get(), "押金"),
            "monthly_rent": parse_float(self.rent_var.get(), "月租金"),
            "payment_period": period,
            "start_date": parse_date(self.start_var.get(), "起租时间"),
            "end_date": parse_date(self.end_var.get(), "到期时间"),
            "free_periods": self.free_editor.get_periods(),
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
        filter_box.grid(row=0, column=1, sticky="e", padx=(12, 8))
        ctk.CTkLabel(filter_box, text="项目", text_color="#6b7280").pack(
            side="left", padx=(0, 8)
        )
        self.project_var = ctk.StringVar(value="全部项目")
        self.project_menu = ctk.CTkOptionMenu(
            filter_box,
            values=["全部项目"],
            variable=self.project_var,
            command=lambda _v: self.refresh(),
            width=180,
        )
        self.project_menu.pack(side="left")
        ctk.CTkLabel(filter_box, text="状态", text_color="#6b7280").pack(
            side="left", padx=(12, 8)
        )
        self.status_var = ctk.StringVar(value="全部")
        ctk.CTkOptionMenu(
            filter_box,
            values=["全部", "生效", "结束"],
            variable=self.status_var,
            command=lambda _v: self.refresh(),
            width=100,
        ).pack(side="left")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(actions, text="新建租赁", width=100, command=self.create_lease).pack(
            side="left", padx=4
        )
        ctk.CTkButton(actions, text="编辑", width=80, command=self.edit_lease).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="结束租赁", width=100, command=self.end_lease
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="删除", width=80, fg_color="#b91c1c", command=self.delete_lease
        ).pack(side="left", padx=4)

        self.table = DataTable(
            self,
            columns=[
                ("id", "ID", 48),
                ("project", "项目", 120),
                ("room", "房间", 70),
                ("deposit", "押金", 80),
                ("rent", "月租金", 80),
                ("period", "缴费周期", 72),
                ("term", "租期（起租~到期）", 210),
                ("free", "免租期", 210),
                ("status", "状态", 60),
            ],
            column_anchors={
                "id": "center",
                "project": "w",
                "room": "center",
                "deposit": "e",
                "rent": "e",
                "period": "center",
                "term": "center",
                "free": "w",
                "status": "center",
            },
            style_prefix="TallyLease",
            rowheight=34,
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

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.leases is None:
            return
        self._reload_project_filters()
        status = self.status_var.get()
        leases = self.services.leases.list_all(
            None if status == "全部" else status,
            self._current_project_id(),
        )
        rows = []
        for lease in leases:
            rows.append(
                (
                    lease.id,
                    lease.project_name,
                    lease.room_no,
                    format_money(lease.deposit),
                    format_money(lease.monthly_rent),
                    lease.payment_period,
                    f"{lease.start_date.isoformat()} ~ {lease.end_date.isoformat()}",
                    lease.free_periods_label(),
                    lease.status,
                )
            )
        self.table.set_rows(rows, [str(l.id) for l in leases])

    def create_lease(self) -> None:
        rooms = self._rooms()
        if not rooms:
            show_info("请先创建房间")
            return
        data = LeaseFormDialog(self, "新建租赁", rooms).show()
        if not data:
            return
        try:
            self.services.leases.create(  # type: ignore[union-attr]
                room_id=data["room_id"],
                deposit=data["deposit"],
                monthly_rent=data["monthly_rent"],
                start_date=data["start_date"],
                end_date=data["end_date"],
                free_periods=data["free_periods"],
                payment_period=data["payment_period"],
            )
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

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
        data = LeaseFormDialog(
            self,
            "编辑租赁",
            rooms,
            {
                "project_name": lease.project_name,
                "room_no": lease.room_no,
                "deposit": lease.deposit,
                "monthly_rent": lease.monthly_rent,
                "payment_period": lease.payment_period,
                "start_date": lease.start_date.isoformat(),
                "end_date": lease.end_date.isoformat(),
                "free_periods": free_periods,
                "status": lease.status,
            },
            lock_room=True,
            allow_status=True,
        ).show()
        if not data:
            return
        try:
            self.services.leases.update(  # type: ignore[union-attr]
                lease_id,
                deposit=data["deposit"],
                monthly_rent=data["monthly_rent"],
                start_date=data["start_date"],
                end_date=data["end_date"],
                free_periods=data["free_periods"],
                status=data["status"],
                payment_period=data["payment_period"],
            )
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

    def end_lease(self) -> None:
        lease_id = self._selected_id()
        if lease_id is None:
            show_info("请先选择一条租赁")
            return
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        if not lease:
            return
        if lease.status != "生效":
            show_info("该租赁已是结束状态")
            return
        if not ask_yes_no("确认结束该租赁？"):
            return
        try:
            self.services.leases.update(  # type: ignore[union-attr]
                lease_id,
                deposit=lease.deposit,
                monthly_rent=lease.monthly_rent,
                start_date=lease.start_date,
                end_date=lease.end_date,
                free_periods=[
                    (p.start_date, p.end_date) for p in (lease.free_periods or [])
                ],
                status="结束",
                payment_period=lease.payment_period,
            )
            self.refresh()
        except ValidationError as exc:
            show_error(str(exc))

    def delete_lease(self) -> None:
        lease_id = self._selected_id()
        if lease_id is None:
            show_info("请先选择一条租赁")
            return
        if not ask_yes_no("删除租赁将同时删除其缴费记录，确认继续？"):
            return
        self.services.leases.delete(lease_id)  # type: ignore[union-attr]
        self.refresh()
