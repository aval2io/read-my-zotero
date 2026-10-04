import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("zotero_app_codex", Path(__file__).resolve().parents[1] / "app.py")
app = importlib.util.module_from_spec(spec)
with patch.object(threading.Thread, "start"):
    spec.loader.exec_module(app)


class FakeRuns:
    def __init__(self):
        self.lock = threading.Lock()
        self.runs = {}
        self.added = []

    def add(self, *args, **kwargs):
        self.added.append((args, kwargs))


class AutoCodexTests(unittest.TestCase):
    def test_collection_waits_for_all_papers_and_uses_exact_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            project = root / "collection"
            papers = project / "papers"
            first = papers / "first"
            second = papers / "second"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            for paper in (first, second):
                (paper / "full.md").write_text("# paper", encoding="utf-8")
                (paper / "images").mkdir()
            (project / "manifest.json").write_text(json.dumps({
                "name": "collection", "project_type": "collection",
                "codex_workflow": {"enabled": True, "prompt": app.DEFAULT_CODEX_PROMPT},
            }), encoding="utf-8")
            tasks = SimpleNamespace(lock=threading.Lock(), tasks={
                "1": {"project_slug": "collection", "project_type": "collection", "paper_slug": "first", "status": "completed"},
                "2": {"project_slug": "collection", "project_type": "collection", "paper_slug": "second", "status": "running"},
            })
            runs = FakeRuns()
            with patch.object(app, "WORKSPACES", root), patch.object(app, "TASKS", tasks), patch.object(app, "CODEX_RUNS", runs):
                self.assertFalse(app.maybe_start_project_codex("collection"))
                self.assertEqual(runs.added, [])
                tasks.tasks["2"]["status"] = "completed"
                self.assertTrue(app.maybe_start_project_codex("collection"))
            self.assertEqual(len(runs.added), 2)
            for args, kwargs in runs.added:
                self.assertEqual(args[1], app.DEFAULT_CODEX_PROMPT)
                self.assertEqual(kwargs["origin"], "auto")
                self.assertTrue(str(kwargs["working_dir"]).endswith(("/first", "/second")))


if __name__ == "__main__":
    unittest.main()
