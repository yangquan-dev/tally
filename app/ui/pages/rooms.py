from __future__ import annotations

import customtkinter as ctk

from app.services import AppServices
from app.ui.utils import ask_yes_no, format_decimal, parse_float, show_error, show_info
from app.ui.widgets import DataTable, DecimalEntry, FormDialog

STATUS_TAGS = {
    "空置": "vacant",
    "在租": "rented",
    "即将到期": "expiring",
    "已到期": "expired",
}


class RoomFormDialog(FormDialog):
    def __init__(
        self,
        master,
        title: str,
        projects: list[tuple[int, str]],
        initial: dict | None = None,
        lock_project: bool = False,
    ) -> None:
        super().__init__(master, title, height=320)
        initial = initial or {}
        self.projects = projects
        names = [name for _, name in projects]
        default_name = initial.get("project_name") or (names[0] if names else "")
        self.project_var = ctk.StringVar(value=default_name)
        self.room_no_var = ctk.StringVar(value=initial.get("room_no", ""))
        self.area_var = ctk.StringVar(value=format_decimal(initial.get("area", "")))

        project_widget = ctk.CTkOptionMenu(
            self.body, values=names or ["无项目"], variable=self.project_var
        )
        if lock_project or not names:
            project_widget.configure(state="disabled")
        self.add_field(0, "所属项目", project_widget)
        self.add_field(1, "房间号", ctk.CTkEntry(self.body, textvariable=self.room_no_var))
        self.add_field(2, "面积(㎡)", DecimalEntry(self.body, textvariable=self.area_var))

    def collect(self) -> dict:
        if not self.projects:
            raise ValueError("请先创建项目")
        name = self.project_var.get()
        project_id = next(pid for pid, pname in self.projects if pname == name)
        return {
            "project_id": project_id,
            "room_no": self.room_no_var.get().strip(),
            "area": parse_float(self.area_var.get(), "面积"),
        }


class RoomsPage(ctk.CTkFrame):
    def __init__(self, master, services: AppServices, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.services = services
        self.filter_project_id: int | None = None
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="房间管理", font=ctk.CTkFont(size=22, weight="bold")
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

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=3, sticky="e", padx=(10, 8))
        ctk.CTkButton(actions, text="新建房间", height=28, width=80, command=self.create_room).pack(
            side="left", padx=(0, 4)
        )
        ctk.CTkButton(actions, text="编辑", height=28, width=56, command=self.edit_room).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="删除", height=28, width=56, fg_color="#b91c1c", command=self.delete_room
        ).pack(side="left", padx=(4, 0))

        self.table = DataTable(
            self,
            columns=[
                ("id", "ID", 60),
                ("project", "项目", 160),
                ("room_no", "房间号", 120),
                ("area", "面积(㎡)", 100),
                ("status", "租赁状态", 110),
            ],
            column_anchors={
                "id": "center",
                "project": "w",
                "room_no": "center",
                "area": "e",
                "status": "center",
            },
            rowheight=34,
            style_prefix="TallyRoom",
        )
        self.table.grid(row=1, column=0, sticky="nsew")
        self._configure_status_tags()

    def _configure_status_tags(self) -> None:
        tree = self.table.tree
        tree.tag_configure(
            "vacant",
            foreground="#6b7280",
            background="#f3f4f6",
            font=("PingFang SC", 11, "bold"),
        )
        tree.tag_configure(
            "rented",
            foreground="#047857",
            background="#ecfdf5",
            font=("PingFang SC", 11, "bold"),
        )
        tree.tag_configure(
            "expiring",
            foreground="#c2410c",
            background="#fff7ed",
            font=("PingFang SC", 11, "bold"),
        )
        tree.tag_configure(
            "expired",
            foreground="#b91c1c",
            background="#fef2f2",
            font=("PingFang SC", 11, "bold"),
        )

    def set_project_filter(self, project_id: int | None) -> None:
        self.filter_project_id = project_id
        self._reload_project_filters()
        if project_id is None:
            self.project_var.set("全部项目")
        else:
            project = self.services.projects.get(project_id)
            if project:
                self.project_var.set(project.name)
        self._reload_room_filters(preserve_selection=False)
        self.refresh()

    def _reload_project_filters(self) -> None:
        if self.services.projects is None:
            return
        projects = self.services.projects.list_all()  # 按项目创建顺序
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

    def _selected_id(self) -> int | None:
        iid = self.table.selected_iid()
        return int(iid) if iid else None

    def _current_project_id(self) -> int | None:
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

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.rooms is None:
            return
        self._reload_project_filters()
        project_id = self._current_project_id()
        rooms = (
            self.services.rooms.list_by_project(project_id)
            if project_id is not None
            else self.services.rooms.list_all()
        )
        # 刷新状态
        for room in rooms:
            self.services.rooms.refresh_status(room.id)
        rooms = (
            self.services.rooms.list_by_project(project_id)
            if project_id is not None
            else self.services.rooms.list_all()
        )
        room_no = self._current_room_no()
        if room_no is not None:
            rooms = [r for r in rooms if r.room_no == room_no]
        rows = [
            (r.id, r.project_name, r.room_no, f"{r.area:.2f}", r.lease_status)
            for r in rooms
        ]
        tags = [STATUS_TAGS.get(r.lease_status, "vacant") for r in rooms]
        self.table.set_rows(rows, [str(r.id) for r in rooms], tags=tags)

    def create_room(self) -> None:
        projects = [(p.id, p.name) for p in self.services.projects.list_all()]
        if not projects:
            show_info("请先创建项目")
            return
        current = self._current_project_id()
        initial = {}
        if current is not None:
            project = self.services.projects.get(current)
            if project:
                initial["project_name"] = project.name

        def save(data: dict) -> None:
            self.services.rooms.create(**data)
            self.refresh()

        RoomFormDialog(
            self, "新建房间", projects, initial=initial, lock_project=current is not None
        ).show(on_submit=save)

    def edit_room(self) -> None:
        room_id = self._selected_id()
        if room_id is None:
            show_info("请先选择一个房间")
            return
        room = self.services.rooms.get(room_id)
        if not room:
            show_error("房间不存在")
            return
        projects = [(p.id, p.name) for p in self.services.projects.list_all()]

        def save(data: dict) -> None:
            self.services.rooms.update(room_id, data["room_no"], data["area"])
            self.refresh()

        RoomFormDialog(
            self,
            "编辑房间",
            projects,
            {
                "project_name": room.project_name,
                "room_no": room.room_no,
                "area": room.area,
            },
            lock_project=True,
        ).show(on_submit=save)

    def delete_room(self) -> None:
        room_id = self._selected_id()
        if room_id is None:
            show_info("请先选择一个房间")
            return
        if not ask_yes_no("删除房间将同时删除其租赁和缴费记录，确认继续？"):
            return
        self.services.rooms.delete(room_id)
        self.refresh()
