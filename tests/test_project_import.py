import importlib.util
import json
import tempfile
import threading
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("zotero_app", Path(__file__).resolve().parents[1] / "app.py")
app = importlib.util.module_from_spec(spec)
with patch.object(threading.Thread, "start"):
    spec.loader.exec_module(app)


class ProjectImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "article.pdf"
        self.pdf.write_bytes(b"%PDF-1.4")
        self.workspace = self.root / "workspaces"
        self.workspace.mkdir()
        self.tasks = Mock()
        for name, value in [("WORKSPACES", self.workspace), ("TASKS", self.tasks)]:
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app.save_folder_index({"version": 1, "folders": [
            {"id": "A", "name": "A"}, {"id": "B", "name": "B"},
        ], "project_folders": {}})
        self.paper = {"path": str(self.pdf), "zotero_item_key": "ITEM1", "citation_key": "article"}

    def existing(self, name="renamed", key="ITEM1", folders=None, status="ready", project_type="paper"):
        directory = self.workspace / name
        directory.mkdir()
        manifest = {"name": name, "project_type": project_type, "citation_key": "old-citation",
                    "source": {"zotero_item_key": key, "path": str(self.pdf)},
                    "tags": ["keep"], "processing": {"status": status}}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        if status == "ready":
            (directory / "full.md").write_text("Existing extraction")
        app.set_project_folders(name, folders or [])
        return directory

    def submit(self, paper=None, folders=None, **options):
        return app.create_project({"project_type": "paper", "papers": [paper or self.paper],
                                   "folder_ids": folders if folders is not None else ["A"],
                                   "skip_existing": True, **options})

    def test_existing_target_project_skipped_without_modification(self):
        directory = self.existing(folders=["A", "B"])
        before = (directory / "manifest.json").read_bytes()
        result = self.submit(tags=["new"], auto_codex=True, translate=True)
        self.assertEqual(result["outcome"], "skipped")
        self.assertEqual(result["task_count"], 0)
        self.assertEqual((directory / "manifest.json").read_bytes(), before)
        self.assertEqual(app.load_folder_index()["project_folders"]["renamed"], ["A", "B"])
        self.tasks.add.assert_not_called()

    def test_failed_or_queued_projects_are_also_skipped(self):
        for status in ["failed", "queued"]:
            with self.subTest(status=status):
                directory = self.existing(name=status, key=status, folders=["A"], status=status)
                result = self.submit({**self.paper, "zotero_item_key": status})
                self.assertEqual(result["outcome"], "skipped")
                self.assertEqual(app.project_manifest(directory)["processing"]["status"], status)
        self.tasks.add.assert_not_called()

    def test_existing_elsewhere_added_to_missing_targets(self):
        self.existing(folders=["B"])
        self.assertEqual(self.submit(folders=["A", "B"])["outcome"], "linked")
        self.assertEqual(app.load_folder_index()["project_folders"]["renamed"], ["B", "A"])
        self.assertEqual(self.submit(folders=["A", "B"])["outcome"], "skipped")
        self.tasks.add.assert_not_called()

    def test_mixed_existing_and_new_papers_continue(self):
        self.existing(folders=["A"])
        self.assertEqual(self.submit()["outcome"], "skipped")
        paper = {**self.paper, "zotero_item_key": "ITEM2", "citation_key": "new-article"}
        self.assertEqual(self.submit(paper)["outcome"], "created")
        self.assertEqual(self.submit(paper)["outcome"], "skipped")
        self.tasks.add.assert_called_once()
        self.assertEqual(app.project_manifest(self.workspace / "new-article")["source"]["zotero_item_key"], "ITEM2")

    def test_concurrent_duplicate_submissions_enqueue_once(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.submit(), range(2)))
        self.assertCountEqual([result["outcome"] for result in results], ["created", "skipped"])
        self.tasks.add.assert_called_once()

    def test_different_zotero_items_are_not_matched_by_citation(self):
        self.existing(name="article", key="OTHER", folders=["A"])
        with self.assertRaisesRegex(ValueError, "项目已存在"):
            self.submit()
        self.tasks.add.assert_not_called()

    def test_collection_does_not_count_as_single_project(self):
        self.existing(project_type="collection", folders=["A"])
        self.assertEqual(self.submit()["outcome"], "created")
        self.tasks.add.assert_called_once()

    def test_legacy_project_falls_back_to_citation_key(self):
        self.existing(key=None, folders=["A"])
        self.assertEqual(self.submit({**self.paper, "citation_key": "old-citation"})["outcome"], "skipped")

    def test_invalid_pdf_and_target_do_not_create_projects(self):
        with self.assertRaisesRegex(ValueError, "PDF 不存在"):
            self.submit({**self.paper, "path": str(self.root / "missing.pdf")})
        with self.assertRaisesRegex(ValueError, "目标工作区目录不存在"):
            self.submit(folders=["missing"])
        self.assertEqual(list(self.workspace.glob("*/manifest.json")), [])
        self.tasks.add.assert_not_called()

    def test_no_target_and_standard_creation_behavior(self):
        self.existing(name="article")
        self.assertEqual(self.submit(folders=[])["outcome"], "skipped")
        with self.assertRaisesRegex(ValueError, "项目已存在"):
            self.submit(skip_existing=False)

    def test_delete_removes_project_files_and_folder_membership(self):
        directory = self.existing(name="to-delete", folders=["A", "B"])
        (directory / "outputs").mkdir()
        (directory / "outputs" / "summary.md").write_text("Generated output")
        task_manager = SimpleNamespace(lock=threading.Lock(), tasks={})
        with patch.object(app, "TASKS", task_manager), patch.object(app, "project_task_state", return_value=[]):
            result = app.delete_project("to-delete")
        self.assertEqual(result["name"], "to-delete")
        self.assertFalse(directory.exists())
        self.assertNotIn("to-delete", app.load_folder_index()["project_folders"])

    def test_delete_rejects_projects_with_active_tasks(self):
        directory = self.existing(name="in-progress", folders=["A"])
        with patch.object(app, "project_task_state", return_value=["cancelling"]):
            with self.assertRaisesRegex(ValueError, "仍有任务处理中"):
                app.delete_project("in-progress")
        self.assertTrue(directory.exists())


if __name__ == "__main__":
    unittest.main()
