from __future__ import annotations

import customtkinter as ctk

from app.models import NAME_MAX_LENGTH
from app.services import AppServices
from app.ui.utils import ask_yes_no, show_error, show_info
from app.ui.widgets import DataTable, FormDialog, bind_entry_max_length


class ProjectFormDialog(FormDialog):
    def __init__(self, master, title: str, initial: dict | None = None) -> None:
        super().__init__(master, title, height=220)
        initial = initial or {}
        name = str(initial.get("name") or "").strip()
        self.name_var = ctk.StringVar(master=self, value=name)
        self.name_entry = ctk.CTkEntry(self.body, textvariable=self.name_var)
        bind_entry_max_length(self.name_entry, NAME_MAX_LENGTH)
        self.add_field(0, "项目名称", self.name_entry)
        # 显式写入，避免部分环境下 StringVar 初值未同步到输入框
        if name:
            self.name_var.set(name)
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, name)

    def collect(self) -> dict:
        name = self.name_var.get().strip()
        if not name:
            raise ValueError("项目名称不能为空")
        if len(name) > NAME_MAX_LENGTH:
            raise ValueError(f"项目名称不能超过{NAME_MAX_LENGTH}个字符")
        return {"name": name}


class ProjectsPage(ctk.CTkFrame):
    def __init__(
        self,
        master,
        services: AppServices,
        on_open_rooms=None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.services = services
        self.on_open_rooms = on_open_rooms
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header, text="项目管理", font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        search_box = ctk.CTkFrame(header, fg_color="transparent")
        search_box.grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.search_var = ctk.StringVar(value="")
        self.search_entry = ctk.CTkEntry(
            search_box,
            textvariable=self.search_var,
            placeholder_text="按项目名称搜索",
            width=160,
        )
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", lambda _e: self.refresh())
        ctk.CTkButton(
            search_box, text="查询", height=28, width=52, command=self.refresh
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            search_box,
            text="重置",
            height=28,
            width=52,
            fg_color="#6b7280",
            command=self._reset_search,
        ).pack(side="left", padx=(6, 0))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=3, sticky="e", padx=(10, 8))
        ctk.CTkButton(actions, text="新建项目", height=28, width=80, command=self.create_project).pack(
            side="left", padx=(0, 4)
        )
        ctk.CTkButton(actions, text="编辑", height=28, width=56, command=self.edit_project).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="删除", height=28, width=56, fg_color="#b91c1c", command=self.delete_project
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="管理房间", height=28, width=80, command=self.open_rooms
        ).pack(side="left", padx=(4, 0))

        self.table = DataTable(
            self,
            columns=[
                ("id", "ID", 60),
                ("name", "项目", 360),
                ("created_at", "创建时间", 200),
            ],
            column_anchors={
                "id": "center",
                "name": "w",
                "created_at": "center",
            },
            style_prefix="TallyProject",
        )
        self.table.grid(row=1, column=0, sticky="nsew")

    def _selected_id(self) -> int | None:
        iid = self.table.selected_iid()
        return int(iid) if iid else None

    def _reset_search(self) -> None:
        self.search_var.set("")
        self.refresh()

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.projects is None:
            return
        keyword = self.search_var.get().strip().lower()
        projects = self.services.projects.list_all()
        if keyword:
            projects = [p for p in projects if keyword in (p.name or "").lower()]
        rows = [(p.id, p.name, p.created_at) for p in projects]
        self.table.set_rows(rows, [str(p.id) for p in projects])

    def create_project(self) -> None:
        def save(data: dict) -> None:
            self.services.projects.create(**data)
            self.refresh()

        ProjectFormDialog(self, "新建项目").show(on_submit=save)

    def edit_project(self) -> None:
        project_id = self._selected_id()
        if project_id is None:
            show_info("请先选择一个项目")
            return
        project = self.services.projects.get(project_id)  # type: ignore[union-attr]
        if not project:
            show_error("项目不存在")
            return
        name = (project.name or "").strip()

        def save(data: dict) -> None:
            self.services.projects.update(project_id, **data)  # type: ignore[union-attr]
            self.refresh()

        ProjectFormDialog(
            self,
            "编辑项目",
            {"name": name},
        ).show(on_submit=save)

    def delete_project(self) -> None:
        project_id = self._selected_id()
        if project_id is None:
            show_info("请先选择一个项目")
            return
        if not ask_yes_no("删除项目将同时删除其房间、租赁和缴费记录，确认继续？"):
            return
        self.services.projects.delete(project_id)
        self.refresh()

    def open_rooms(self) -> None:
        project_id = self._selected_id()
        if project_id is None:
            show_info("请先选择一个项目")
            return
        if self.on_open_rooms:
            self.on_open_rooms(project_id)
