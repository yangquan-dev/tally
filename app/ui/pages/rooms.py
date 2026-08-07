from __future__ import annotations

import customtkinter as ctk

from app.services import AppServices, ValidationError
from app.ui.utils import ask_yes_no, parse_float, show_error, show_info
from app.ui.widgets import DataTable, FormDialog

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
        self.area_var = ctk.StringVar(value=str(initial.get("area", "")))

        project_widget = ctk.CTkOptionMenu(
            self.body, values=names or ["无项目"], variable=self.project_var
        )
        if lock_project or not names:
            project_widget.configure(state="disabled")
        self.add_field(0, "所属项目", project_widget)
        self.add_field(1, "房间号", ctk.CTkEntry(self.body, textvariable=self.room_no_var))
        self.add_field(2, "面积(㎡)", ctk.CTkEntry(self.body, textvariable=self.area_var))

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
        filter_box.grid(row=0, column=1, sticky="e", padx=(12, 8))
        ctk.CTkLabel(filter_box, text="项目筛选", text_color="#6b7280").pack(
            side="left", padx=(0, 8)
        )
        self.filter_var = ctk.StringVar(value="全部项目")
        self.filter_menu = ctk.CTkOptionMenu(
            filter_box,
            values=["全部项目"],
            variable=self.filter_var,
            command=lambda _v: self.refresh(),
            width=180,
        )
        self.filter_menu.pack(side="left")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(actions, text="新建房间", width=100, command=self.create_room).pack(
            side="left", padx=4
        )
        ctk.CTkButton(actions, text="编辑", width=80, command=self.edit_room).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="删除", width=80, fg_color="#b91c1c", command=self.delete_room
        ).pack(side="left", padx=4)

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
        self._reload_filters()
        if project_id is None:
            self.filter_var.set("全部项目")
        else:
            project = self.services.projects.get(project_id)
            if project:
                self.filter_var.set(project.name)
        self.refresh()

    def _reload_filters(self) -> None:
        if self.services.projects is None:
            return
        projects = self.services.projects.list_all()  # 按项目创建顺序
        current = self.filter_var.get()
        values = ["全部项目"] + [p.name for p in projects]
        self.filter_menu.configure(values=values)
        if current in values:
            self.filter_var.set(current)
        else:
            self.filter_var.set("全部项目")

    def _selected_id(self) -> int | None:
        iid = self.table.selected_iid()
        return int(iid) if iid else None

    def _current_project_id(self) -> int | None:
        name = self.filter_var.get()
        if name == "全部项目":
            return None
        for p in self.services.projects.list_all():
            if p.name == name:
                return p.id
        return None

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.rooms is None:
            return
        self._reload_filters()
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
        data = RoomFormDialog(
            self, "新建房间", projects, initial=initial, lock_project=current is not None
        ).show()
        if not data:
            return
        try:
            self.services.rooms.create(**data)
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

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
        data = RoomFormDialog(
            self,
            "编辑房间",
            projects,
            {
                "project_name": room.project_name,
                "room_no": room.room_no,
                "area": room.area,
            },
            lock_project=True,
        ).show()
        if not data:
            return
        try:
            self.services.rooms.update(room_id, data["room_no"], data["area"])
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

    def delete_room(self) -> None:
        room_id = self._selected_id()
        if room_id is None:
            show_info("请先选择一个房间")
            return
        if not ask_yes_no("删除房间将同时删除其租赁和缴费记录，确认继续？"):
            return
        self.services.rooms.delete(room_id)
        self.refresh()
