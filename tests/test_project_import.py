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

    def test_title_duplicate_is_skipped_only_in_target_folder(self):
        existing = self.existing(name="existing-title", key="OTHER", folders=["A"])
        manifest = app.project_manifest(existing)
        manifest["metadata"] = {"title": "Exact Paper Title"}
        (existing / "manifest.json").write_text(json.dumps(manifest))
        paper = {**self.paper, "zotero_item_key": "NEW", "citation_key": "new-key", "title": "Exact Paper Title"}

        skipped = self.submit(paper, folders=["A"])
        self.assertEqual(skipped["outcome"], "skipped")
        self.assertEqual(skipped["skipped_titles"], ["Exact Paper Title"])
        self.assertFalse((self.workspace / "new-key").exists())

        with patch.object(app, "project_task_state", return_value=[]):
            created = self.submit(paper, folders=["B"])
        self.assertEqual(created["outcome"], "copied")
        self.assertTrue((self.workspace / created["name"]).exists())
        self.assertEqual(app.load_folder_index()["project_folders"][created["name"]], ["B"])

    def test_existing_project_elsewhere_is_copied_without_new_task(self):
        source = self.existing(name="source-paper", key="OTHER", folders=["B"])
        manifest = app.project_manifest(source)
        manifest["metadata"] = {"title": "Copy Me"}
        (source / "manifest.json").write_text(json.dumps(manifest))
        (source / "translation.md").write_text("translation")
        paper = {**self.paper, "zotero_item_key": "NEW", "citation_key": "copy-me", "title": "Copy Me"}
        with patch.object(app, "project_task_state", return_value=[]):
            result = self.submit(paper, folders=["A"])
        self.assertEqual(result["outcome"], "copied")
        copied = self.workspace / result["name"]
        self.assertEqual((copied / "full.md").read_text(), "Existing extraction")
        self.assertEqual((copied / "translation.md").read_text(), "translation")
        self.assertEqual(app.load_folder_index()["project_folders"][result["name"]], ["A"])
        self.tasks.add.assert_not_called()

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

    def test_copy_project_deep_copies_all_artifacts_and_updates_manifest(self):
        source = self.existing(name="source-project", folders=["A"])
        (source / "translation.md").write_text("Translated")
        (source / "images").mkdir()
        (source / "images" / "figure.png").write_bytes(b"image")
        (source / "outputs").mkdir()
        (source / "outputs" / "report.md").write_text("Report")
        manifest = app.project_manifest(source)
        manifest["path"] = str(source)
        (source / "manifest.json").write_text(json.dumps(manifest))
        with patch.object(app, "project_task_state", return_value=[]):
            result = app.copy_project("source-project", ["B"])

        destination = self.workspace / result["name"]

        self.assertEqual(result["name"], "source-project-copy")
        self.assertEqual((destination / "full.md").read_text(), "Existing extraction")
        self.assertEqual((destination / "translation.md").read_text(), "Translated")
        self.assertEqual((destination / "images" / "figure.png").read_bytes(), b"image")
        self.assertEqual((destination / "outputs" / "report.md").read_text(), "Report")
        copied_manifest = app.project_manifest(destination)
        self.assertEqual(copied_manifest["name"], "source-project-copy")
        self.assertEqual(copied_manifest["path"], str(destination.resolve()))
        self.assertEqual(app.load_folder_index()["project_folders"]["source-project-copy"], ["B"])

        (destination / "full.md").write_text("Changed copy")
        (destination / "outputs" / "report.md").unlink()
        self.assertEqual((source / "full.md").read_text(), "Existing extraction")
        self.assertTrue((source / "outputs" / "report.md").exists())

    def test_copy_project_rejects_nested_or_existing_destinations(self):
        source = self.existing(name="source-project")
        with patch.object(app, "project_task_state", return_value=[]):
            with self.assertRaisesRegex(ValueError, "请选择目标工作区目录"):
                app.copy_project("source-project", [])
            with self.assertRaisesRegex(ValueError, "目标工作区目录不存在"):
                app.copy_project("source-project", ["missing"])

    def collection(self, name="bundle", papers=None):
        directory = self.workspace / name
        (directory / "papers").mkdir(parents=True)
        manifest = {
            "schema_version": 1,
            "project_type": "collection",
            "name": name,
            "papers": papers or [],
            "tags": [],
            "codex_workflow": {"enabled": False, "prompt": ""},
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return directory

    def test_append_collection_reuses_existing_single_project_artifacts(self):
        reusable = self.existing(name="single-source", key="ITEM2", folders=[])
        paper = {**self.paper, "zotero_item_key": "ITEM2", "citation_key": "article-2"}
        directory = self.collection()
        task_manager = SimpleNamespace(lock=threading.Lock(), tasks={})
        task_manager.add = lambda task: task_manager.tasks.__setitem__(task["id"], task)
        with patch.object(app, "TASKS", task_manager):
            result = app.add_collection_papers("bundle", {"papers": [paper]})
        self.assertEqual(result["task_count"], 1)
        task = next(iter(task_manager.tasks.values()))
        self.assertEqual(Path(task["reuse_from"]), reusable)
        manifest = app.project_manifest(directory)
        self.assertEqual(manifest["papers"][0]["citation_key"], "article-2")

    def test_append_collection_skips_duplicate_and_delete_removes_one_paper(self):
        paper = {**self.paper, "citation_key": "article"}
        directory = self.collection(papers=[{
            "citation_key": "article", "metadata": paper,
            "source": {"path": str(self.pdf), "zotero_item_key": "ITEM1"},
        }])
        paper_dir = directory / "papers" / "article"
        paper_dir.mkdir()
        (paper_dir / "manifest.json").write_text(json.dumps({"project_type": "paper", "citation_key": "article"}))
        task_manager = SimpleNamespace(lock=threading.Lock(), tasks={})
        task_manager.add = lambda task: task_manager.tasks.__setitem__(task["id"], task)
        with patch.object(app, "TASKS", task_manager):
            result = app.add_collection_papers("bundle", {"papers": [paper]})
            self.assertEqual(result["skipped"], ["article"])
            deleted = app.delete_collection_paper("bundle", "article")
        self.assertEqual(deleted["article_count"], 0)
        self.assertFalse(paper_dir.exists())
        self.assertEqual(app.project_manifest(directory)["papers"], [])


if __name__ == "__main__":
    unittest.main()
