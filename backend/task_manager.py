# Vera_Shop — Task Manager

from __future__ import annotations

import os
import signal
import time
import uuid
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .models import Package, Task, TaskType, TaskState, PackageSource

# Mismo PID file que escribe applet.py (ver applet.py: _APPLET_PID_FILE) y
# que ya lee ui/settings_page.py para reiniciar el applet.
_APPLET_PID_FILE = Path.home() / ".config" / "yl-soft" / "applet.pid"


def _notify_applet_update_done() -> None:
    """Avisa al applet (si está corriendo) que una instalación/actualización/
    remoción terminó, vía SIGUSR2, para que reprograme su re-chequeo en
    vez de esperar al siguiente intervalo periódico (hasta 12h en modo
    background). El applet ya escucha SIGUSR2 (ver applet.py: _sig_usr2)
    pero nada se lo enviaba, así que esta notificación nunca llegaba."""
    try:
        pid = int(_APPLET_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGUSR2)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass
    except Exception:
        pass


_FINISHED_STATES = (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)


class TaskWorker(QThread):
    progress_updated = Signal(str, int, str)   # task_id, percent, status_text
    task_finished    = Signal(str, bool, str)  # task_id, success, message

    def __init__(self, task: Task, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.task = task

    def run(self):
        from . import flatpak_backend
        from . import admin_session
        try:
            import core.settings as _app_settings
            configured_mode = _app_settings.get("admin_expiry_mode", "always")
            configured_minutes = int(_app_settings.get("admin_expiry_custom_minutes", 5))
            current = admin_session.session()
            if current.mode != configured_mode or current.custom_minutes != configured_minutes:
                admin_session.configure(configured_mode, configured_minutes)
        except Exception:
            admin_session.configure("always", 5)
        try:
            from . import xbps_backend as _xbps
        except ImportError:
            _xbps = None  # type: ignore
        try:
            from . import local_installer as _local
        except ImportError:
            _local = None  # type: ignore

        task = self.task
        task.state = TaskState.RUNNING

        if task.package.source == PackageSource.LOCAL and _local:
            backend = None  # local usa run_local_*_task directamente
            try:
                if task.task_type == TaskType.LOCAL_REMOVE:
                    gen = _local.run_local_remove_task(task)
                elif task.task_type == TaskType.LOCAL_REINSTALL:
                    gen = _local.run_local_reinstall_task(task)
                else:
                    gen = _local.run_local_task(task)
                for progress, status in gen:
                    task.progress = progress
                    task.status_text = status
                    self.progress_updated.emit(task.id, progress, status)
            except Exception as e:
                task.state = TaskState.FAILED
                task.error_message = str(e)
                self.task_finished.emit(task.id, False, str(e))
                return
            success = task.state == TaskState.DONE
            self.task_finished.emit(
                task.id,
                success,
                "Done" if success else task.error_message,
            )
            return

        if task.package.source == PackageSource.FLATPAK:
            backend = flatpak_backend
        elif task.package.source == PackageSource.APPIMAGE:
            from . import appimage_backend
            backend = appimage_backend
        else:
            backend = _xbps

        try:
            for progress, status in backend.run_task(task):
                task.progress = progress
                task.status_text = status
                self.progress_updated.emit(task.id, progress, status)
        except Exception as e:
            task.state = TaskState.FAILED
            task.error_message = str(e)
            self.task_finished.emit(task.id, False, str(e))
            return

        success = task.state == TaskState.DONE
        if success and task.task_type != TaskType.REFRESH:
            admin_session.mark_authenticated()
        self.task_finished.emit(
            task.id,
            success,
            "Done" if success else task.error_message,
        )


class TaskManager(QObject):
    """
    Cola central de tareas.
    Admite hasta _MAX_CONCURRENT tareas corriendo al mismo tiempo.
    Nuevas llamadas a submit() siempre se encolan aunque haya tareas activas.
    """

    task_added     = Signal(object)         # Task
    task_started   = Signal(str)            # task_id (pasó de la cola a ejecutarse)
    task_updated   = Signal(str, int, str)  # task_id, progress, status
    task_completed = Signal(str, bool, str) # task_id, success, message
    task_cancelled = Signal(str)            # task_id
    task_removed   = Signal(str)            # task_id (quitada de la lista)

    _MAX_CONCURRENT = 3

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._tasks:   dict[str, Task]       = {}
        self._workers: dict[str, TaskWorker] = {}
        self._queue:   list[str]             = []

    # Public

    def submit(self, package: Package, task_type: TaskType) -> Task:
        """
        Encola una nueva tarea. Si hay un slot libre, arranca inmediatamente;
        de lo contrario espera en la cola FIFO.
        Siempre es seguro llamar mientras hay otras tareas en curso.
        """
        task = Task(
            id=str(uuid.uuid4()),
            package=package,
            task_type=task_type,
        )
        self._tasks[task.id] = task
        self._queue.append(task.id)
        self.task_added.emit(task)
        self._try_start_next()
        return task

    def cancel(self, task_id: str):
        """Cancela una tarea (si está corriendo, la termina; si está pendiente, la saca de la cola)."""
        task = self._tasks.get(task_id)
        if task and task.state in _FINISHED_STATES:
            return
        worker = self._workers.get(task_id)
        if worker and worker.isRunning():
            worker.terminate()
            worker.wait(2000)
        if task:
            task.state = TaskState.CANCELLED
            task.finished_at = time.time()
        self._workers.pop(task_id, None)
        if task_id in self._queue:
            self._queue.remove(task_id)
        if task:
            self.task_cancelled.emit(task_id)
        # Intentar arrancar la siguiente tarea pendiente
        self._try_start_next()

    def retry(self, task_id: str) -> Optional[Task]:
        """Vuelve a encolar una tarea terminada (fallida/cancelada/lista)
        como una tarea nueva con el mismo paquete y tipo."""
        old = self._tasks.get(task_id)
        if old is None or old.state not in _FINISHED_STATES:
            return None
        return self.submit(old.package, old.task_type)

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def remove_task(self, task_id: str) -> bool:
        """Quita una tarea ya terminada del historial."""
        task = self._tasks.get(task_id)
        if task is None or task.state not in _FINISHED_STATES:
            return False
        del self._tasks[task_id]
        self.task_removed.emit(task_id)
        return True

    def clear_finished(self) -> int:
        """Quita del historial todas las tareas terminadas. Devuelve cuántas."""
        ids = [tid for tid, t in self._tasks.items() if t.state in _FINISHED_STATES]
        for tid in ids:
            self.remove_task(tid)
        return len(ids)

    @property
    def active_tasks(self) -> list[Task]:
        return [t for t in self._tasks.values()
                if t.state in (TaskState.RUNNING, TaskState.PENDING)]

    @property
    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    # Internals

    def _try_start_next(self):
        """Arranca tantas tareas pendientes como slots libres haya."""
        running = sum(1 for w in self._workers.values() if w.isRunning())
        while running < self._MAX_CONCURRENT and self._queue:
            task_id = self._queue.pop(0)
            task = self._tasks.get(task_id)
            if task and task.state == TaskState.PENDING:
                self._start_task(task)
                running += 1

    def _start_task(self, task: Task):
        task.state = TaskState.RUNNING
        task.started_at = time.time()
        worker = TaskWorker(task, parent=self)
        self._workers[task.id] = worker
        worker.progress_updated.connect(self._on_progress)
        worker.task_finished.connect(self._on_finished)
        worker.start()
        self.task_started.emit(task.id)

    @Slot(str, int, str)
    def _on_progress(self, task_id: str, progress: int, status: str):
        self.task_updated.emit(task_id, progress, status)

    @Slot(str, bool, str)
    def _on_finished(self, task_id: str, success: bool, message: str):
        self._workers.pop(task_id, None)
        _t = self._tasks.get(task_id)
        if _t is not None:
            _t.finished_at = time.time()
        self.task_completed.emit(task_id, success, message)

        # Notificación de escritorio
        task = self._tasks.get(task_id)
        if task:
            try:
                from .notifier import notify_task_done
                notify_task_done(task.package.name, task.task_type.value, success)
            except Exception:
                pass

            # Tras un INSTALL/UPDATE/REMOVE exitoso → sincronizar la base de
            # datos de actualizaciones pendientes y las cachés de paquetes
            if success and task.task_type in (TaskType.INSTALL, TaskType.UPDATE, TaskType.REMOVE):
                try:
                    if task.package.source != PackageSource.LOCAL:
                        from .updates_db import updates_db as _updates_db
                        real_name = getattr(task.package, "pkgname", "") or task.package.name
                        _updates_db.remove_applied(
                            [real_name], task.package.source.value
                        )
                except Exception as _e:
                    print(f"[task_manager] updates_db sync error: {_e}")

                # Avisar al applet para que reprograme su re-chequeo
                # (ver _notify_applet_update_done arriba).
                _notify_applet_update_done()

            # Tras un REFRESH exitoso → sincronización adicional si hiciera falta
            if success and task.task_type == TaskType.REFRESH:
                pass

        # Arrancar siguiente tarea pendiente
        self._try_start_next()
