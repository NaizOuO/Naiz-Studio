"""把耗時的 PDF 運算丟到背景執行緒,讓 UI 不會卡住。"""

import threading
import traceback


class TaskRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self.cancel_event = threading.Event()
        self.done = 0
        self.total = 1
        self.message = ""
        self.lines = []
        self.error = None
        self.finished = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def ratio(self) -> float:
        with self._lock:
            return 0.0 if self.total <= 0 else min(1.0, self.done / self.total)

    def snapshot(self):
        with self._lock:
            return self.done, self.total, self.message, list(self.lines), self.error, self.finished

    def report(self, done, total, message=""):
        with self._lock:
            self.done, self.total, self.message = done, total, message

    def log(self, text):
        with self._lock:
            self.lines.append(text)

    def start(self, job):
        """job 會收到這個 runner 本身,可呼叫 report() / log() / cancel_event。"""
        if self.running:
            return False
        self.cancel_event.clear()
        with self._lock:
            self.done, self.total, self.message = 0, 1, "準備中"
            self.lines, self.error, self.finished = [], None, False

        def wrapper():
            try:
                job(self)
            except Exception as exc:
                with self._lock:
                    self.error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            finally:
                with self._lock:
                    self.finished = True

        self._thread = threading.Thread(target=wrapper, daemon=True)
        self._thread.start()
        return True

    def request_cancel(self):
        self.cancel_event.set()
