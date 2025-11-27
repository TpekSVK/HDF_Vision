from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QMetaObject, Signal, Slot


class _Task(QObject):
    finished = Signal(object)

    def __init__(self, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs
        self._result: Any = None
        self._exc: BaseException | None = None

    def _execute(self) -> None:
        try:
            self._result = self._func(*self._args, **self._kwargs)
        except BaseException as exc:  # propagate any exception type
            self._exc = exc
        finally:
            # Emit finished on the Qt event loop thread
            QMetaObject.invokeMethod(self, "_emit_finished", Qt.QueuedConnection)

    @Slot()
    def _emit_finished(self) -> None:
        self.finished.emit(self)

    def result(self) -> Any:
        if self._exc:
            raise self._exc
        return self._result


def run(func: Callable[..., Any], *args: Any, **kwargs: Any) -> _Task:
    """Lightweight replacement for QtConcurrent.run.

    Executes *func* in a background daemon thread and returns a task object
    exposing a ``finished`` signal and ``result()`` accessor, matching the
    subset of functionality needed by the UI for Modbus actions.
    """

    task = _Task(func, args, kwargs)
    thread = threading.Thread(target=task._execute, daemon=True)
    thread.start()
    return task
