import queue
import threading
import unittest
from unittest.mock import patch

from test_project_import import app


class StoppableQueue(queue.Queue):
    def get(self, *args, **kwargs):
        item = super().get(*args, **kwargs)
        if item is None:
            self.task_done()
            raise SystemExit
        return item


class ConcurrencyTests(unittest.TestCase):
    def test_pdf_limit_can_increase_and_decrease_with_active_tasks(self):
        with patch.object(threading.Thread, "start"):
            manager = app.TaskManager()
        manager.workers = []
        manager.pending = StoppableQueue()
        started = queue.Queue()
        releases = {str(i): threading.Event() for i in range(4)}

        def run(task):
            started.put(task["id"])
            releases[task["id"]].wait(5)

        manager._run = run
        manager.resize(3)
        manager.resize(1)
        try:
            for task_id in releases:
                manager.add({"id": task_id, "status": "queued"})
            first = started.get(timeout=2)
            with self.assertRaises(queue.Empty):
                started.get(timeout=0.1)
            manager.resize(2)
            second = started.get(timeout=2)
            manager.resize(1)
            self.assertEqual(manager.concurrency, 1)
            releases[first].set()
            with self.assertRaises(queue.Empty):
                started.get(timeout=0.1)
            releases[second].set()
            third = started.get(timeout=2)
            with self.assertRaises(queue.Empty):
                started.get(timeout=0.1)
            releases[third].set()
            fourth = started.get(timeout=2)
            releases[fourth].set()
        finally:
            for event in releases.values():
                event.set()
            for worker in manager.workers:
                manager.pending.put(None)
            for worker in manager.workers:
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
