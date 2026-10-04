import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("zotero_search_app", Path(__file__).resolve().parents[1] / "app.py")
app = importlib.util.module_from_spec(spec)
with patch.object(threading.Thread, "start"):
    spec.loader.exec_module(app)


class ZoteroSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch.dict(app.CONFIG, {"zotero_dir": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.connection = sqlite3.connect(self.root / "zotero.sqlite")
        self.addCleanup(self.connection.close)
        self.connection.executescript("""
            CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT, itemTypeID INTEGER);
            CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
            CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
            CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER, orderIndex INTEGER);
            CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER, path TEXT, contentType TEXT);
            INSERT INTO fields VALUES (1, 'title'), (2, 'abstractNote'), (3, 'citationKey'), (4, 'DOI');
            INSERT INTO creators VALUES (1, 'Ada', 'Lovelace');
        """)
        self.add_article(1, "Keyword article", abstract="Unusual abstract", citation="citation-needle", doi="10.123/example")
        self.connection.execute("INSERT INTO itemCreators VALUES (1, 1, 0)")
        pdf_dir = self.root / "storage" / "PDFKEY"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "paper.pdf").write_bytes(b"%PDF-1.4")
        self.connection.execute("INSERT INTO items VALUES (1000, 'PDFKEY', 14)")
        self.connection.execute("INSERT INTO itemAttachments VALUES (1000, 1, 'storage:paper.pdf', 'application/pdf')")
        self.add_article(2, "No attachment article")
        self.add_article(3, "A 100%_literal! title")
        self.connection.commit()

    def add_article(self, item_id, title, abstract="", citation="", doi=""):
        self.connection.execute("INSERT INTO items VALUES (?, ?, 2)", (item_id, f"ITEM{item_id}"))
        for field_id, value in enumerate([title, abstract, citation, doi], 1):
            value_id = item_id * 10 + field_id
            self.connection.execute("INSERT INTO itemDataValues VALUES (?, ?)", (value_id, value))
            self.connection.execute("INSERT INTO itemData VALUES (?, ?, ?)", (item_id, field_id, value_id))

    def test_searches_metadata_and_authors_across_entire_library(self):
        for query in ["Keyword", "Unusual", "citation-needle", "10.123/example", "Ada Lovelace", "Lovelace", "ITEM1"]:
            with self.subTest(query=query):
                items = app.zotero_rows(query)
                self.assertEqual([item["zotero_item_key"] for item in items], ["ITEM1"])
                self.assertTrue(items[0]["has_pdf"])
                self.assertEqual(items[0]["authors"], "Ada Lovelace")

    def test_articles_without_pdf_remain_visible_and_attachments_are_excluded(self):
        items = app.zotero_rows("article")
        self.assertEqual({item["zotero_item_key"] for item in items}, {"ITEM1", "ITEM2"})
        self.assertFalse(next(item for item in items if item["zotero_item_key"] == "ITEM2")["has_pdf"])

    def test_results_are_not_truncated_at_eighty(self):
        for item_id in range(10, 100):
            self.add_article(item_id, f"Many matches {item_id}")
        self.connection.commit()
        self.assertEqual(len(app.zotero_rows("Many matches")), 90)

    def test_keywords_are_trimmed_and_sql_wildcards_are_literal(self):
        self.assertEqual([item["zotero_item_key"] for item in app.zotero_rows("  Keyword  ")], ["ITEM1"])
        for query in ["%", "_", "!", "100%_literal!"]:
            with self.subTest(query=query):
                self.assertEqual([item["zotero_item_key"] for item in app.zotero_rows(query)], ["ITEM3"])
        self.assertEqual(app.zotero_rows("' OR 1=1 --"), [])


if __name__ == "__main__":
    unittest.main()
