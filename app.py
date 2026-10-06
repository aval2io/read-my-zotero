#!/usr/bin/env python3
"""Local web UI for turning Zotero or arbitrary PDFs into reading workspaces."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web"
DEFAULT_ZOTERO = Path.home() / "Zotero"
DEFAULT_WORKSPACES = ROOT / "read-my-zotero-workspaces"
CONFIG_PATH = ROOT / ".read-my-zotero.json"
DEFAULT_CODEX_PROMPT = "使用 $paper-reading-zh 这个 skill 来阅读 full.md 以及 images/ 并撰写 markdown 报告输出到 outputs/ 中，标题使用论文标题的中文总结"
CODEX_REASONING_EFFORT = "xhigh"
LEGACY_CODEX_PROMPTS = {
    "请使用 $paper-reading-zh 来阅读并撰写总结 md 文件输出到 outputs/ 中，标题使用论文标题的中文总结版",
    "请使用 $paper-reading-zh 来阅读 full.md 并撰写总结 md 文件输出到 outputs/ 中，标题使用论文标题的中文总结版",
}


def normalize_codex_prompt(value: object) -> str:
    prompt = str(value or "").strip()
    return DEFAULT_CODEX_PROMPT if not prompt or prompt in LEGACY_CODEX_PROMPTS else prompt


def load_config() -> dict:
    config = {"zotero_dir": str(DEFAULT_ZOTERO), "workspaces_dir": str(DEFAULT_WORKSPACES), "concurrency": 2, "codex_concurrency": 1}
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


CONFIG = load_config()
WORKSPACES = Path(CONFIG["workspaces_dir"]).expanduser()
WORKSPACES.mkdir(parents=True, exist_ok=True)
IMPORTS = WORKSPACES / ".imports"
IMPORTS.mkdir(parents=True, exist_ok=True)
FOLDER_LOCK = threading.RLock()
PROJECT_CREATE_LOCK = threading.Lock()
FOLDER_INDEX_VERSION = 1
TODO_LOCK = threading.RLock()
TODO_INDEX_VERSION = 1


def folder_index_path() -> Path:
    return WORKSPACES / ".workspace-index.json"


def todo_index_path() -> Path:
    return WORKSPACES / ".todos.json"


def _empty_todo_index() -> dict:
    return {"version": TODO_INDEX_VERSION, "projects": [], "read": []}


def load_todo_index() -> dict:
    with TODO_LOCK:
        try:
            raw = json.loads(todo_index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = _empty_todo_index()
        if not isinstance(raw, dict):
            raw = _empty_todo_index()
        projects = []
        for project in raw.get("projects", []):
            value = str(project or "").strip()
            if value and value not in projects:
                projects.append(value)
        read = []
        for project in raw.get("read", []):
            value = str(project or "").strip()
            if value and value not in projects and value not in read:
                read.append(value)
        return {"version": TODO_INDEX_VERSION, "projects": projects, "read": read}


def save_todo_index(index: dict) -> None:
    with TODO_LOCK:
        target = todo_index_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)


def set_project_todo(project_name: str, state: str) -> str:
    name = slug(project_name)
    if isinstance(state, bool):
        state = "todo" if state else "read"
    with TODO_LOCK:
        index = load_todo_index()
        projects = [project for project in index["projects"] if project != name]
        read = [project for project in index["read"] if project != name]
        if state == "todo":
            projects.append(name)
        elif state == "read":
            read.append(name)
        elif state != "clear":
            raise ValueError("不支持的 TODO 状态")
        index["projects"] = projects
        index["read"] = read
        save_todo_index(index)
        return state


def rename_project_todo(old_name: str, new_name: str) -> None:
    old_slug = slug(old_name)
    new_slug = slug(new_name)
    with TODO_LOCK:
        index = load_todo_index()
        if old_slug not in index["projects"]:
            return
        index["projects"] = [new_slug if project == old_slug else project for project in index["projects"]]
        index["read"] = [new_slug if project == old_slug else project for project in index["read"]]
        index["projects"] = list(dict.fromkeys(index["projects"]))
        index["read"] = list(dict.fromkeys(index["read"]))
        save_todo_index(index)


def remove_project_todo(project_name: str) -> None:
    name = slug(project_name)
    with TODO_LOCK:
        index = load_todo_index()
        updated = [project for project in index["projects"] if project != name]
        updated_read = [project for project in index["read"] if project != name]
        if updated != index["projects"] or updated_read != index["read"]:
            index["projects"] = updated
            index["read"] = updated_read
            save_todo_index(index)


def _empty_folder_index() -> dict:
    return {"version": FOLDER_INDEX_VERSION, "folders": [], "project_folders": {}}


def load_folder_index() -> dict:
    """Load the virtual folder catalog without changing project directories."""
    with FOLDER_LOCK:
        try:
            raw = json.loads(folder_index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = _empty_folder_index()
        if not isinstance(raw, dict):
            raw = _empty_folder_index()
        folders = []
        for folder in raw.get("folders", []):
            if not isinstance(folder, dict) or not folder.get("id") or not str(folder.get("name", "")).strip():
                continue
            folders.append({
                "id": str(folder["id"]),
                "name": str(folder["name"]).strip(),
                "parent_id": str(folder["parent_id"]) if folder.get("parent_id") else None,
                "created_at": folder.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "updated_at": folder.get("updated_at") or folder.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
        folder_ids = {folder["id"] for folder in folders}
        project_folders = {}
        for project, ids in (raw.get("project_folders") or {}).items():
            if not isinstance(ids, list):
                continue
            project_folders[str(project)] = list(dict.fromkeys(str(folder_id) for folder_id in ids if str(folder_id) in folder_ids))
        return {"version": FOLDER_INDEX_VERSION, "folders": folders, "project_folders": project_folders}


def save_folder_index(index: dict) -> None:
    with FOLDER_LOCK:
        target = folder_index_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)


def _folder_name_valid(name: str) -> str:
    value = str(name or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError("文件夹名称不能为空，且不能包含路径分隔符")
    if len(value) > 80:
        raise ValueError("文件夹名称不能超过 80 个字符")
    return value


def _folder_by_id(index: dict, folder_id: str) -> dict:
    for folder in index["folders"]:
        if folder["id"] == folder_id:
            return folder
    raise ValueError("找不到文件夹")


def _folder_descendants(index: dict, folder_id: str) -> set[str]:
    descendants = {folder_id}
    changed = True
    while changed:
        changed = False
        for folder in index["folders"]:
            if folder.get("parent_id") in descendants and folder["id"] not in descendants:
                descendants.add(folder["id"])
                changed = True
    return descendants


def folder_catalog() -> dict:
    index = load_folder_index()
    counts = {}
    for folder in index["folders"]:
        descendants = _folder_descendants(index, folder["id"])
        counts[folder["id"]] = sum(1 for folder_ids in index["project_folders"].values() if descendants.intersection(folder_ids))
    return {"version": index["version"], "folders": index["folders"], "project_folders": index["project_folders"], "counts": counts}


def create_folder(name: str, parent_id: str | None = None) -> dict:
    value = _folder_name_valid(name)
    with FOLDER_LOCK:
        index = load_folder_index()
        if parent_id:
            _folder_by_id(index, parent_id)
        if any(folder["name"].casefold() == value.casefold() and folder.get("parent_id") == parent_id for folder in index["folders"]):
            raise ValueError("同级文件夹已存在")
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        folder = {"id": uuid.uuid4().hex, "name": value, "parent_id": parent_id or None, "created_at": now, "updated_at": now}
        index["folders"].append(folder)
        save_folder_index(index)
        return folder


def rename_folder(folder_id: str, name: str) -> dict:
    value = _folder_name_valid(name)
    with FOLDER_LOCK:
        index = load_folder_index()
        folder = _folder_by_id(index, folder_id)
        if any(item["id"] != folder_id and item["name"].casefold() == value.casefold() and item.get("parent_id") == folder.get("parent_id") for item in index["folders"]):
            raise ValueError("同级文件夹已存在")
        folder["name"] = value
        folder["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        save_folder_index(index)
        return folder


def delete_folder(folder_id: str) -> dict:
    with FOLDER_LOCK:
        index = load_folder_index()
        folder = _folder_by_id(index, folder_id)
        # Removing a folder also removes its child folders. Projects that were
        # assigned anywhere in that subtree simply lose those assignments and
        # therefore appear under “未分类”; no project files are touched.
        removed_ids = _folder_descendants(index, folder_id)
        index["folders"] = [item for item in index["folders"] if item["id"] not in removed_ids]
        affected_projects = 0
        for project, folder_ids in list(index["project_folders"].items()):
            if removed_ids.intersection(folder_ids):
                # Any project in the deleted subtree becomes completely
                # unclassified, even if it also had another folder tag.
                index["project_folders"].pop(project, None)
                affected_projects += 1
        save_folder_index(index)
        return {"id": folder_id, "name": folder["name"], "removed": len(removed_ids), "removed_ids": sorted(removed_ids), "affected_projects": affected_projects}


def set_project_folders(project_name: str, folder_ids: list[str]) -> list[str]:
    name = slug(project_name)
    with FOLDER_LOCK:
        index = load_folder_index()
        available = {folder["id"] for folder in index["folders"]}
        normalized = list(dict.fromkeys(str(folder_id) for folder_id in folder_ids if str(folder_id) in available))
        if normalized:
            index["project_folders"][name] = normalized
        else:
            index["project_folders"].pop(name, None)
        save_folder_index(index)
        return normalized


def add_project_folders(project_name: str, folder_ids: list[str]) -> list[str]:
    """Add folder memberships while keeping the project's existing folders."""
    name = slug(project_name)
    with FOLDER_LOCK:
        index = load_folder_index()
        available = {folder["id"] for folder in index["folders"]}
        current = index["project_folders"].get(name, [])
        additions = [str(folder_id) for folder_id in folder_ids if str(folder_id) in available]
        normalized = list(dict.fromkeys(current + additions))
        if normalized:
            index["project_folders"][name] = normalized
        else:
            index["project_folders"].pop(name, None)
        save_folder_index(index)
        return normalized


def copy_folder_projects(source_id: str, target_ids: list[str]) -> dict:
    """Add every direct project member of one folder to one or more target folders."""
    with FOLDER_LOCK:
        index = load_folder_index()
        _folder_by_id(index, source_id)
        available = {folder["id"] for folder in index["folders"]}
        targets = list(dict.fromkeys(str(folder_id) for folder_id in target_ids if str(folder_id) in available and str(folder_id) != source_id))
        projects = [project for project, folder_ids in index["project_folders"].items() if source_id in folder_ids]
        for project in projects:
            index["project_folders"][project] = list(dict.fromkeys(index["project_folders"].get(project, []) + targets))
        save_folder_index(index)
        return {"source_id": source_id, "target_ids": targets, "project_count": len(projects)}


def rename_project_folders(old_name: str, new_name: str) -> None:
    with FOLDER_LOCK:
        index = load_folder_index()
        if old_name in index["project_folders"]:
            index["project_folders"][new_name] = index["project_folders"].pop(old_name)
            save_folder_index(index)


def remove_project_folders(project_name: str) -> None:
    with FOLDER_LOCK:
        index = load_folder_index()
        if slug(project_name) in index["project_folders"]:
            index["project_folders"].pop(slug(project_name), None)
            save_folder_index(index)


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return value or f"project-{uuid.uuid4().hex[:8]}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_markdown_image_paths(path: Path) -> None:
    """Keep converter image references aligned with the workspace images/ directory."""
    try:
        text = path.read_text(encoding="utf-8")
        normalized = text.replace("./imgs/", "./images/").replace("imgs/", "images/")
        if normalized != text:
            path.write_text(normalized, encoding="utf-8")
    except OSError:
        pass


def copy_converter_images(output_dir: Path, paper_dir: Path) -> int:
    """Copy image assets from pdf2md-zh, which has used both imgs/ and images/."""
    target = paper_dir / "images"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source_dir in (output_dir / "imgs", output_dir / "images"):
        if not source_dir.is_dir():
            continue
        for source in source_dir.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(source_dir)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
                copied += 1
            except OSError:
                continue
    return copied


def sqlite_path() -> Path:
    return Path(CONFIG["zotero_dir"]).expanduser() / "zotero.sqlite"


def db_copy() -> Path | None:
    source = sqlite_path()
    if not source.exists():
        return None
    copy_path = IMPORTS / ".zotero-search.sqlite"
    try:
        shutil.copy2(source, copy_path)
        return copy_path
    except OSError:
        return source


def zotero_connection() -> tuple[sqlite3.Connection | None, Path | None]:
    """Open a consistent read-only Zotero snapshot without writing to the library."""
    source = sqlite_path()
    if not source.exists():
        return None, None
    # Zotero keeps the database in WAL mode. A normal read can block while the
    # desktop app is writing, so prefer SQLite's immutable read-only view and
    # fall back to the legacy copied database for older profiles.
    for uri in (f"file:{source}?mode=ro", f"file:{source}?immutable=1"):
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=0.8)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("SELECT 1").fetchone()
            return connection, None
        except sqlite3.Error:
            if connection is not None:
                connection.close()
    database = db_copy()
    if not database:
        return None, None
    try:
        connection = sqlite3.connect(database, timeout=1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection, database
    except sqlite3.Error:
        return None, database


def close_zotero_connection(connection: sqlite3.Connection | None, temporary: Path | None) -> None:
    if connection is not None:
        connection.close()
    if temporary and temporary != sqlite_path():
        try:
            temporary.unlink()
        except OSError:
            pass


def _zotero_item_rows(connection: sqlite3.Connection, item_ids: list[int] | None = None, query: str = "") -> list[dict]:
    clauses = ["i.itemTypeID NOT IN (14, 15)"]
    params: list[object] = []
    query_params: list[object] = []
    if item_ids is not None:
        if not item_ids:
            return []
        placeholders = ",".join("?" for _ in item_ids)
        clauses.append(f"i.itemID IN ({placeholders})")
        params.extend(item_ids)
    if query:
        like = f"%{query.replace('!', '!!').replace('%', '!%').replace('_', '!_')}%"
        query_params.extend([like] * 6)
    sql = f"""
    SELECT i.itemID, i.key, i.itemTypeID,
      MAX(CASE WHEN f.fieldName='title' THEN v.value END) AS title,
      MAX(CASE WHEN f.fieldName='abstractNote' THEN v.value END) AS abstract,
      MAX(CASE WHEN f.fieldName='date' THEN v.value END) AS date,
      MAX(CASE WHEN f.fieldName='DOI' THEN v.value END) AS doi,
      MAX(CASE WHEN f.fieldName='citationKey' THEN v.value END) AS citation_key
    FROM items i
    LEFT JOIN itemData d ON d.itemID=i.itemID
    LEFT JOIN itemDataValues v ON v.valueID=d.valueID
    LEFT JOIN fields f ON f.fieldID=d.fieldID
    WHERE {' AND '.join(clauses)}
    GROUP BY i.itemID
    {'''HAVING title LIKE ? ESCAPE '!' OR abstract LIKE ? ESCAPE '!'
       OR citation_key LIKE ? ESCAPE '!' OR doi LIKE ? ESCAPE '!'
       OR i.key LIKE ? ESCAPE '!'
       OR EXISTS (SELECT 1 FROM itemCreators search_ic
                  JOIN creators search_c ON search_c.creatorID=search_ic.creatorID
                  WHERE search_ic.itemID=i.itemID
                  AND TRIM(COALESCE(search_c.firstName, '') || ' ' || COALESCE(search_c.lastName, '')) LIKE ? ESCAPE '!')''' if query else ""}
    ORDER BY COALESCE(date, '') DESC, title COLLATE NOCASE
    """
    rows: list[dict] = []
    for raw in connection.execute(sql, params + query_params).fetchall():
        row = dict(raw)
        row["zotero_item_key"] = row["key"]
        creators = connection.execute(
            """SELECT c.firstName, c.lastName FROM itemCreators ic
               JOIN creators c ON c.creatorID=ic.creatorID
               WHERE ic.itemID=? ORDER BY ic.orderIndex""", (row["itemID"],)
        ).fetchall()
        row["authors"] = ", ".join(
            " ".join(part for part in (creator[0], creator[1]) if part).strip() for creator in creators
        )
        attachments = connection.execute(
            """SELECT a.key AS attachment_key, ia.path, ia.contentType
               FROM itemAttachments ia JOIN items a ON a.itemID=ia.itemID
               WHERE ia.parentItemID=? AND ia.contentType='application/pdf'""", (row["itemID"],)
        ).fetchall()
        row["pdfs"] = []
        zotero_root = Path(CONFIG["zotero_dir"]).expanduser()
        for attachment in attachments:
            attachment_path = str(attachment[1] or "")
            if attachment_path.startswith("storage:"):
                attachment_path = attachment_path.removeprefix("storage:")
                candidate = zotero_root / "storage" / attachment[0] / attachment_path
                if candidate.exists():
                    row["pdfs"].append(str(candidate))
            elif attachment_path and Path(attachment_path).expanduser().exists():
                row["pdfs"].append(str(Path(attachment_path).expanduser()))
        row["has_pdf"] = bool(row["pdfs"])
        rows.append(row)
    return rows


def zotero_collections() -> list[dict]:
    connection, temporary = zotero_connection()
    if not connection:
        return []
    try:
        rows = [dict(row) for row in connection.execute(
            "SELECT collectionID, key, collectionName, parentCollectionID, libraryID FROM collections ORDER BY collectionName COLLATE NOCASE"
        ).fetchall()]
        counts = {collection_id: count for collection_id, count in connection.execute(
            """SELECT c.collectionID, COUNT(DISTINCT i.itemID)
               FROM collections c LEFT JOIN collectionItems ci ON ci.collectionID=c.collectionID
               LEFT JOIN items i ON i.itemID=ci.itemID AND i.itemTypeID NOT IN (14, 15)
               GROUP BY c.collectionID"""
        ).fetchall()}
        return [{"id": row["collectionID"], "key": row["key"], "name": row["collectionName"],
                 "parent_id": row["parentCollectionID"], "library_id": row["libraryID"],
                 "item_count": counts.get(row["collectionID"], 0)} for row in rows]
    except sqlite3.Error:
        return []
    finally:
        close_zotero_connection(connection, temporary)


def zotero_collection_items(collection_key: str) -> dict:
    connection, temporary = zotero_connection()
    if not connection:
        raise ValueError("找不到可读取的 Zotero 数据库")
    try:
        collection = connection.execute(
            "SELECT collectionID, key, collectionName, parentCollectionID, libraryID FROM collections WHERE key=?",
            (str(collection_key),),
        ).fetchone()
        if not collection:
            raise ValueError("找不到 Zotero collection")
        item_ids = [int(row[0]) for row in connection.execute(
            "SELECT itemID FROM collectionItems WHERE collectionID=? ORDER BY orderIndex, itemID",
            (collection["collectionID"],),
        ).fetchall()]
        items = _zotero_item_rows(connection, item_ids=item_ids)
        return {"collection": {"id": collection["collectionID"], "key": collection["key"],
                               "name": collection["collectionName"], "parent_id": collection["parentCollectionID"],
                               "library_id": collection["libraryID"]}, "items": items,
                "direct_count": len(items), "pdf_count": sum(1 for item in items if item["has_pdf"])}
    finally:
        close_zotero_connection(connection, temporary)


def zotero_rows(query: str) -> list[dict]:
    connection, temporary = zotero_connection()
    if not connection:
        return []
    try:
        return _zotero_item_rows(connection, query=query.strip())
    except sqlite3.Error:
        return []
    finally:
        close_zotero_connection(connection, temporary)


def project_manifest(project_dir: Path) -> dict | None:
    path = project_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("project_type") == "paper" and not (project_dir / "full.md").exists() and manifest.get("processing", {}).get("status") == "ready":
            manifest.setdefault("processing", {})["status"] = "needs_retry"
        return manifest
    except (OSError, ValueError):
        return None


def timestamp_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def ensure_project_created_at(project_dir: Path, manifest: dict) -> dict:
    """Keep a creation timestamp available for legacy manifests."""
    if manifest.get("created_at"):
        return manifest
    manifest = dict(manifest)
    try:
        fallback = (project_dir / "manifest.json").stat().st_mtime
    except OSError:
        fallback = project_dir.stat().st_mtime
    manifest["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(fallback))
    try:
        (project_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return manifest


def project_output_files(project_dir: Path) -> list[Path]:
    """Return files in a project's root and per-paper outputs directories."""
    output_dirs = [project_dir / "outputs"]
    papers_dir = project_dir / "papers"
    if papers_dir.is_dir():
        output_dirs.extend(path for path in papers_dir.glob("*/outputs") if path.is_dir())
    files: list[Path] = []
    for output_dir in output_dirs:
        if not output_dir.is_dir():
            continue
        try:
            files.extend(path for path in output_dir.rglob("*") if path.is_file())
        except OSError:
            continue
    return files


def project_translation_files(project_dir: Path) -> list[Path]:
    """Return existing translation.md files in a project."""
    candidates = [project_dir / "translation.md"]
    papers_dir = project_dir / "papers"
    if papers_dir.is_dir():
        candidates.extend(path for path in papers_dir.glob("*/translation.md"))
    return [path for path in candidates if path.is_file()]


def list_projects() -> list[dict]:
    projects = []
    folder_data = folder_catalog()
    todo_index = load_todo_index()
    todo_projects = set(todo_index["projects"])
    read_projects = set(todo_index["read"])
    folders_by_id = {folder["id"]: folder for folder in folder_data["folders"]}
    for directory in sorted(WORKSPACES.iterdir() if WORKSPACES.exists() else [], key=lambda item: item.stat().st_mtime, reverse=True):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        manifest = project_manifest(directory)
        if manifest:
            manifest = ensure_project_created_at(directory, manifest)
            mtimes = [directory.stat().st_mtime]
            try:
                mtimes.extend(item.stat().st_mtime for item in directory.rglob("*") if item.is_file())
            except OSError:
                pass
            manifest = dict(manifest)
            manifest["path"] = str(directory)
            manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(max(mtimes)))
            manifest["article_count"] = len(manifest.get("papers", [])) if manifest.get("project_type") == "collection" else 1
            # These values are derived from the filesystem so the project list
            # stays accurate when Codex creates or removes artifacts directly.
            manifest["output_count"] = len(project_output_files(directory))
            manifest["translation_exists"] = bool(project_translation_files(directory))
            folder_ids = folder_data["project_folders"].get(manifest.get("name") or directory.name, [])
            manifest["folder_ids"] = folder_ids
            manifest["folders"] = [folders_by_id[folder_id] for folder_id in folder_ids if folder_id in folders_by_id]
            manifest["todo"] = (manifest.get("name") or directory.name) in todo_projects
            manifest["todo_read"] = (manifest.get("name") or directory.name) in read_projects
            projects.append(manifest)
    return projects


def safe_project_dir(name: str) -> Path:
    """Resolve a project directory and prevent paths outside the workspace root."""
    project_dir = (WORKSPACES / slug(name)).resolve()
    workspace_root = WORKSPACES.resolve()
    try:
        project_dir.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("项目路径不在工作区目录内") from exc
    return project_dir


def project_task_state(name: str) -> list[str]:
    with TASKS.lock:
        states = [task.get("status", "") for task in TASKS.tasks.values() if task.get("project_slug") == name]
    codex_runs = globals().get("CODEX_RUNS")
    if codex_runs is not None:
        with codex_runs.lock:
            states.extend(run.get("status", "") for run in codex_runs.runs.values() if run.get("project_name") == name)
    return states


def rename_project(old_name: str, new_name: str) -> dict:
    old_slug = slug(old_name)
    new_slug = slug(new_name)
    if not new_slug or new_slug == old_slug:
        raise ValueError("请输入不同的项目名称")
    old_dir = safe_project_dir(old_slug)
    new_dir = safe_project_dir(new_slug)
    if not old_dir.is_dir() or not project_manifest(old_dir):
        raise ValueError(f"找不到项目：{old_slug}")
    if new_dir.exists():
        raise ValueError(f"项目已存在：{new_slug}")
    states = project_task_state(old_slug)
    if any(state in {"queued", "running"} for state in states):
        raise ValueError("项目仍有任务处理中，请等待任务完成后再重命名")
    shutil.move(str(old_dir), str(new_dir))
    old_path = str(old_dir)
    new_path = str(new_dir)
    for manifest_path in new_dir.rglob("manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("name") == old_slug:
                manifest["name"] = new_slug
            if manifest.get("path") == old_path or str(manifest.get("path", "")).startswith(old_path + os.sep):
                manifest["path"] = new_path + str(manifest["path"])[len(old_path):]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            continue
    with TASKS.lock:
        for task in TASKS.tasks.values():
            if task.get("project_slug") == old_slug:
                task["project_slug"] = new_slug
    rename_project_folders(old_slug, new_slug)
    rename_project_todo(old_slug, new_slug)
    return {"name": new_slug, "path": new_path}


def delete_project(name: str) -> dict:
    project_slug = slug(name)
    project_dir = safe_project_dir(project_slug)
    if not project_dir.is_dir() or not project_manifest(project_dir):
        raise ValueError(f"找不到项目：{project_slug}")
    states = project_task_state(project_slug)
    if any(state in {"queued", "running", "cancelling"} for state in states):
        raise ValueError("项目仍有任务处理中，请等待任务完成后再删除")
    shutil.rmtree(project_dir)
    with TASKS.lock:
        TASKS.tasks = {task_id: task for task_id, task in TASKS.tasks.items() if task.get("project_slug") != project_slug}
    remove_project_folders(project_slug)
    remove_project_todo(project_slug)
    return {"name": project_slug}


def extract_markdown_abstract(markdown_path: Path) -> str:
    """Extract an Abstract section when the source metadata has no abstract."""
    try:
        text = markdown_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(
        r"(?ims)^#{1,6}\s*(?:abstract|摘要)\s*$\n?(.*?)(?=^#{1,6}\s+|\Z)",
        text,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def metadata_for_paper(manifest: dict, paper_dir: Path) -> dict:
    metadata = dict(manifest.get("metadata") or {})
    if not metadata.get("abstract"):
        abstract = extract_markdown_abstract(paper_dir / "full.md")
        if abstract:
            metadata["abstract"] = abstract
            metadata["abstract_source"] = "full.md"
    return metadata


def find_reusable_artifacts(source: Path, citation_key: str) -> Path | None:
    """Find an existing processed paper directory to reuse in a new project."""
    source_resolved = source.expanduser().resolve()
    source_hash: str | None = None
    for directory in WORKSPACES.iterdir() if WORKSPACES.exists() else []:
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        root = project_manifest(directory)
        if not root:
            continue
        candidates: list[Path] = []
        if root.get("project_type") == "paper":
            candidates.append(directory)
        elif root.get("project_type") == "collection":
            papers_dir = directory / "papers"
            candidates.extend(child for child in papers_dir.iterdir() if child.is_dir()) if papers_dir.exists() else None
        for candidate in candidates:
            manifest = project_manifest(candidate)
            full = candidate / "full.md"
            if not manifest or not full.exists():
                continue
            candidate_source = Path(manifest.get("source", {}).get("path", "")).expanduser()
            same_path = False
            try:
                same_path = candidate_source.resolve() == source_resolved
            except OSError:
                pass
            same_key = manifest.get("citation_key") == citation_key
            same_hash = False
            stored_hash = manifest.get("source", {}).get("sha256")
            if stored_hash:
                if source_hash is None:
                    try:
                        source_hash = sha256(source)
                    except OSError:
                        source_hash = ""
                same_hash = bool(source_hash) and source_hash == stored_hash
            if same_path or same_key or same_hash:
                return candidate
    return None


def project_details(project_name: str) -> dict:
    name = slug(project_name)
    project_dir = WORKSPACES / name
    root = project_manifest(project_dir)
    if not root:
        raise ValueError(f"找不到项目：{name}")
    root = ensure_project_created_at(project_dir, root)
    papers: list[dict] = []
    if root.get("project_type") == "paper":
        papers.append({
            "citation_key": root.get("citation_key") or name,
            "path": str(project_dir),
            "metadata": metadata_for_paper(root, project_dir),
            "full_exists": (project_dir / "full.md").exists(),
            "translation_exists": (project_dir / "translation.md").exists(),
            "translation_path": "translation.md" if (project_dir / "translation.md").is_file() else None,
        })
    else:
        for item in root.get("papers", []):
            citation_key = item.get("citation_key", "")
            paper_dir = project_dir / "papers" / citation_key
            child = project_manifest(paper_dir) or {}
            papers.append({
                "citation_key": citation_key,
                "path": str(paper_dir),
                "metadata": metadata_for_paper(child or item, paper_dir),
                "full_exists": (paper_dir / "full.md").exists(),
                "translation_exists": (paper_dir / "translation.md").exists(),
                "translation_path": f"papers/{citation_key}/translation.md" if (paper_dir / "translation.md").is_file() else None,
            })
    folder_data = folder_catalog()
    folder_ids = folder_data["project_folders"].get(name, [])
    root = dict(root)
    root["folder_ids"] = folder_ids
    root["folders"] = [folder for folder in folder_data["folders"] if folder["id"] in folder_ids]
    root["output_count"] = len(project_output_files(project_dir))
    root["translation_exists"] = bool(project_translation_files(project_dir))
    root["translation_path"] = "translation.md" if (project_dir / "translation.md").is_file() else None
    return {"project": root, "path": str(project_dir), "papers": papers}


class TaskManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.capacity = threading.Condition(self.lock)
        self.active = 0
        self.concurrency = 1
        self.tasks: dict[str, dict] = {}
        self.pending: queue.Queue[str] = queue.Queue()
        self.workers: list[threading.Thread] = []
        self.resize(int(CONFIG.get("concurrency", 2)))

    def resize(self, count: int) -> None:
        count = max(1, min(int(count), 8))
        with self.capacity:
            self.concurrency = count
            while len(self.workers) < count:
                worker = threading.Thread(target=self._worker, daemon=True)
                self.workers.append(worker)
                worker.start()
            self.capacity.notify_all()

    def add(self, task: dict) -> None:
        with self.lock:
            self.tasks[task["id"]] = task
        self.pending.put(task["id"])

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(reversed(list(self.tasks.values())))

    def _update(self, task_id: str, **changes: object) -> None:
        with self.lock:
            if task_id in self.tasks:
                self.tasks[task_id].update(changes)

    def _worker(self) -> None:
        while True:
            task_id = self.pending.get()
            with self.capacity:
                while self.active >= self.concurrency:
                    self.capacity.wait()
                task = self.tasks.get(task_id)
                if task:
                    self.active += 1
            if not task:
                self.pending.task_done()
                continue
            self._update(task_id, status="running", progress=8, message="准备处理 PDF")
            try:
                self._run(task)
            except Exception as exc:  # pragma: no cover - defensive UI boundary
                self._mark_manifest_failed(task, str(exc))
                self._update(task_id, status="failed", message=str(exc))
            finally:
                with self.capacity:
                    self.active -= 1
                    self.capacity.notify_all()
                self.pending.task_done()

    def _mark_manifest_failed(self, task: dict, message: str) -> None:
        project_dir = WORKSPACES / task["project_slug"]
        paper_dir = project_dir / "papers" / task["paper_slug"] if task["project_type"] == "collection" else project_dir
        manifest_path = paper_dir / "manifest.json"
        if not manifest_path.exists():
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.setdefault("processing", {})["status"] = "failed"
            manifest["processing"]["error"] = message
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _run(self, task: dict) -> None:
        project_dir = WORKSPACES / task["project_slug"]
        paper_dir = project_dir / "papers" / task["paper_slug"] if task["project_type"] == "collection" else project_dir
        paper_dir.mkdir(parents=True, exist_ok=True)
        (paper_dir / "images").mkdir(exist_ok=True)
        (paper_dir / "outputs").mkdir(exist_ok=True)
        source = Path(task["source_path"])
        self._update(task["id"], progress=18, message="读取源文件并写入 manifest")
        metadata = task.get("metadata") or {}
        source_hash = sha256(source)
        existing_manifest = project_manifest(project_dir) or {}
        manifest = {
            "schema_version": 1,
            "project_type": task["project_type"],
            "name": task["project_slug"],
            "path": str(project_dir),
            "created_at": existing_manifest.get("created_at") or task.get("created_at") or timestamp_now(),
            "citation_key": task["paper_slug"],
            "tags": task.get("tags", []),
            "source": {"path": str(source), "sha256": source_hash, "zotero_item_key": metadata.get("zotero_item_key")},
            "metadata": metadata,
            "artifacts": {"full": "full.md", "translation": "translation.md", "images": "images/", "outputs": "outputs/"},
            "processing": {"status": "ready", "converter": "pdf2md-zh", "options": task.get("options", {})},
        }
        if task.get("codex_workflow"):
            manifest["codex_workflow"] = task["codex_workflow"]
        (paper_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._update(task["id"], progress=36, message="生成可供 Agent 阅读的 Markdown")
        title = metadata.get("title") or source.stem
        full = paper_dir / "full.md"
        reuse_from = Path(task["reuse_from"]) if task.get("reuse_from") else None
        if reuse_from and (reuse_from / "full.md").exists() and not full.exists():
            self._update(task["id"], progress=72, message="复用已有 OCR 结果")
            shutil.copy2(reuse_from / "full.md", full)
            normalize_markdown_image_paths(full)
            if (reuse_from / "translation.md").exists():
                translation_path = paper_dir / "translation.md"
                shutil.copy2(reuse_from / "translation.md", translation_path)
                normalize_markdown_image_paths(translation_path)
            copied_images = 0
            for image_dir in (reuse_from / "images", reuse_from / "imgs"):
                if not image_dir.is_dir():
                    continue
                for image in image_dir.rglob("*"):
                    if not image.is_file():
                        continue
                    destination = paper_dir / "images" / image.relative_to(image_dir)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(image, destination)
                    copied_images += 1
            self._update(task["id"], progress=90, message=f"已复用 {copied_images} 张图片")
            self._update(task["id"], progress=100, status="completed", message="已复用已有结果，项目就绪", project_path=str(project_dir))
            return
        converter_dir = Path(os.environ.get("PDF2MD_ZH_DIR", str(ROOT.parent / "pdf2md-zh"))).expanduser()
        converter_entry = converter_dir / "run.py"
        converter_ready = converter_entry.exists() and (os.environ.get("PADDLE_API_TOKEN") or (converter_dir / ".env").exists())
        if converter_ready and (not full.exists() or task.get("force")):
            output_dir = paper_dir / ".pdf2md-output"
            shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(exist_ok=True)
            python_bin = converter_dir / ".venv" / "bin" / "python"
            if not python_bin.exists():
                python_bin = Path(sys.executable)
            command = [str(python_bin), str(converter_entry), str(source), "--pure", "--save-token", "-o", str(output_dir)]
            if task.get("translate"):
                command.remove("--pure")
            run_env = os.environ.copy()
            source_dir = str(converter_dir / "src")
            run_env["PYTHONPATH"] = source_dir + os.pathsep + run_env.get("PYTHONPATH", "")
            self._update(task["id"], progress=48, message="正在运行 pdf2md-zh OCR / 翻译")
            try:
                completed = subprocess.run(
                    command,
                    cwd=converter_dir,
                    env=run_env,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )
                if completed.returncode != 0:
                    raise RuntimeError((completed.stderr or completed.stdout or "pdf2md-zh 处理失败").strip()[-1000:])
                original = next(output_dir.glob("group_full*.md"), None)
                translated = next(output_dir.glob("group_trans_full*.md"), None)
                if original:
                    shutil.copy2(original, full)
                    normalize_markdown_image_paths(full)
                if translated:
                    translation_path = paper_dir / "translation.md"
                    shutil.copy2(translated, translation_path)
                    normalize_markdown_image_paths(translation_path)
                copied_images = copy_converter_images(output_dir, paper_dir)
                self._update(task["id"], progress=90, message=f"已复制 {copied_images} 张图片")
            finally:
                shutil.rmtree(output_dir, ignore_errors=True)
        elif not full.exists() or task.get("force"):
            full.write_text(
                f"# {title}\n\n> Source PDF: `{source}`\n\n"
                "This workspace is ready for PDF conversion. Configure `PADDLE_API_TOKEN` and `PDF2MD_ZH_DIR` to run pdf2md-zh automatically.\n",
                encoding="utf-8",
            )
        if task.get("translate") and not (paper_dir / "translation.md").exists():
            (paper_dir / "translation.md").write_text(f"# {title}\n\nTranslation will be generated after OCR completes.\n", encoding="utf-8")
        self._update(task["id"], progress=100, status="completed", message="项目已就绪", project_path=str(project_dir))
        if task["project_type"] == "collection":
            with PROJECT_CREATE_LOCK:
                root_manifest = project_manifest(project_dir) or {}
                root_manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                (project_dir / "manifest.json").write_text(json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8")


TASKS = TaskManager()


TEXT_OUTPUT_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".csv", ".yaml", ".yml"}


def resolve_codex_bin() -> str:
    """Find Codex when the macOS launcher starts Python without the user's shell PATH."""
    configured = str(CONFIG.get("codex_bin", "") or os.environ.get("CODEX_BIN", "")).strip()
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            found = shutil.which(configured)
            if found:
                candidates.append(Path(found))
    found = shutil.which("codex")
    if found:
        candidates.append(Path(found))
    candidates.extend([
        Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
        Path.home() / ".local/bin/codex",
        Path("/opt/homebrew/bin/codex"),
        Path("/usr/local/bin/codex"),
    ])
    fnm_root = Path.home() / ".local/share/fnm/node-versions"
    if fnm_root.exists():
        candidates.extend(sorted(fnm_root.glob("*/installation/bin/codex"), key=lambda item: item.stat().st_mtime, reverse=True))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise FileNotFoundError(
        "找不到 Codex CLI。请先在终端运行 `command -v codex`，或在启动前设置 CODEX_BIN=/绝对路径/codex"
    )


def resolve_vscode_bin() -> str | None:
    """Find the VS Code CLI when the app launcher does not inherit shell PATH."""
    configured = str(CONFIG.get("vscode_bin", "") or os.environ.get("VSCODE_BIN", "")).strip()
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            found = shutil.which(configured)
            if found:
                candidates.append(Path(found))
    found = shutil.which("code")
    if found:
        candidates.append(Path(found))
    candidates.extend([
        Path("/usr/local/bin/code"),
        Path("/opt/homebrew/bin/code"),
        Path.home() / ".local/bin/code",
        Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
        Path("/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code"),
        Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    ])
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def output_root(project_name: str) -> Path:
    project_dir = safe_project_dir(project_name)
    manifest = project_manifest(project_dir)
    if not manifest:
        raise ValueError(f"找不到项目：{slug(project_name)}")
    root = project_dir / "outputs"
    root.mkdir(exist_ok=True)
    return root


def list_project_outputs(project_name: str) -> list[dict]:
    root = output_root(project_name)
    project_dir = root.parent
    output_dirs = [root]
    papers_dir = project_dir / "papers"
    if papers_dir.is_dir():
        output_dirs.extend(path for path in papers_dir.glob("*/outputs") if path.is_dir())
    outputs = []
    for output_dir in output_dirs:
        for path in sorted(output_dir.rglob("*"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix() if output_dir == root else path.relative_to(project_dir).as_posix()
            stat = path.stat()
            outputs.append({"path": relative, "name": path.name, "size": stat.st_size,
                            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime))})
    return outputs


def read_project_output(project_name: str, relative_path: str) -> dict:
    root = output_root(project_name).resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("产出文件路径无效") from exc
    if not candidate.is_file() or candidate.suffix.lower() not in TEXT_OUTPUT_EXTENSIONS:
        raise ValueError("只支持读取 outputs/ 下的文本产出")
    if candidate.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("产出文件超过 2 MB，暂不支持在线预览")
    return {"path": candidate.relative_to(root).as_posix(), "name": candidate.name,
            "content": candidate.read_text(encoding="utf-8", errors="replace")}


def project_output_path(project_name: str, relative_path: str) -> Path:
    """Resolve a user-visible output path without allowing traversal outside outputs/."""
    root = output_root(project_name).resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("产出文件路径无效") from exc
    if not candidate.is_file():
        raise ValueError("产出文件不存在")
    return candidate


def project_translation_path(project_name: str, relative_path: str) -> Path:
    """Resolve an existing translation.md path inside a project."""
    project_dir = safe_project_dir(project_name).resolve()
    if not project_manifest(project_dir):
        raise ValueError(f"找不到项目：{slug(project_name)}")
    candidate = (project_dir / str(relative_path or "")).resolve()
    try:
        candidate.relative_to(project_dir)
    except ValueError as exc:
        raise ValueError("翻译文件路径无效") from exc
    if candidate.name != "translation.md" or not candidate.is_file():
        raise ValueError("翻译文件不存在")
    return candidate


class CodexRunManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.capacity = threading.Condition(self.lock)
        self.runs: dict[str, dict] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.pending: queue.Queue[str] = queue.Queue()
        self.concurrency = max(1, min(int(CONFIG.get("codex_concurrency", 1)), 8))
        self.storage_path = WORKSPACES / ".codex-runs.json"
        self._load()
        self.workers: list[threading.Thread] = []
        self.active = 0
        self.resize(self.concurrency)

    def resize(self, count: int) -> None:
        count = max(1, min(int(count), 8))
        with self.capacity:
            self.concurrency = count
            while len(self.workers) < count:
                worker = threading.Thread(target=self._worker, daemon=True)
                self.workers.append(worker)
                worker.start()
            self.capacity.notify_all()

    def _load(self) -> None:
        try:
            saved = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if isinstance(saved, list):
                for run in saved[-100:]:
                    if isinstance(run, dict) and run.get("id"):
                        if run.get("status") in {"queued", "running", "cancelling"}:
                            run["status"] = "interrupted"
                            run["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                            run["log"] = (run.get("log", "") + "\n[read-my-zotero] 服务重启，任务已中断。\n")[-120000:]
                        self.runs[run["id"]] = run
        except (OSError, ValueError, TypeError):
            pass

    def _save_locked(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.storage_path.with_suffix(".tmp")
            temp.write_text(json.dumps(list(self.runs.values())[-100:], ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.storage_path)
        except OSError:
            pass

    def add(self, project_name: str, prompt: str, origin: str = "manual", working_dir: Path | None = None,
            paper_slug: str | None = None) -> dict:
        name = slug(project_name)
        project_root = safe_project_dir(name)
        if not project_manifest(project_root):
            raise ValueError(f"找不到项目：{name}")
        project_dir = project_root if working_dir is None else Path(working_dir).expanduser().resolve()
        try:
            project_dir.relative_to(project_root.resolve())
        except ValueError as exc:
            raise ValueError("Codex 工作目录必须位于项目目录内") from exc
        if not project_dir.is_dir():
            raise ValueError(f"找不到 Codex 工作目录：{project_dir}")
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("请输入提示词")
        run = {"id": uuid.uuid4().hex, "project_name": name, "project_path": str(project_dir),
               "prompt": prompt, "status": "queued", "log": "", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "started_at": None, "finished_at": None, "exit_code": None, "outputs": [], "origin": origin}
        if paper_slug:
            run["paper_slug"] = paper_slug
        with self.lock:
            self.runs[run["id"]] = run
            self._save_locked()
        self.pending.put(run["id"])
        return dict(run)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [dict(run) for run in reversed(list(self.runs.values()))]

    def project_status(self, project_name: str) -> str | None:
        with self.lock:
            matching = [run for run in self.runs.values() if run.get("project_name") == project_name]
        active = [run for run in matching if run.get("status") in {"queued", "running", "cancelling"}]
        if active:
            return "running"
        return None

    def get(self, run_id: str) -> dict:
        with self.lock:
            run = self.runs.get(run_id)
            if not run:
                raise ValueError("找不到 Codex 运行任务")
            return dict(run)

    def cancel(self, run_id: str) -> dict:
        with self.lock:
            run = self.runs.get(run_id)
            process = self.processes.get(run_id)
            if not run:
                raise ValueError("找不到 Codex 运行任务")
            if run["status"] == "queued":
                run["status"] = "cancelled"
                run["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            elif run["status"] == "running" and process:
                run["status"] = "cancelling"
                process.terminate()
            self._save_locked()
            return dict(run)

    def _update(self, run_id: str, **changes: object) -> None:
        with self.lock:
            if run_id in self.runs:
                self.runs[run_id].update(changes)
                self._save_locked()

    def _append_log(self, run_id: str, line: str) -> None:
        if not line:
            return
        with self.lock:
            run = self.runs.get(run_id)
            if run:
                run["log"] = (run.get("log", "") + line.rstrip() + "\n")[-120000:]
                self._save_locked()

    def _worker(self) -> None:
        while True:
            run_id = self.pending.get()
            slot_acquired = False
            try:
                with self.lock:
                    run = self.runs.get(run_id)
                if not run or run.get("status") == "cancelled":
                    continue
                with self.capacity:
                    while self.active >= self.concurrency:
                        self.capacity.wait()
                        run = self.runs.get(run_id)
                        if not run or run.get("status") == "cancelled":
                            break
                    if not run or run.get("status") == "cancelled":
                        continue
                    self.active += 1
                    slot_acquired = True
                self._execute(run)
            except Exception as exc:
                self._append_log(run_id, f"[read-my-zotero] {exc}")
                self._update(run_id, status="failed", finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), exit_code=-1)
            finally:
                with self.capacity:
                    if slot_acquired:
                        self.active -= 1
                    self.capacity.notify_all()
                self.pending.task_done()

    def _execute(self, run: dict) -> None:
        run_id = run["id"]
        project_dir = Path(run["project_path"])
        codex_bin = resolve_codex_bin()
        # The CLI's non-interactive mode needs approvals disabled; the project cwd is still fixed
        # to the selected workspace and the prompt is passed via stdin rather than a shell string.
        command = [codex_bin, "exec", "--json", "--skip-git-repo-check", "--cd", str(project_dir),
                   "--dangerously-bypass-approvals-and-sandbox", "-c",
                   f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"', "-"]
        self._update(run_id, status="running", started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self._append_log(run_id, f"$ {codex_bin} exec --json --cd {project_dir} -c model_reasoning_effort=\"{CODEX_REASONING_EFFORT}\"")
        try:
            process = subprocess.Popen(command, cwd=project_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as exc:
            raise RuntimeError(f"无法启动 Codex CLI：{exc}") from exc
        with self.lock:
            self.processes[run_id] = process
        assert process.stdin is not None
        process.stdin.write(run["prompt"] + "\n")
        process.stdin.close()
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            try:
                event = json.loads(line)
                event_type = event.get("type", "")
                session_id = event.get("thread_id") or event.get("session_id")
                if session_id:
                    self._update(run_id, session_id=str(session_id))
                if event_type == "item.completed" and isinstance(event.get("item"), dict):
                    text = event["item"].get("text") or event["item"].get("content")
                    if text:
                        self._append_log(run_id, str(text))
                elif event_type in {"thread.started", "turn.started", "turn.completed", "error"}:
                    self._append_log(run_id, json.dumps(event, ensure_ascii=False))
                elif not event_type:
                    self._append_log(run_id, line)
            except json.JSONDecodeError:
                self._append_log(run_id, line)
        return_code = process.wait()
        with self.lock:
            self.processes.pop(run_id, None)
            current_status = self.runs.get(run_id, {}).get("status")
        final_status = "cancelled" if current_status in {"cancelling", "cancelled"} else ("completed" if return_code == 0 else "failed")
        outputs = []
        try:
            outputs = list_project_outputs(run["project_name"])
        except ValueError:
            pass
        if final_status == "completed" and run.get("origin") == "auto" and not outputs:
            final_status = "failed"
            self._append_log(run_id, "[read-my-zotero] Codex 已退出，但没有在 outputs/ 中生成文件。")
        self._update(run_id, status=final_status, exit_code=return_code,
                     finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), outputs=outputs)

    def command_for(self, run_id: str) -> str:
        run = self.get(run_id)
        session_id = run.get("session_id")
        if not session_id:
            return f"cd {shlex.quote(run['project_path'])} && codex exec -c 'model_reasoning_effort=\"{CODEX_REASONING_EFFORT}\"' -"
        return f"cd {shlex.quote(run['project_path'])} && codex -c 'model_reasoning_effort=\"{CODEX_REASONING_EFFORT}\"' resume {shlex.quote(str(session_id))}"

    def project_runs(self, project_name: str) -> list[dict]:
        name = slug(project_name)
        project_dir = safe_project_dir(name)
        if not project_manifest(project_dir):
            raise ValueError(f"找不到项目：{name}")
        with self.lock:
            runs = [dict(run) for run in self.runs.values() if run.get("project_name") == name]
        current_outputs = list_project_outputs(name)
        for run in runs:
            run["log"] = str(run.get("log", ""))[-40000:]
            run["outputs"] = current_outputs
            run["command"] = self.command_for(run["id"])
        return list(reversed(runs))


CODEX_RUNS = CodexRunManager()
AUTO_CODEX_LOCK = threading.Lock()
AUTO_CODEX_PENDING: set[str] = set()


def maybe_start_project_codex(project_name: str) -> bool:
    """Start automatic Codex runs only after every extraction artifact is ready."""
    name = slug(project_name)
    project_dir = WORKSPACES / name
    manifest = project_manifest(project_dir)
    workflow = (manifest or {}).get("codex_workflow") or {}
    if not workflow.get("enabled"):
        return False
    with TASKS.lock:
        extraction = [task for task in TASKS.tasks.values() if task.get("project_slug") == name and task.get("status") != "superseded"]
    if not extraction or any(task.get("status") in {"queued", "running", "cancelling"} for task in extraction):
        return False
    if any(task.get("status") != "completed" for task in extraction):
        return False
    if not all(
        (project_dir / "papers" / task["paper_slug"] if task.get("project_type") == "collection" else project_dir).joinpath("full.md").is_file()
        and (project_dir / "papers" / task["paper_slug"] if task.get("project_type") == "collection" else project_dir).joinpath("images").is_dir()
        for task in extraction
    ):
        return False
    with AUTO_CODEX_LOCK:
        with CODEX_RUNS.lock:
            existing = [run for run in CODEX_RUNS.runs.values() if run.get("project_name") == name and run.get("origin") == "auto" and run.get("status") in {"queued", "running", "cancelling", "completed"}]
        prompt = normalize_codex_prompt(workflow.get("prompt"))
        project_type = manifest.get("project_type")
        if project_type == "collection":
            existing_papers = {run.get("paper_slug") for run in existing}
            for task in extraction:
                paper_slug = task["paper_slug"]
                if paper_slug in existing_papers:
                    continue
                working_dir = project_dir / "papers" / paper_slug
                CODEX_RUNS.add(name, prompt, origin="auto", working_dir=working_dir, paper_slug=paper_slug)
        elif not existing:
            CODEX_RUNS.add(name, prompt, origin="auto")
        return True


def schedule_project_codex(project_name: str) -> None:
    """Wait briefly for filesystem writes and the whole extraction batch before starting Codex."""
    name = slug(project_name)
    with AUTO_CODEX_LOCK:
        if name in AUTO_CODEX_PENDING:
            return
        AUTO_CODEX_PENDING.add(name)

    def wait_and_start() -> None:
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if maybe_start_project_codex(name):
                    return
                with TASKS.lock:
                    extraction = [task for task in TASKS.tasks.values() if task.get("project_slug") == name and task.get("status") != "superseded"]
                if extraction and any(task.get("status") == "failed" for task in extraction):
                    return
                time.sleep(0.25)
        finally:
            with AUTO_CODEX_LOCK:
                AUTO_CODEX_PENDING.discard(name)

    threading.Thread(target=wait_and_start, name=f"auto-codex-{name}", daemon=True).start()


def create_project(payload: dict) -> dict:
    with PROJECT_CREATE_LOCK:
        return _create_project(payload)


def matching_paper_project(paper: dict) -> tuple[str, dict] | None:
    item_key = paper.get("zotero_item_key")
    citation_key = paper.get("citation_key")
    source_path = Path(paper["path"]).expanduser().resolve()
    for directory in WORKSPACES.iterdir():
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        manifest = project_manifest(directory)
        if not manifest or manifest.get("project_type") != "paper":
            continue
        source = manifest.get("source") or {}
        metadata = manifest.get("metadata") or {}
        existing_key = source.get("zotero_item_key") or metadata.get("zotero_item_key")
        if item_key and existing_key:
            matches = item_key == existing_key
        elif citation_key and (manifest.get("citation_key") or metadata.get("citation_key")):
            matches = slug(citation_key) == slug(manifest.get("citation_key") or metadata["citation_key"])
        else:
            matches = bool(source.get("path")) and Path(source["path"]).expanduser().resolve() == source_path
        if matches:
            return directory.name, manifest
    return None


def _create_project(payload: dict) -> dict:
    project_type = payload.get("project_type", "paper")
    papers = payload.get("papers", [])
    if not papers:
        raise ValueError("至少选择一篇 PDF")
    requested_name = str(payload.get("name") or "").strip()
    requested_folder_ids = payload.get("folder_ids") or []
    if not isinstance(requested_folder_ids, list):
        requested_folder_ids = []
    if project_type == "paper" and payload.get("skip_existing"):
        if len(papers) != 1:
            raise ValueError("自动跳过已有项目时，每次只能创建一个单篇项目")
        index = load_folder_index()
        available = {folder["id"] for folder in index["folders"]}
        if any(str(folder_id) not in available for folder_id in requested_folder_ids):
            raise ValueError("目标工作区目录不存在，请刷新后重试")
        match = matching_paper_project(papers[0])
        if match:
            existing_name, _ = match
            current = index["project_folders"].get(existing_name, [])
            missing = [str(folder_id) for folder_id in requested_folder_ids if str(folder_id) not in current]
            if missing:
                add_project_folders(existing_name, missing)
            return {"name": existing_name, "path": str(WORKSPACES / existing_name), "task_count": 0, "outcome": "linked" if missing else "skipped"}
    for paper in papers:
        path = Path(paper["path"]).expanduser()
        if not path.exists() or path.suffix.lower() != ".pdf":
            raise ValueError(f"PDF 不存在：{path}")
    auto_codex = bool(payload.get("auto_codex"))
    codex_prompt = normalize_codex_prompt(payload.get("codex_prompt"))
    default_key = papers[0].get("citation_key") or Path(papers[0]["path"]).stem
    name = slug(requested_name or (f"{default_key}-workspace" if project_type == "collection" else default_key))
    if project_type == "collection":
        base_name = name
        counter = 2
        while (WORKSPACES / name).exists():
            occupied = project_manifest(WORKSPACES / name)
            if not occupied or occupied.get("project_type") != "paper":
                raise ValueError(f"多篇项目已存在：{name}")
            name = f"{base_name}-workspace" if counter == 2 and requested_name else f"{base_name}-{counter}"
            counter += 1
    project_dir = WORKSPACES / name
    existing_manifest = project_manifest(project_dir) if project_dir.exists() else None
    if project_dir.exists() and existing_manifest:
        if project_type != existing_manifest.get("project_type"):
            raise ValueError(f"项目已存在：{name}")
        existing_papers = existing_manifest.get("papers", [])
        if project_type == "paper":
            existing_full = project_dir / "full.md"
            can_retry = not existing_full.exists() or existing_manifest.get("processing", {}).get("status") == "failed"
        else:
            existing_full = [project_dir / "papers" / item.get("citation_key", "") / "full.md" for item in existing_papers]
            can_retry = any(not path.exists() for path in existing_full)
        if not can_retry:
            raise ValueError(f"项目已存在：{name}")
    elif project_dir.exists():
        raise ValueError(f"项目已存在：{name}")
    else:
        project_dir.mkdir(parents=True)
    if project_type == "collection":
        (project_dir / "papers").mkdir(exist_ok=True)
        (project_dir / "outputs").mkdir(exist_ok=True)
    root_manifest = existing_manifest or {"schema_version": 1, "project_type": project_type, "name": name, "tags": payload.get("tags", []), "papers": [], "created_at": timestamp_now()}
    root_manifest.setdefault("created_at", timestamp_now())
    root_manifest["tags"] = payload.get("tags", root_manifest.get("tags", []))
    root_manifest["name"] = name
    root_manifest["project_type"] = project_type
    root_manifest["path"] = str(project_dir)
    root_manifest["codex_workflow"] = {"enabled": auto_codex, "prompt": codex_prompt}
    if project_type == "collection":
        (project_dir / "manifest.json").write_text(json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    elif not existing_manifest:
        # Persist identity before enqueueing so another import can find a queued project.
        root_manifest.update({
            "citation_key": slug(papers[0].get("citation_key") or Path(papers[0]["path"]).stem),
            "source": {"path": str(Path(papers[0]["path"]).expanduser()), "zotero_item_key": papers[0].get("zotero_item_key")},
            "metadata": papers[0],
            "processing": {"status": "queued"},
        })
        (project_dir / "manifest.json").write_text(json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for paper in papers:
        path = Path(paper["path"]).expanduser()
        if not path.exists() or path.suffix.lower() != ".pdf":
            raise ValueError(f"PDF 不存在：{path}")
        paper_slug = slug(paper.get("citation_key") or path.stem)
        root_manifest["papers"].append({
            "citation_key": paper_slug,
            "title": paper.get("title") or path.stem,
            "metadata": paper,
            "source": {"path": str(path), "zotero_item_key": paper.get("zotero_item_key")},
            "created_at": timestamp_now(),
        })
        reusable = find_reusable_artifacts(path, paper_slug)
        task = {"id": uuid.uuid4().hex, "project_slug": name, "project_type": project_type, "paper_slug": paper_slug, "source_path": str(path), "metadata": paper, "tags": payload.get("tags", []), "translate": bool(payload.get("translate")), "options": payload.get("options", {}), "reuse_from": str(reusable) if reusable else "", "auto_codex": auto_codex, "codex_workflow": {"enabled": auto_codex, "prompt": codex_prompt}, "status": "queued", "progress": 0, "message": "等待处理" if not reusable else "等待复用已有结果", "created_at": timestamp_now()}
        TASKS.add(task)
    if project_type == "collection":
        (project_dir / "manifest.json").write_text(json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    set_project_folders(name, requested_folder_ids)
    if auto_codex:
        schedule_project_codex(name)
    return {"name": name, "path": str(project_dir), "task_count": len(papers), "outcome": "created"}


def _paper_identity(paper: dict) -> tuple[str, str, str]:
    """Return stable identity fields used when merging papers into a collection."""
    return (
        str(paper.get("zotero_item_key") or "").strip(),
        slug(paper.get("citation_key") or ""),
        str(Path(paper.get("path", "")).expanduser().resolve()) if paper.get("path") else "",
    )


def add_collection_papers(project_name: str, payload: dict) -> dict:
    """Append papers to an existing collection project and enqueue extraction tasks."""
    with PROJECT_CREATE_LOCK:
        return _add_collection_papers(project_name, payload)


def _add_collection_papers(project_name: str, payload: dict) -> dict:
    """Implementation for add_collection_papers, called while creation is locked."""
    name = slug(project_name)
    project_dir = safe_project_dir(name)
    root_manifest = project_manifest(project_dir)
    if not root_manifest or root_manifest.get("project_type") != "collection":
        raise ValueError("只有多文章项目可以追加文章")
    papers = payload.get("papers") or []
    if not isinstance(papers, list) or not papers:
        raise ValueError("至少选择一篇文章")
    for paper in papers:
        if not isinstance(paper, dict):
            raise ValueError("文章数据格式无效")
        path = Path(paper.get("path", "")).expanduser()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError(f"PDF 不存在：{path}")

    existing = root_manifest.get("papers", [])
    existing_ids = {_paper_identity(item.get("metadata") or item) for item in existing}
    existing_slugs = {slug(item.get("citation_key") or "") for item in existing}
    added: list[dict] = []
    skipped: list[str] = []
    auto_codex = bool(payload.get("auto_codex", (root_manifest.get("codex_workflow") or {}).get("enabled")))
    codex_prompt = normalize_codex_prompt(payload.get("codex_prompt", (root_manifest.get("codex_workflow") or {}).get("prompt")))
    tags = payload.get("tags", root_manifest.get("tags", []))
    translate = bool(payload.get("translate"))
    for paper in papers:
        paper_slug = slug(paper.get("citation_key") or Path(paper["path"]).stem)
        identity = _paper_identity(paper)
        if identity in existing_ids or paper_slug in existing_slugs:
            skipped.append(paper_slug)
            continue
        # Keep directory names unique even for legacy records with duplicate citation keys.
        base_slug = paper_slug
        counter = 2
        while paper_slug in existing_slugs or (project_dir / "papers" / paper_slug).exists():
            paper_slug = f"{base_slug}-{counter}"
            counter += 1
        path = Path(paper["path"]).expanduser()
        item = {
            "citation_key": paper_slug,
            "title": paper.get("title") or path.stem,
            "metadata": paper,
            "source": {"path": str(path), "zotero_item_key": paper.get("zotero_item_key")},
            "created_at": timestamp_now(),
        }
        existing.append(item)
        existing_ids.add(identity)
        existing_slugs.add(paper_slug)
        reusable = find_reusable_artifacts(path, paper_slug)
        task = {
            "id": uuid.uuid4().hex,
            "project_slug": name,
            "project_type": "collection",
            "paper_slug": paper_slug,
            "source_path": str(path),
            "metadata": paper,
            "tags": tags,
            "translate": translate,
            "options": payload.get("options", {}),
            "reuse_from": str(reusable) if reusable else "",
            "auto_codex": auto_codex,
            "codex_workflow": {"enabled": auto_codex, "prompt": codex_prompt},
            "status": "queued",
            "progress": 0,
            "message": "等待处理" if not reusable else "等待复用已有结果",
            "created_at": timestamp_now(),
        }
        TASKS.add(task)
        added.append({"citation_key": paper_slug, "reused": bool(reusable)})
    root_manifest["papers"] = existing
    root_manifest["tags"] = tags
    root_manifest["codex_workflow"] = {"enabled": auto_codex, "prompt": codex_prompt}
    root_manifest["updated_at"] = timestamp_now()
    (project_dir / "manifest.json").write_text(json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if added and auto_codex:
        schedule_project_codex(name)
    return {"name": name, "added": added, "skipped": skipped, "task_count": len(added), "outcome": "updated"}


def delete_collection_paper(project_name: str, citation_key: str) -> dict:
    """Remove one paper directory and its manifest entry from a collection."""
    name = slug(project_name)
    project_dir = safe_project_dir(name)
    root_manifest = project_manifest(project_dir)
    if not root_manifest or root_manifest.get("project_type") != "collection":
        raise ValueError("只有多文章项目可以删除单独文章")
    paper_slug = slug(citation_key)
    papers = root_manifest.get("papers", [])
    target = next((item for item in papers if slug(item.get("citation_key") or "") == paper_slug), None)
    if not target:
        raise ValueError(f"找不到文章：{paper_slug}")
    with TASKS.lock:
        active = [task.get("status") for task in TASKS.tasks.values()
                  if task.get("project_slug") == name and task.get("paper_slug") == paper_slug
                  and task.get("status") in {"queued", "running", "cancelling"}]
    if active:
        raise ValueError("文章仍在处理中，请等待任务完成后再删除")
    paper_dir = project_dir / "papers" / paper_slug
    if paper_dir.exists():
        shutil.rmtree(paper_dir)
    root_manifest["papers"] = [item for item in papers if item is not target]
    root_manifest["updated_at"] = timestamp_now()
    (project_dir / "manifest.json").write_text(json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with TASKS.lock:
        TASKS.tasks = {task_id: task for task_id, task in TASKS.tasks.items()
                       if not (task.get("project_slug") == name and task.get("paper_slug") == paper_slug)}
    return {"name": name, "citation_key": paper_slug, "article_count": len(root_manifest["papers"])}


def enqueue_retry(project_name: str) -> dict:
    name = slug(project_name)
    project_dir = WORKSPACES / name
    manifest = project_manifest(project_dir)
    if not manifest or manifest.get("project_type") != "paper":
        raise ValueError("找不到可重试的单篇文章项目")
    source_path = Path(manifest.get("source", {}).get("path", "")).expanduser()
    if not source_path.is_file():
        raise ValueError(f"原始 PDF 不存在：{source_path}")
    with TASKS.lock:
        for existing in TASKS.tasks.values():
            if existing.get("project_slug") == name and existing.get("status") not in {"queued", "running", "cancelling"}:
                existing["status"] = "superseded"
    workflow = manifest.get("codex_workflow") or {}
    task = {
        "id": uuid.uuid4().hex,
        "project_slug": name,
        "project_type": "paper",
        "paper_slug": manifest.get("citation_key") or name,
        "source_path": str(source_path),
        "metadata": manifest.get("metadata", {}),
        "tags": manifest.get("tags", []),
        "translate": (project_dir / "translation.md").exists(),
        "options": manifest.get("processing", {}).get("options", {}),
        "status": "queued",
        "progress": 0,
        "message": "等待重试",
        "auto_codex": bool(workflow.get("enabled")),
        "codex_workflow": workflow,
        "created_at": time.strftime("%H:%M:%S"),
    }
    TASKS.add(task)
    if task["auto_codex"]:
        schedule_project_codex(name)
    return {"task_id": task["id"], "name": name}


class Handler(BaseHTTPRequestHandler):
    server_version = "ReadMyZotero/0.1"

    def log_message(self, *_: object) -> None:
        return

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self.send_json({"api_version": 2, "zotero_dir": CONFIG["zotero_dir"], "workspaces_dir": str(WORKSPACES), "concurrency": TASKS.concurrency, "codex_concurrency": CODEX_RUNS.concurrency})
        elif parsed.path == "/api/folders":
            self.send_json(folder_catalog())
        elif parsed.path == "/api/search":
            self.send_json({"items": zotero_rows(parse_qs(parsed.query).get("q", [""])[0])})
        elif parsed.path == "/api/zotero/collections":
            self.send_json({"collections": zotero_collections()})
        elif parsed.path == "/api/zotero/collection":
            self.send_json(zotero_collection_items(parse_qs(parsed.query).get("key", [""])[0]))
        elif parsed.path == "/api/projects":
            projects = list_projects()
            for project in projects:
                project["codex_status"] = CODEX_RUNS.project_status(project.get("name", ""))
            self.send_json({"projects": projects})
        elif parsed.path == "/api/todos":
            todo_projects = set(load_todo_index()["projects"])
            self.send_json({"projects": [project for project in list_projects() if project.get("name") in todo_projects]})
        elif parsed.path == "/api/project":
            self.send_json(project_details(parse_qs(parsed.query).get("name", [""])[0]))
        elif parsed.path == "/api/project/codex-runs":
            self.send_json({"runs": CODEX_RUNS.project_runs(parse_qs(parsed.query).get("name", [""])[0])})
        elif parsed.path == "/api/tasks":
            codex_tasks = []
            for run in CODEX_RUNS.snapshot():
                terminal = run["status"] in {"completed", "failed", "cancelled", "interrupted"}
                codex_tasks.append({
                    "id": run["id"], "task_type": "codex", "project_slug": run["project_name"],
                    "paper_slug": "Codex 对话", "status": run["status"],
                    "progress": 100 if terminal else (50 if run["status"] == "running" else 0),
                    "message": "Codex 运行中" if run["status"] == "running" else ("等待 Codex 执行" if run["status"] == "queued" else "Codex 任务"),
                    "created_at": run.get("created_at"), "run_id": run["id"], "session_id": run.get("session_id"),
                })
            self.send_json({"tasks": TASKS.snapshot() + codex_tasks})
        elif parsed.path == "/api/codex/runs":
            self.send_json({"runs": CODEX_RUNS.snapshot()})
        elif parsed.path == "/api/codex/run":
            self.send_json(CODEX_RUNS.get(parse_qs(parsed.query).get("id", [""])[0]))
        elif parsed.path == "/api/codex/command":
            self.send_json({"command": CODEX_RUNS.command_for(parse_qs(parsed.query).get("id", [""])[0])})
        elif parsed.path == "/api/project/outputs":
            self.send_json({"outputs": list_project_outputs(parse_qs(parsed.query).get("name", [""])[0])})
        elif parsed.path == "/api/output":
            query = parse_qs(parsed.query)
            self.send_json(read_project_output(query.get("project", [""])[0], query.get("path", [""])[0]))
        elif parsed.path == "/":
            # The UI is served by Next.js; keep the Python process API-only
            # while redirecting old bookmarks away from the legacy frontend.
            frontend_port = os.environ.get("READ_MY_ZOTERO_FRONTEND_PORT", "3000")
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"http://127.0.0.1:{frontend_port}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif parsed.path.startswith("/static/"):
            self.serve_file(STATIC / parsed.path.removeprefix("/static/"))
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/import":
                self.handle_import()
            elif parsed.path == "/api/projects":
                self.send_json(create_project(self.read_json()), 201)
            elif parsed.path == "/api/folders":
                payload = self.read_json()
                action = str(payload.get("action") or "create")
                if action == "create":
                    self.send_json(create_folder(payload.get("name", ""), payload.get("parent_id")))
                elif action == "rename":
                    self.send_json(rename_folder(str(payload.get("id", "")), payload.get("name", "")))
                elif action == "delete":
                    self.send_json(delete_folder(str(payload.get("id", ""))))
                elif action == "copy-projects":
                    target_ids = payload.get("target_ids") or []
                    if not isinstance(target_ids, list):
                        raise ValueError("target_ids 必须是数组")
                    self.send_json(copy_folder_projects(str(payload.get("source_id", "")), target_ids))
                else:
                    raise ValueError("不支持的文件夹操作")
            elif parsed.path == "/api/project/folders":
                payload = self.read_json()
                project_name = slug(payload.get("project", ""))
                if not project_manifest(safe_project_dir(project_name)):
                    raise ValueError(f"找不到项目：{project_name}")
                folder_ids = payload.get("folder_ids") or []
                if not isinstance(folder_ids, list):
                    raise ValueError("folder_ids 必须是数组")
                mode = str(payload.get("mode") or "replace")
                if mode == "add":
                    memberships = add_project_folders(project_name, folder_ids)
                elif mode == "replace":
                    memberships = set_project_folders(project_name, folder_ids)
                else:
                    raise ValueError("不支持的文件夹归类模式")
                self.send_json({"project": project_name, "folder_ids": memberships})
            elif parsed.path == "/api/project/todo":
                payload = self.read_json()
                project_name = slug(payload.get("project", ""))
                if not project_manifest(safe_project_dir(project_name)):
                    raise ValueError(f"找不到项目：{project_name}")
                state = str(payload.get("state") or ("todo" if payload.get("enabled") else "read"))
                result = set_project_todo(project_name, state)
                self.send_json({"project": project_name, "state": result})
            elif parsed.path == "/api/project/papers":
                payload = self.read_json()
                project_name = payload.get("project", "")
                self.send_json(add_collection_papers(project_name, payload), 202)
            elif parsed.path == "/api/project/paper/delete":
                payload = self.read_json()
                self.send_json(delete_collection_paper(payload.get("project", ""), payload.get("citation_key", "")))
            elif parsed.path == "/api/retry":
                self.send_json(enqueue_retry(self.read_json().get("name", "")), 202)
            elif parsed.path == "/api/codex/run":
                payload = self.read_json()
                self.send_json(CODEX_RUNS.add(payload.get("project", ""), payload.get("prompt", "")), 202)
            elif parsed.path == "/api/codex/cancel":
                self.send_json(CODEX_RUNS.cancel(self.read_json().get("id", "")))
            elif parsed.path == "/api/rename":
                payload = self.read_json()
                self.send_json(rename_project(payload.get("name", ""), payload.get("new_name", "")))
            elif parsed.path == "/api/delete":
                self.send_json(delete_project(self.read_json().get("name", "")))
            elif parsed.path == "/api/config":
                payload = self.read_json()
                if payload.get("zotero_dir"):
                    CONFIG["zotero_dir"] = str(Path(payload["zotero_dir"]).expanduser())
                if payload.get("workspaces_dir"):
                    CONFIG["workspaces_dir"] = str(Path(payload["workspaces_dir"]).expanduser())
                    global WORKSPACES, IMPORTS
                    WORKSPACES = Path(CONFIG["workspaces_dir"])
                    WORKSPACES.mkdir(parents=True, exist_ok=True)
                    IMPORTS = WORKSPACES / ".imports"
                    IMPORTS.mkdir(exist_ok=True)
                if payload.get("concurrency"):
                    CONFIG["concurrency"] = max(1, min(int(payload["concurrency"]), 8))
                    TASKS.resize(CONFIG["concurrency"])
                if payload.get("codex_concurrency"):
                    CONFIG["codex_concurrency"] = max(1, min(int(payload["codex_concurrency"]), 8))
                    CODEX_RUNS.resize(CONFIG["codex_concurrency"])
                save_config(CONFIG)
                self.send_json({"ok": True})
            elif parsed.path == "/api/open-vscode":
                project_path = self.read_json().get("path")
                if not project_path:
                    raise ValueError("缺少项目路径")
                project_dir = Path(project_path).expanduser()
                if not project_dir.is_dir():
                    raise ValueError("项目目录不存在")
                vscode_bin = resolve_vscode_bin()
                if vscode_bin:
                    subprocess.Popen(
                        [vscode_bin, "--reuse-window", str(project_dir)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    # The app bundle can open a folder even when its `code` CLI is not installed.
                    subprocess.Popen(
                        ["/usr/bin/open", "-a", "Visual Studio Code", str(project_dir)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                self.send_json({"ok": True})
            elif parsed.path == "/api/open-finder":
                project_name = self.read_json().get("name", "")
                project_dir = safe_project_dir(project_name)
                if not project_manifest(project_dir):
                    raise ValueError(f"找不到项目：{slug(project_name)}")
                subprocess.Popen(["open", "-R", str(project_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json({"ok": True})
            elif parsed.path == "/api/open-output":
                payload = self.read_json()
                output_path = project_output_path(payload.get("project", ""), payload.get("path", ""))
                subprocess.Popen(["open", str(output_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json({"ok": True, "path": str(output_path)})
            elif parsed.path == "/api/open-translation":
                payload = self.read_json()
                translation_path = project_translation_path(payload.get("project", ""), payload.get("path", ""))
                subprocess.Popen(["open", str(translation_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json({"ok": True, "path": str(translation_path)})
            else:
                self.send_json({"error": "Not found"}, 404)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_import(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        match = re.search(r"boundary=([^;]+)", content_type)
        if not match:
            raise ValueError("缺少文件上传边界")
        boundary = match.group(1).strip('"').encode()
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        parts = body.split(b"--" + boundary)
        imported = []
        for part in parts:
            if b"filename=" not in part:
                continue
            header, content = part.split(b"\r\n\r\n", 1)
            filename_match = re.search(rb'filename="([^"]+)"', header)
            if not filename_match:
                continue
            filename = Path(filename_match.group(1).decode("utf-8", "replace")).name
            content = content.rstrip(b"\r\n-")
            if not filename.lower().endswith(".pdf"):
                continue
            target = IMPORTS / f"{uuid.uuid4().hex[:10]}-{filename}"
            target.write_bytes(content)
            imported.append({"path": str(target), "title": target.stem, "citation_key": slug(target.stem), "abstract": "", "authors": "", "year": ""})
        if not imported:
            raise ValueError("没有收到 PDF 文件")
        self.send_json({"files": imported}, 201)

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_json({"error": "Not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        if path.suffix.lower() in {".js", ".css", ".html"}:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("READ_MY_ZOTERO_PORT", "8765"))
    print(f"Read My Zotero: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
