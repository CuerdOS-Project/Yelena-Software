# Yelena Software — Independent Tasks Window
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout

from core.i18n import tr
from backend.task_manager import TaskManager
from .tasks_page import TasksPage


class TasksWindow(QDialog):
    """Ventana separada: historial de tareas, progreso y salida en vivo."""

    def __init__(self, task_manager: TaskManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("nav_tasks"))
        self.setMinimumSize(820, 500)
        self.resize(1040, 640)
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.page = TasksPage(task_manager, self, show_header=True)
        layout.addWidget(self.page)

    def refresh_language(self) -> None:
        self.setWindowTitle(tr("nav_tasks"))
