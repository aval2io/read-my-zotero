"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Folder,
  FolderOpen,
  Library,
  ListChecks,
  ListTodo,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Tag,
  X,
  Zap,
  Copy,
  ExternalLink,
  Terminal,
  FileText,
  Languages,
  Play,
  ArrowUpDown,
  Square,
  Eye,
  Trash2,
} from "lucide-react";

const DEFAULT_CODEX_PROMPT =
  "使用 $paper-reading-zh 这个 skill 来阅读 full.md 以及 images/ 并撰写 markdown 报告输出到 outputs/ 中，标题使用论文标题的中文总结";

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function cn(...values) {
  return values.filter(Boolean).join(" ");
}
function formatDate(value) {
  return value ? String(value).slice(0, 10) : "日期未知";
}
function projectStatus(project) {
  if (project.codex_status === "running") return ["running", "Codex 执行中"];
  const status = project.processing?.status;
  return status === "failed"
    ? ["failed", "处理失败"]
    : status === "needs_retry"
      ? ["retry", "待重试"]
      : ["ready", "已就绪"];
}

function projectPaperTitle(project) {
  return (
    project.metadata?.title ||
    project.title ||
    project.papers?.[0]?.metadata?.title ||
    project.papers?.[0]?.title ||
    ""
  );
}

function projectDisplayName(project, nameMode = "citation") {
  const name = project.name || project.citation_key || "";
  // A paper project is automatically named from its citation key at creation
  // time. Collection names and renamed projects must remain untouched.
  const isAutomaticPaperName =
    project.project_type === "paper" &&
    Boolean(project.citation_key) &&
    name === project.citation_key;
  if (nameMode === "title" && isAutomaticPaperName) {
    return projectPaperTitle(project) || name;
  }
  return name;
}

function TreeNode({
  item,
  childrenByParent,
  selected,
  onSelect,
  expanded,
  onToggle,
  depth = 0,
}) {
  const children = childrenByParent[item.id] || [];
  const open = expanded.has(item.id);
  return (
    <div className="tree-branch">
      <div
        className={cn("tree-node", selected === item.key && "selected")}
        style={{ "--depth": depth }}
      >
        <button
          className="tree-toggle"
          onClick={() => children.length && onToggle(item.id)}
          aria-label={open ? "收起目录" : "展开目录"}
        >
          {children.length ? (
            open ? (
              <ChevronDown size={15} />
            ) : (
              <ChevronRight size={15} />
            )
          ) : (
            <span className="tree-leaf" />
          )}
        </button>
        <button className="tree-label" onClick={() => onSelect(item.key)}>
          {open ? <FolderOpen size={16} /> : <Folder size={16} />}
          <span>{item.name}</span>
        </button>
        <span className="tree-count">{item.item_count || 0}</span>
      </div>
      {children.length > 0 && (
        <div
          className={cn("tree-children", open && "open")}
          aria-hidden={!open}
          inert={!open}
        >
          <div className="tree-children-inner">
            {children.map((child) => (
              <TreeNode
                key={child.key}
                item={child}
                childrenByParent={childrenByParent}
                selected={selected}
                onSelect={onSelect}
                expanded={expanded}
                onToggle={onToggle}
                depth={depth + 1}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ZoteroTree({ collections, selected, onSelect }) {
  const [expanded, setExpanded] = useState(new Set());
  const childrenByParent = useMemo(
    () =>
      collections.reduce((map, item) => {
        const key = item.parent_id || "root";
        (map[key] ||= []).push(item);
        return map;
      }, {}),
    [collections],
  );
  const roots = childrenByParent.root || [];
  const toggle = (id) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  return (
    <div className="tree-list">
      {roots.map((item) => (
        <TreeNode
          key={item.key}
          item={item}
          childrenByParent={childrenByParent}
          selected={selected}
          onSelect={onSelect}
          expanded={expanded}
          onToggle={toggle}
        />
      ))}
      {!roots.length && (
        <div className="empty-small">没有找到 Zotero collection</div>
      )}
    </div>
  );
}

function WorkspaceTree({ folders, counts, selected, onSelect, onDelete }) {
  const [expanded, setExpanded] = useState(new Set());
  const childrenByParent = useMemo(
    () =>
      folders.reduce((map, item) => {
        const key = item.parent_id || "root";
        (map[key] ||= []).push(item);
        return map;
      }, {}),
    [folders],
  );
  const roots = childrenByParent.root || [];
  const toggle = (id) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  return (
    <div className="workspace-tree">
      <button
        className={cn("workspace-root", selected === "" && "active")}
        onClick={() => onSelect("")}
      >
        <Library size={15} />
        <span>全部项目</span>
        <b>{counts.total || 0}</b>
      </button>
      <button
        className={cn("workspace-root", selected === "__unfiled__" && "active")}
        onClick={() => onSelect("__unfiled__")}
      >
        <CircleHelp size={15} />
        <span>未分类</span>
        <b>{counts.unfiled || 0}</b>
      </button>
      {roots.map((item) => (
        <WorkspaceNode
          key={item.id}
          item={item}
          childrenByParent={childrenByParent}
          selected={selected}
          onSelect={onSelect}
          expanded={expanded}
          onToggle={toggle}
          depth={0}
          counts={counts}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}

function WorkspaceNode({
  item,
  childrenByParent,
  selected,
  onSelect,
  expanded,
  onToggle,
  depth,
  counts,
  onDelete,
}) {
  const children = childrenByParent[item.id] || [];
  const open = expanded.has(item.id);
  return (
    <div>
      <div
        className={cn("workspace-node", selected === item.id && "active")}
        style={{ "--depth": depth }}
      >
        <button
          className="tree-toggle"
          onClick={() => children.length && onToggle(item.id)}
        >
          {children.length ? (
            open ? (
              <ChevronDown size={14} />
            ) : (
              <ChevronRight size={14} />
            )
          ) : (
            <span className="tree-leaf" />
          )}
        </button>
        <button className="workspace-label" onClick={() => onSelect(item.id)}>
          <Folder size={15} />
          <span>{item.name}</span>
        </button>
        <b>{counts.byFolder?.[item.id] || 0}</b>
        <button
          className="workspace-delete"
          title={`删除目录 ${item.name}`}
          aria-label={`删除目录 ${item.name}`}
          onClick={(event) => {
            event.stopPropagation();
            onDelete?.(item);
          }}
        >
          <Trash2 size={13} />
        </button>
      </div>
      {children.length > 0 && (
        <div
          className={cn("workspace-children", open && "open")}
          aria-hidden={!open}
          inert={!open}
        >
          <div className="tree-children-inner">
            {children.map((child) => (
              <WorkspaceNode
                key={child.id}
                item={child}
                childrenByParent={childrenByParent}
                selected={selected}
                onSelect={onSelect}
                expanded={expanded}
                onToggle={onToggle}
                depth={depth + 1}
                counts={counts}
                onDelete={onDelete}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AppShell({
  view,
  setView,
  children,
  taskCount,
  todoCount,
  workspaceSidebar,
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div>
            <strong>Read My Zotero</strong>
          </div>
        </div>
        <div className="nav-section">
          <span className="nav-caption">WORKSPACE</span>
          <button
            className={cn("nav-item", view === "projects" && "active")}
            onClick={() => setView("projects")}
          >
            <Library size={17} />
            项目
          </button>
          <button
            className={cn("nav-item", view === "todos" && "active")}
            onClick={() => setView("todos")}
          >
            <ListTodo size={17} />
            TODOs {todoCount > 0 && <em>{todoCount}</em>}
          </button>
          <button
            className={cn("nav-item", view === "zotero" && "active")}
            onClick={() => setView("zotero")}
          >
            <BookOpen size={17} />
            Zotero 库
          </button>
          <button
            className={cn("nav-item", view === "create" && "active")}
            onClick={() => setView("create")}
          >
            <Plus size={17} />
            创建项目
          </button>
          <button
            className={cn("nav-item", view === "tasks" && "active")}
            onClick={() => setView("tasks")}
          >
            <ListChecks size={17} />
            任务队列 {taskCount > 0 && <em>{taskCount}</em>}
          </button>
          <button
            className={cn(
              "nav-item mobile-settings",
              view === "settings" && "active",
            )}
            onClick={() => setView("settings")}
            aria-label="设置"
          >
            <Settings2 size={17} />
          </button>
        </div>
        {workspaceSidebar}
        <div className="sidebar-foot">
          <button onClick={() => setView("settings")}>
            <Settings2 size={15} />
            设置
          </button>
          <span className="live-dot" />
          本地服务
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}

function Header({ eyebrow, title, action, onRefresh }) {
  return (
    <header className="topbar">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
      </div>
      <div className="top-actions">
        {onRefresh && (
          <button className="button ghost" onClick={onRefresh}>
            <RefreshCw size={15} />
            刷新
          </button>
        )}
        {action}
      </div>
    </header>
  );
}

function ProjectsView({
  projects,
  folders,
  folderData,
  folderId,
  setFolderId,
  setView,
  refresh,
  onOpenProject,
  onDeleteProject,
  onToggleTodo,
  todoOnly = false,
}) {
  const [filter, setFilter] = useState("");
  const [sortMode, setSortMode] = useState("created_desc");
  const [nameMode, setNameMode] = useState("citation");
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("read-my-zotero-project-name-mode");
      if (saved === "title" || saved === "citation") setNameMode(saved);
    } catch {
      // localStorage can be unavailable in private browsing contexts.
    }
  }, []);
  const changeNameMode = (event) => {
    const next = event.target.checked ? "title" : "citation";
    setNameMode(next);
    try {
      window.localStorage.setItem("read-my-zotero-project-name-mode", next);
    } catch {
      // The preference still applies for the current page session.
    }
  };
  const visible = useMemo(() => {
    const filtered = projects
      .filter((project) =>
        todoOnly
          ? project.todo
          : folderId === ""
            ? true
            : folderId === "__unfiled__"
              ? !(project.folder_ids || []).length
              : (project.folder_ids || []).includes(folderId),
      )
      .filter((project) =>
        `${projectDisplayName(project, nameMode)} ${project.name} ${project.tags?.join(" ")}`
          .toLowerCase()
          .includes(filter.toLowerCase()),
      );
    const dateValue = (value) => {
      const parsed = Date.parse(value || "");
      return Number.isNaN(parsed) ? 0 : parsed;
    };
    const nameValue = (project) => projectDisplayName(project, nameMode);
    return [...filtered].sort((left, right) => {
      if (sortMode === "name_asc")
        return nameValue(left).localeCompare(nameValue(right), "zh-Hans-CN", {
          numeric: true,
          sensitivity: "base",
        });
      if (sortMode === "name_desc")
        return nameValue(right).localeCompare(nameValue(left), "zh-Hans-CN", {
          numeric: true,
          sensitivity: "base",
        });
      if (sortMode === "outputs_desc")
        return (
          (right.output_count || 0) - (left.output_count || 0) ||
          dateValue(right.created_at) - dateValue(left.created_at)
        );
      if (sortMode === "articles_desc")
        return (
          (right.article_count || 0) - (left.article_count || 0) ||
          dateValue(right.created_at) - dateValue(left.created_at)
        );
      if (sortMode === "updated_desc")
        return (
          dateValue(right.updated_at) - dateValue(left.updated_at) ||
          dateValue(right.created_at) - dateValue(left.created_at)
        );
      if (sortMode === "created_asc")
        return (
          dateValue(left.created_at) - dateValue(right.created_at) ||
          nameValue(left).localeCompare(nameValue(right))
        );
      return (
        dateValue(right.created_at) - dateValue(left.created_at) ||
        nameValue(left).localeCompare(nameValue(right))
      );
    });
  }, [projects, folderId, filter, sortMode, nameMode]);
  const counts = {
    total: projects.length,
    unfiled: projects.filter((project) => !(project.folder_ids || []).length)
      .length,
    byFolder: folderData?.counts || {},
  };
  const title = todoOnly ? "TODOs" : "项目";
  const emptyTitle = todoOnly ? "TODO 队列为空" : "这里还没有项目";
  return (
    <>
      <Header
        eyebrow={todoOnly ? "READING PLAN" : "WORKSPACES"}
        title={title}
        onRefresh={refresh}
        action={
          !todoOnly && (
            <button
              className="button primary"
              onClick={() => setView("create")}
            >
              <Plus size={16} />
              新建项目
            </button>
          )
        }
      />
      <div className="content-toolbar">
        <div className="search-box">
          <Search size={16} />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="筛选项目、标签..."
          />
        </div>
        <div className="project-toolbar-actions">
          <label className="name-mode-toggle" title="切换项目列表中的名称显示方式">
            <input
              type="checkbox"
              checked={nameMode === "title"}
              onChange={changeNameMode}
            />
            <span>显示论文标题</span>
          </label>
          <label className="sort-control" title="项目排序">
            <ArrowUpDown size={15} />
            <select
              aria-label="项目排序"
              value={sortMode}
              onChange={(event) => setSortMode(event.target.value)}
            >
              <option value="created_desc">最近创建</option>
              <option value="created_asc">最早创建</option>
              <option value="updated_desc">最近更新</option>
              <option value="name_asc">名称 A-Z</option>
              <option value="name_desc">名称 Z-A</option>
              <option value="outputs_desc">产出最多</option>
              <option value="articles_desc">文章最多</option>
            </select>
          </label>
          <span className="result-count">{visible.length} 个项目</span>
        </div>
      </div>
      <section className="project-table">
        <div className="table-head">
          <span>项目</span>
          <span>TODO</span>
          <span>文章</span>
          <span>产出</span>
          <span>翻译</span>
          <span>状态</span>
          <span>创建时间</span>
          <span />
        </div>
        {visible.length ? (
          visible.map((project) => {
            const [status, label] = projectStatus(project);
            const projectName = project.name || project.citation_key;
            const displayName = projectDisplayName(project, nameMode);
            const paperTitle = projectPaperTitle(project) || projectName;
            const isRead = project.todo_read && !project.todo;
            const todoLabel = todoOnly
              ? "移除TODO"
              : project.todo
                ? "TODO"
                : isRead
                  ? "已读"
                  : "加入TODO";
            const todoState = todoOnly
              ? "read"
              : project.todo
                ? "read"
                : isRead
                  ? "clear"
                  : "todo";
            return (
              <div className="project-row" key={projectName}>
                <div className="project-title">
                  <div className="project-badge">
                    {project.project_type === "collection" ? (
                      <Library size={16} />
                    ) : (
                      <BookOpen size={16} />
                    )}
                  </div>
                  <div>
                    <button
                      className="project-title-link"
                      title={paperTitle}
                      onClick={() => onOpenProject(projectName)}
                    >
                      {displayName}
                    </button>
                    <small>
                      {project.tags?.map((tag) => (
                        <span className="tag" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </small>
                  </div>
                </div>
                <button
                  type="button"
                  className={cn(
                    "todo-button",
                    project.todo && "is-todo",
                    isRead && "is-read",
                    todoOnly && "remove-todo",
                  )}
                  onClick={() => onToggleTodo(project, todoState)}
                  title={
                    todoOnly
                      ? "移出 TODO 队列并标记为已读"
                      : project.todo
                        ? "完成阅读并移出 TODO 队列"
                        : isRead
                          ? "清除已读状态"
                          : "加入 TODO 队列"
                  }
                >
                  {todoLabel}
                </button>
                <span className="muted">{project.article_count || 1}</span>
                <span
                  className="project-indicator"
                  title={`${project.output_count || 0} 个 outputs 文件`}
                >
                  <FileText size={13} />
                  {project.output_count || 0}
                </span>
                <span
                  className={cn(
                    "project-indicator",
                    project.translation_exists && "is-ready",
                  )}
                  title={
                    project.translation_exists ? "有翻译文件" : "暂无翻译文件"
                  }
                >
                  <Languages size={13} />
                  {project.translation_exists ? "有" : "无"}
                </span>
                <span className={cn("status", status)}>
                  <i />
                  {label}
                </span>
                <time
                  className="project-created"
                  dateTime={project.created_at || undefined}
                >
                  {project.created_at ? formatDate(project.created_at) : "-"}
                </time>
                <div className="row-actions">
                  <button
                    className="icon-button"
                    onClick={() => onOpenProject(projectName)}
                  >
                    查看
                  </button>
                  <button
                    className="icon-button delete-icon-button"
                    onClick={() => onDeleteProject(projectName)}
                    aria-label={`删除项目 ${projectName}`}
                    title="删除项目"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            );
          })
        ) : (
          <div className="empty">
            <ListTodo size={28} />
            <strong>{emptyTitle}</strong>
            <span>
              {todoOnly
                ? "从项目列表将计划阅读的项目加入这里。"
                : "从 Zotero 库选择文章，或创建一个新项目。"}
            </span>
            {!todoOnly && (
              <button
                className="button primary"
                onClick={() => setView("zotero")}
              >
                <BookOpen size={16} />
                浏览 Zotero
              </button>
            )}
          </div>
        )}
      </section>
    </>
  );
}

function DeleteProjectDialog({ projectName, onClose, onDeleted }) {
  const dialogRef = useRef(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!projectName || !dialog) return;
    dialog.showModal();
    return () => dialog.close();
  }, [projectName]);
  const remove = async (event) => {
    event.preventDefault();
    if (deleting) return;
    setDeleting(true);
    setError("");
    try {
      await api("/api/delete", {
        method: "POST",
        body: JSON.stringify({ name: projectName }),
      });
      await onDeleted(projectName);
    } catch (cause) {
      setError(cause.message);
    } finally {
      setDeleting(false);
    }
  };
  return (
    <dialog
      ref={dialogRef}
      className="detail-dialog delete-project-dialog"
      aria-labelledby="delete-project-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!deleting) onClose();
      }}
    >
      <form onSubmit={remove}>
        <header className="detail-header">
          <div>
            <div className="eyebrow">DELETE PROJECT</div>
            <h2 id="delete-project-title">删除项目</h2>
          </div>
          <button
            type="button"
            className="icon-button detail-close"
            aria-label="关闭"
            disabled={deleting}
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </header>
        <p className="delete-project-copy">
          将永久删除 <strong>{projectName}</strong> 及其项目文件，包括已提取的
          Markdown、翻译和 outputs。此操作无法撤销。
        </p>
        {error && (
          <div className="error-banner folder-error" role="alert">
            <CircleHelp size={16} />
            {error}
          </div>
        )}
        <footer className="folder-footer">
          <button
            type="button"
            className="button ghost"
            disabled={deleting}
            onClick={onClose}
          >
            取消
          </button>
          <button type="submit" className="button danger" disabled={deleting}>
            {deleting ? <LoaderCircle size={16} /> : <Trash2 size={15} />}
            {deleting ? "删除中..." : "永久删除项目"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}

function ZoteroView({
  collections,
  folders,
  selectedCollection,
  setSelectedCollection,
  collectionData,
  refresh,
  onCreate,
}) {
  const [selected, setSelected] = useState(new Set());
  const [mode, setMode] = useState("single");
  const [projectName, setProjectName] = useState("");
  const [folderIds, setFolderIds] = useState([]);
  const [tags, setTags] = useState("");
  const [translate, setTranslate] = useState(false);
  const [autoCodex, setAutoCodex] = useState(false);
  const [prompt, setPrompt] = useState(DEFAULT_CODEX_PROMPT);
  const [submitting, setSubmitting] = useState(false);
  const items = collectionData?.items || [];
  const pdfItems = items.filter((item) => item.has_pdf);
  const allSelected =
    pdfItems.length > 0 &&
    pdfItems.every((item) => selected.has(item.zotero_item_key));
  const toggleItem = (key) =>
    setSelected((previous) => {
      const next = new Set(previous);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  const chooseCollection = (key) => {
    setSelectedCollection(key);
    setSelected(new Set());
  };
  const selectAll = (checked) =>
    setSelected(
      checked
        ? new Set(pdfItems.map((item) => item.zotero_item_key))
        : new Set(),
    );
  const selectedPapers = items
    .filter((item) => selected.has(item.zotero_item_key))
    .flatMap((item) =>
      (item.pdfs || []).map((path) => ({
        ...item,
        path,
        title: item.title || path.split("/").pop(),
      })),
    );
  const submit = async () => {
    setSubmitting(true);
    try {
      await onCreate({
        papers: selectedPapers,
        mode,
        projectName,
        folderIds,
        tags,
        translate,
        autoCodex,
        prompt,
      });
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <>
      <Header
        eyebrow="ZOTERO LIBRARY"
        title="Zotero 库"
        onRefresh={refresh}
        action={
          <span className="library-status">
            <span className="live-dot" />
            本地数据库
          </span>
        }
      />
      <div className="zotero-layout">
        <section className="library-panel">
          <div className="panel-head">
            <div>
              <h2>Collections</h2>
            </div>
            <span className="count-chip">{collections.length}</span>
          </div>
          <ZoteroTree
            collections={collections}
            selected={selectedCollection}
            onSelect={chooseCollection}
          />
        </section>
        <section className="library-main">
          <div className="library-main-head">
            <div>
              <div className="eyebrow">DIRECT ITEMS</div>
              <h2>
                {collectionData?.collection?.name || "选择一个 collection"}
              </h2>
              <p>
                {collectionData
                  ? `${collectionData.direct_count} 篇直接条目 · ${collectionData.pdf_count} 篇有本地 PDF · 不包含子目录`
                  : "从左侧目录选择一个 collection"}
              </p>
            </div>
            <div className="selection-tools">
              <button
                className="button ghost"
                onClick={() => selectAll(true)}
                disabled={!pdfItems.length}
              >
                <Check size={15} />
                全选带 PDF
              </button>
              <span>{selectedPapers.length} 篇已选择</span>
            </div>
          </div>
          <div className="item-list">
            {items.length ? (
              items.map((item) => (
                <label
                  key={`${item.zotero_item_key}-${item.pdfs?.length}`}
                  className={cn("item-card", !item.has_pdf && "disabled")}
                >
                  <input
                    type="checkbox"
                    disabled={!item.has_pdf}
                    checked={selected.has(item.zotero_item_key)}
                    onChange={() => toggleItem(item.zotero_item_key)}
                  />
                  <div>
                    <strong>{item.title || "未命名条目"}</strong>
                    <div className="item-meta">
                      {item.authors || "作者未知"} · {formatDate(item.date)}
                      {item.doi && ` · DOI ${item.doi}`}
                    </div>
                    {item.abstract && <p>{item.abstract}</p>}
                    <small
                      className={item.has_pdf ? "pdf-ready" : "pdf-missing"}
                    >
                      {item.has_pdf
                        ? `${item.pdfs.length} 个 PDF 附件`
                        : "没有可用 PDF 附件"}
                    </small>
                  </div>
                </label>
              ))
            ) : (
              <div className="empty">
                <FolderOpen size={30} />
                <strong>选择一个 collection</strong>
                <span>这里会列出该目录下的直接文章。</span>
              </div>
            )}
          </div>
          <div className="create-panel">
            <div className="create-panel-head">
              <div>
                <h3>创建阅读工作区</h3>
                <p>所选文章会进入现有 PDF 提取、翻译和 Codex 流程。</p>
              </div>
              <label className="select-all">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={(e) => selectAll(e.target.checked)}
                  disabled={!pdfItems.length}
                />
                全选当前目录（仅直接条目）
              </label>
            </div>
            <div className="form-grid">
              <label>
                创建方式
                <select value={mode} onChange={(e) => setMode(e.target.value)}>
                  <option value="single">逐个创建单篇项目</option>
                  <option value="collection">合并为一个多文章项目</option>
                </select>
              </label>
              <label>
                多文章项目名称
                <input
                  value={projectName}
                  disabled={mode === "single"}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="默认使用 collection 名称"
                />
              </label>
              <label>
                工作区目录
                <select
                  multiple
                  value={folderIds}
                  onChange={(e) =>
                    setFolderIds(
                      [...e.target.selectedOptions].map(
                        (option) => option.value,
                      ),
                    )
                  }
                >
                  {folders.map((folder) => (
                    <option key={folder.id} value={folder.id}>
                      {"　".repeat(folder.depth || 0)}
                      {folder.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                项目标签
                <input
                  value={tags}
                  onChange={(e) => setTags(e.target.value)}
                  placeholder="例如：待读, SIGIR"
                />
              </label>
            </div>
            <div className="option-row">
              <label>
                <input
                  type="checkbox"
                  checked={translate}
                  onChange={(e) => setTranslate(e.target.checked)}
                />
                同时生成中文翻译
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={autoCodex}
                  onChange={(e) => setAutoCodex(e.target.checked)}
                />
                提取完成后自动执行 Codex
              </label>
            </div>
            {autoCodex && (
              <label className="prompt-field">
                自动执行提示词
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              </label>
            )}
            <div className="create-footer">
              <span>只处理有本地 PDF 附件的条目</span>
              <button
                className="button primary"
                disabled={submitting || !selectedPapers.length}
                onClick={submit}
              >
                <Zap size={16} />
                加入处理队列
              </button>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}

function CreateProjectView({ folders, onCreate }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(new Map());
  const [searchState, setSearchState] = useState("idle");
  const [searchError, setSearchError] = useState("");
  const [searchVersion, setSearchVersion] = useState(0);
  const [mode, setMode] = useState("single");
  const [projectName, setProjectName] = useState("");
  const [folderIds, setFolderIds] = useState([]);
  const [tags, setTags] = useState("");
  const [translate, setTranslate] = useState(false);
  const [autoCodex, setAutoCodex] = useState(false);
  const [prompt, setPrompt] = useState(DEFAULT_CODEX_PROMPT);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      setSearchState("idle");
      setSearchError("");
      return;
    }
    const controller = new AbortController();
    setResults([]);
    setSearchState("loading");
    setSearchError("");
    const timer = setTimeout(async () => {
      try {
        const data = await api(
          `/api/search?q=${encodeURIComponent(query.trim())}`,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) {
          setResults(data.items || []);
          setSearchState("ready");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setSearchError(error.message);
          setSearchState("error");
        }
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, searchVersion]);
  const pdfItems = results.filter((item) => item.has_pdf);
  const allSelected =
    pdfItems.length > 0 &&
    pdfItems.every((item) => selected.has(item.zotero_item_key));
  const toggleItem = (item) =>
    setSelected((previous) => {
      const next = new Map(previous);
      next.has(item.zotero_item_key)
        ? next.delete(item.zotero_item_key)
        : next.set(item.zotero_item_key, item);
      return next;
    });
  const selectResults = (checked) =>
    setSelected((previous) => {
      const next = new Map(previous);
      for (const item of pdfItems)
        checked
          ? next.set(item.zotero_item_key, item)
          : next.delete(item.zotero_item_key);
      return next;
    });
  const submit = async (event) => {
    event.preventDefault();
    if (submitting || !selected.size) return;
    setSubmitting(true);
    try {
      const papers = [...selected.values()].map((item) => ({
        ...item,
        path: item.pdfs[0],
      }));
      await onCreate({
        papers,
        mode,
        projectName: projectName.trim(),
        defaultProjectName: "",
        folderIds,
        tags,
        translate,
        autoCodex,
        prompt,
      });
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <>
      <Header
        eyebrow="NEW PROJECT"
        title="创建项目"
        onRefresh={() => setSearchVersion((previous) => previous + 1)}
      />
      <div className="project-search-toolbar">
        <div className="search-box">
          <Search size={16} />
          <input
            aria-label="检索 Zotero 文章"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索标题、作者、摘要、DOI 或 citation key"
          />
          {query && (
            <button
              className="search-clear"
              aria-label="清空关键词"
              title="清空关键词"
              onClick={() => setQuery("")}
            >
              <X size={15} />
            </button>
          )}
        </div>
        <span className="result-count" role="status">
          {searchState === "loading"
            ? "检索中..."
            : searchState === "ready"
              ? `${results.length} 篇文章 · ${pdfItems.length} 篇有本地 PDF`
              : "全部 Zotero 文章"}
        </span>
      </div>
      <div className="project-create-layout">
        <section
          className="project-search-results"
          aria-label="检索结果"
          aria-busy={searchState === "loading"}
        >
          <div className="project-search-head">
            <h2>检索结果</h2>
            <label className="select-all">
              <input
                type="checkbox"
                checked={allSelected}
                disabled={!pdfItems.length || submitting}
                onChange={(event) => selectResults(event.target.checked)}
              />
              全选当前结果
            </label>
          </div>
          {searchState === "error" ? (
            <div className="empty" role="alert">
              <CircleHelp size={28} />
              <strong>检索失败</strong>
              <span>{searchError}</span>
              <button
                className="button ghost"
                onClick={() => setSearchVersion((previous) => previous + 1)}
              >
                <RefreshCw size={15} />
                重试
              </button>
            </div>
          ) : searchState === "loading" ? (
            <div className="empty">
              <LoaderCircle className="search-spinner" size={28} />
              <span>正在检索 Zotero...</span>
            </div>
          ) : results.length ? (
            <div className="item-list">
              {results.map((item) => (
                <label
                  key={item.zotero_item_key}
                  className={cn("item-card", !item.has_pdf && "disabled")}
                >
                  <input
                    type="checkbox"
                    disabled={!item.has_pdf || submitting}
                    checked={selected.has(item.zotero_item_key)}
                    onChange={() => toggleItem(item)}
                  />
                  <div>
                    <strong>{item.title || "未命名条目"}</strong>
                    <div className="item-meta">
                      {item.authors || "作者未知"} · {formatDate(item.date)}
                      {item.citation_key && ` · ${item.citation_key}`}
                      {item.doi && ` · DOI ${item.doi}`}
                    </div>
                    {item.abstract && <p>{item.abstract}</p>}
                    <small
                      className={item.has_pdf ? "pdf-ready" : "pdf-missing"}
                    >
                      {item.has_pdf
                        ? `${item.pdfs.length} 个 PDF 附件`
                        : "没有可用 PDF 附件"}
                    </small>
                  </div>
                </label>
              ))}
            </div>
          ) : (
            <div className="empty">
              <Search size={28} />
              <strong>
                {searchState === "idle" ? "检索 Zotero 文章" : "没有匹配的文章"}
              </strong>
            </div>
          )}
        </section>
        <form className="project-create-settings" onSubmit={submit}>
          <fieldset disabled={submitting}>
            <div className="project-search-head">
              <h2>项目设置</h2>
              <span className="result-count" role="status">
                已选 {selected.size} 篇
              </span>
            </div>
            {selected.size > 0 && (
              <div className="search-selected-list" aria-label="已选文章">
                {[...selected.values()].map((item) => (
                  <div
                    className="search-selected-item"
                    key={item.zotero_item_key}
                  >
                    <span title={item.title}>{item.title || "未命名条目"}</span>
                    <button
                      type="button"
                      className="icon-button"
                      title="移除文章"
                      aria-label={`移除 ${item.title || "未命名条目"}`}
                      onClick={() => toggleItem(item)}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="button ghost"
                  onClick={() => setSelected(new Map())}
                >
                  <X size={14} />
                  清空选择
                </button>
              </div>
            )}
            <div className="form-grid">
              <label>
                创建方式
                <select
                  value={mode}
                  onChange={(event) => setMode(event.target.value)}
                >
                  <option value="single">逐个创建单篇项目</option>
                  <option value="collection">合并为一个多文章项目</option>
                </select>
              </label>
              <label>
                多文章项目名称
                <input
                  value={projectName}
                  disabled={mode === "single"}
                  onChange={(event) => setProjectName(event.target.value)}
                  placeholder="默认使用首篇文章的 citation key"
                />
              </label>
              <label>
                工作区目录
                <select
                  multiple
                  value={folderIds}
                  onChange={(event) =>
                    setFolderIds(
                      [...event.target.selectedOptions].map(
                        (option) => option.value,
                      ),
                    )
                  }
                >
                  {folders.map((folder) => (
                    <option key={folder.id} value={folder.id}>
                      {"　".repeat(folder.depth || 0)}
                      {folder.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                项目标签
                <input
                  value={tags}
                  onChange={(event) => setTags(event.target.value)}
                  placeholder="例如：待读, SIGIR"
                />
              </label>
            </div>
            <div className="option-row">
              <label>
                <input
                  type="checkbox"
                  checked={translate}
                  onChange={(event) => setTranslate(event.target.checked)}
                />
                同时生成中文翻译
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={autoCodex}
                  onChange={(event) => setAutoCodex(event.target.checked)}
                />
                提取完成后自动执行 Codex
              </label>
            </div>
            {autoCodex && (
              <label className="prompt-field">
                自动执行提示词
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                />
              </label>
            )}
            <div className="create-footer">
              <span>
                {mode === "single"
                  ? `${selected.size} 个单篇项目`
                  : `1 个项目 · ${selected.size} 篇文章`}
              </span>
              <button
                type="submit"
                className="button primary"
                disabled={submitting || !selected.size}
              >
                {submitting ? (
                  <LoaderCircle className="search-spinner" size={16} />
                ) : (
                  <Zap size={16} />
                )}
                {submitting ? "提交中..." : "加入处理队列"}
              </button>
            </div>
          </fieldset>
        </form>
      </div>
    </>
  );
}

function taskStatusLabel(status) {
  return (
    {
      queued: "排队中",
      running: "处理中",
      cancelling: "正在取消",
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消",
      interrupted: "已中断",
    }[status] || status
  );
}

function TasksView({ tasks, refresh, onOpenCodexTask }) {
  const [category, setCategory] = useState("processing");
  const processingTasks = tasks.filter((task) => task.task_type !== "codex");
  const codexTasks = tasks.filter((task) => task.task_type === "codex");
  const visibleTasks = category === "codex" ? codexTasks : processingTasks;

  return (
    <>
      <Header eyebrow="QUEUE" title="任务队列" onRefresh={refresh} />
      <div className="queue-tabs" role="tablist" aria-label="任务类型">
        <button
          className={cn("queue-tab", category === "processing" && "active")}
          role="tab"
          aria-selected={category === "processing"}
          onClick={() => setCategory("processing")}
        >
          <FileText size={15} />
          提取 MD / 翻译<span>{processingTasks.length}</span>
        </button>
        <button
          className={cn("queue-tab", category === "codex" && "active")}
          role="tab"
          aria-selected={category === "codex"}
          onClick={() => setCategory("codex")}
        >
          <Terminal size={15} />
          执行 Codex<span>{codexTasks.length}</span>
        </button>
      </div>
      <div className="queue-summary">
        <span>
          <ListChecks size={17} />
          {visibleTasks.length} 个任务
        </span>
        <span>
          {category === "codex"
            ? "点击任务可查看对应的 Codex 会话"
            : "PDF 提取与中文翻译任务"}
        </span>
      </div>
      <section className="task-list">
        {visibleTasks.length ? (
          visibleTasks.map((task) => {
            const isCodex = task.task_type === "codex";
            const taskCard = (
              <article
                className={cn("task-card", isCodex && "codex-task-card")}
                key={`${task.id}-${task.run_id || ""}`}
                onClick={isCodex ? () => onOpenCodexTask(task) : undefined}
                onKeyDown={
                  isCodex
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onOpenCodexTask(task);
                        }
                      }
                    : undefined
                }
                tabIndex={isCodex ? 0 : undefined}
                role={isCodex ? "button" : undefined}
              >
                <div>
                  <strong>
                    {isCodex ? <Terminal size={15} /> : <FileText size={15} />}
                    {isCodex ? "Codex 会话" : task.paper_slug}
                  </strong>
                  <p>{task.message || ""}</p>
                </div>
                <span className={cn("status", task.status)}>
                  <i />
                  {taskStatusLabel(task.status)}
                </span>
                <div className="progress">
                  <i style={{ width: `${task.progress || 0}%` }} />
                </div>
                <small>
                  {task.project_slug} · {task.progress || 0}%
                  {isCodex && (
                    <span className="task-detail-hint">
                      查看 Codex 会话 <ChevronRight size={13} />
                    </span>
                  )}
                </small>
              </article>
            );
            return taskCard;
          })
        ) : (
          <div className="empty">
            <ListChecks size={30} />
            <strong>
              {category === "codex" ? "暂无 Codex 任务" : "暂无提取或翻译任务"}
            </strong>
            <span>创建项目或启动 Codex 后，任务会显示在这里。</span>
          </div>
        )}
      </section>
    </>
  );
}

function AddCollectionPapersDialog({ projectName, onClose, onAdded }) {
  const dialogRef = useRef(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(new Map());
  const [translate, setTranslate] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!projectName || !dialogRef.current) return undefined;
    dialogRef.current.showModal();
    return () => {
      if (dialogRef.current?.open) dialogRef.current.close();
    };
  }, [projectName]);
  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const data = await api(`/api/search?q=${encodeURIComponent(query.trim())}`, { signal: controller.signal });
        if (!controller.signal.aborted) setResults(data.items || []);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause.message);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);
  const toggle = (item) => setSelected((previous) => {
    const next = new Map(previous);
    next.has(item.zotero_item_key) ? next.delete(item.zotero_item_key) : next.set(item.zotero_item_key, item);
    return next;
  });
  const submit = async (event) => {
    event.preventDefault();
    if (!selected.size || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const papers = [...selected.values()].map((item) => ({ ...item, path: item.pdfs[0] }));
      const result = await api("/api/project/papers", {
        method: "POST",
        body: JSON.stringify({ project: projectName, papers, translate }),
      });
      await onAdded(result);
      onClose();
    } catch (cause) {
      setError(cause.message);
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <dialog ref={dialogRef} className="detail-dialog collection-paper-dialog" onCancel={(event) => { event.preventDefault(); if (!submitting) onClose(); }}>
      <form onSubmit={submit}>
        <header className="detail-header">
          <div><div className="eyebrow">ADD ARTICLES</div><h2>追加文章</h2></div>
          <button type="button" className="icon-button detail-close" onClick={onClose} disabled={submitting} aria-label="关闭"><X size={17} /></button>
        </header>
        <p className="muted">从 Zotero 搜索带 PDF 的文章。已处理过的单篇项目会自动复用 Markdown 和图片。</p>
        <div className="search-box collection-paper-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、作者、DOI 或 citation key" autoFocus /></div>
        <div className="collection-paper-results">
          {results.length ? results.map((item) => (
            <label className={cn("item-card", !item.has_pdf && "disabled")} key={item.zotero_item_key}>
              <input type="checkbox" disabled={!item.has_pdf || submitting} checked={selected.has(item.zotero_item_key)} onChange={() => toggle(item)} />
              <div><strong>{item.title || "未命名条目"}</strong><div className="item-meta">{item.authors || "作者未知"} · {formatDate(item.date)}{item.citation_key && ` · ${item.citation_key}`}</div><small className={item.has_pdf ? "pdf-ready" : "pdf-missing"}>{item.has_pdf ? `${item.pdfs.length} 个 PDF 附件` : "没有可用 PDF 附件"}</small></div>
            </label>
          )) : <div className="empty-small">输入关键词搜索 Zotero 文章</div>}
        </div>
        <label className="check"><input type="checkbox" checked={translate} onChange={(event) => setTranslate(event.target.checked)} disabled={submitting} /> 同时生成中文翻译</label>
        {error && <div className="error-banner folder-error" role="alert"><CircleHelp size={16} />{error}</div>}
        <footer className="folder-footer"><button type="button" className="button ghost" onClick={onClose} disabled={submitting}>取消</button><button type="submit" className="button primary" disabled={submitting || !selected.size}>{submitting ? <LoaderCircle size={16} /> : <Plus size={16} />}{submitting ? "加入中..." : `追加 ${selected.size} 篇文章`}</button></footer>
      </form>
    </dialog>
  );
}

function ProjectDetail({ details, onClose, onOpenZotero, onDeleteProject, onRefreshProject }) {
  const project = details?.project || {};
  const papers = details?.papers || [];
  const projectName = project.name || project.citation_key || "";
  const [outputs, setOutputs] = useState([]);
  const [outputsOpen, setOutputsOpen] = useState(false);
  const [outputPreview, setOutputPreview] = useState(null);
  const [runs, setRuns] = useState([]);
  const [codexOpen, setCodexOpen] = useState(false);
  const [prompt, setPrompt] = useState(DEFAULT_CODEX_PROMPT);
  const [activeRunId, setActiveRunId] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [addPapersOpen, setAddPapersOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setOutputs([]);
    setOutputsOpen(false);
    setOutputPreview(null);
    setRuns([]);
    setCodexOpen(false);
    setActiveRunId("");
    setNotice("");
    setAddPapersOpen(false);
    if (!details) return undefined;
    Promise.all([
      api(`/api/project/outputs?name=${encodeURIComponent(projectName)}`),
      api(`/api/project/codex-runs?name=${encodeURIComponent(projectName)}`),
    ])
      .then(([outputData, runData]) => {
        if (cancelled) return;
        setOutputs(outputData.outputs || []);
        const availableRuns = runData.runs || [];
        const requestedRun =
          details.codexRunId &&
          availableRuns.some((run) => run.id === details.codexRunId)
            ? details.codexRunId
            : "";
        setRuns(availableRuns);
        setCodexOpen(Boolean(details.openCodex));
        setActiveRunId(
          requestedRun || (details.openCodex ? availableRuns[0]?.id || "" : ""),
        );
      })
      .catch((error) => {
        if (!cancelled) setNotice(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [details, projectName]);

  useEffect(() => {
    if (!activeRunId) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const [run, commandData] = await Promise.all([
          api(`/api/codex/run?id=${encodeURIComponent(activeRunId)}`),
          api(`/api/codex/command?id=${encodeURIComponent(activeRunId)}`),
        ]);
        if (cancelled) return;
        run.command = commandData.command;
        setRuns((previous) => [
          run,
          ...previous.filter((item) => item.id !== run.id),
        ]);
        if (run.prompt) setPrompt(run.prompt);
        if (["queued", "running", "cancelling"].includes(run.status))
          window.setTimeout(poll, 1000);
      } catch (error) {
        if (!cancelled) setNotice(error.message);
      }
    };
    poll();
    return () => {
      cancelled = true;
    };
  }, [activeRunId]);

  const selectedRun = runs.find((run) => run.id === activeRunId) || null;
  const copyText = async (value, message) => {
    try {
      await navigator.clipboard.writeText(value);
      setNotice(message);
    } catch (_) {
      setNotice("复制失败，请手动选择文本复制");
    }
  };
  const openFinder = async () => {
    setBusy("finder");
    try {
      await api("/api/open-finder", {
        method: "POST",
        body: JSON.stringify({ name: projectName }),
      });
      setNotice("已在 Finder 中定位项目文件夹");
    } catch (error) {
      setNotice(error.message);
    } finally {
      setBusy("");
    }
  };
  const showOutputs = async () => {
    setOutputsOpen(true);
    setOutputPreview(null);
    try {
      const data = await api(
        `/api/project/outputs?name=${encodeURIComponent(projectName)}`,
      );
      setOutputs(data.outputs || []);
    } catch (error) {
      setNotice(error.message);
    }
  };
  const viewOutput = async (path) => {
    try {
      setOutputPreview(
        await api(
          `/api/output?project=${encodeURIComponent(projectName)}&path=${encodeURIComponent(path)}`,
        ),
      );
    } catch (error) {
      setNotice(error.message);
    }
  };
  const openOutput = async (path) => {
    try {
      await api("/api/open-output", {
        method: "POST",
        body: JSON.stringify({ project: projectName, path }),
      });
      setNotice("已使用默认应用打开产出文件");
    } catch (error) {
      setNotice(error.message);
    }
  };
  const openTranslation = async (path) => {
    try {
      await api("/api/open-translation", {
        method: "POST",
        body: JSON.stringify({ project: projectName, path }),
      });
      setNotice("已使用默认应用打开翻译文件");
    } catch (error) {
      setNotice(error.message);
    }
  };
  const startCodex = async () => {
    setBusy("codex");
    try {
      const run = await api("/api/codex/run", {
        method: "POST",
        body: JSON.stringify({
          project: projectName,
          prompt: prompt.trim() || DEFAULT_CODEX_PROMPT,
        }),
      });
      setRuns((previous) => [
        run,
        ...previous.filter((item) => item.id !== run.id),
      ]);
      setActiveRunId(run.id);
      setCodexOpen(true);
      setNotice("Codex 会话已加入队列");
    } catch (error) {
      setNotice(error.message);
    } finally {
      setBusy("");
    }
  };
  const cancelCodex = async () => {
    if (!selectedRun) return;
    try {
      const run = await api("/api/codex/cancel", {
        method: "POST",
        body: JSON.stringify({ id: selectedRun.id }),
      });
      setRuns((previous) =>
        previous.map((item) => (item.id === run.id ? run : item)),
      );
    } catch (error) {
      setNotice(error.message);
    }
  };
  const showCodex = () => {
    setCodexOpen(true);
    if (!activeRunId && runs[0]) setActiveRunId(runs[0].id);
  };

  const deletePaper = async (paper) => {
    if (!window.confirm(`确定从项目中删除“${paper.metadata?.title || paper.citation_key}”？`)) return;
    setBusy(`delete:${paper.citation_key}`);
    try {
      await api("/api/project/paper/delete", { method: "POST", body: JSON.stringify({ project: projectName, citation_key: paper.citation_key }) });
      setNotice("文章已从项目中删除");
      await onRefreshProject?.(projectName);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setBusy("");
    }
  };

  if (!details) return null;
  return (
    <div
      className="detail-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="detail-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="detail-title"
      >
        <header className="detail-header">
          <div>
            <div className="eyebrow">PROJECT DETAILS</div>
            <h2 id="detail-title">{projectName}</h2>
          </div>
          <button
            className="icon-button detail-close"
            onClick={onClose}
            aria-label="关闭"
          >
            <X size={17} />
          </button>
        </header>
        <div className="detail-path" title={details.path}>
          {details.path}
        </div>
        <div className="detail-actions" aria-label="项目操作">
          <button
            className="button ghost"
            onClick={openFinder}
            disabled={busy === "finder"}
          >
            <FolderOpen size={15} />
            {busy === "finder" ? "打开中..." : "在 Finder 中打开"}
          </button>
          <button
            className="button ghost"
            onClick={() => copyText(details.path, "目录路径已复制")}
          >
            <Copy size={15} />
            复制目录路径
          </button>
          <button className="button ghost" onClick={showOutputs}>
            <FileText size={15} />
            查看 outputs/{outputs.length ? ` (${outputs.length})` : ""}
          </button>
          {project.translation_exists && project.translation_path && (
            <button
              className="button ghost"
              onClick={() => openTranslation(project.translation_path)}
            >
              <Languages size={15} />
              打开翻译
            </button>
          )}
          <button
            className="button primary"
            onClick={() => {
              setCodexOpen(true);
              setActiveRunId("");
              setPrompt(DEFAULT_CODEX_PROMPT);
            }}
          >
            <Terminal size={15} />
            进行 Codex 会话
          </button>
          {project.project_type === "collection" && (
            <button className="button ghost" onClick={() => setAddPapersOpen(true)}>
              <Plus size={15} />
              追加文章
            </button>
          )}
          {runs.length > 0 && (
            <button className="button ghost" onClick={showCodex}>
              <Eye size={15} />
              查看 Codex 会话 ({runs.length})
            </button>
          )}
          <button
            className="button danger"
            onClick={() => onDeleteProject(projectName)}
          >
            <Trash2 size={15} />
            删除项目
          </button>
        </div>
        {notice && (
          <div className="detail-notice" role="status">
            {notice}
            <button
              className="icon-button"
              onClick={() => setNotice("")}
              aria-label="关闭提示"
            >
              <X size={13} />
            </button>
          </div>
        )}
        <div className="detail-summary">
          <span>
            {project.project_type === "collection" ? "多文章项目" : "单篇项目"}
          </span>
          <span>{papers.length} 篇文章</span>
          <span>
            {project.tags?.length ? project.tags.join(" · ") : "无标签"}
          </span>
        </div>
        <div className="detail-papers">
          {papers.map((paper) => {
            const metadata = paper.metadata || {};
            return (
              <article className="detail-paper" key={paper.citation_key}>
                <div className="detail-paper-head">
                  <div>
                    <h3>{metadata.title || paper.citation_key}</h3>
                    <p>
                      {metadata.authors || "作者未知"} ·{" "}
                      {formatDate(metadata.date)}
                    </p>
                  </div>
                  <div className="detail-paper-actions">
                    <span
                      className={cn(
                        "artifact-pill",
                        paper.full_exists && "ready",
                      )}
                    >
                      {paper.full_exists ? "Markdown 已就绪" : "等待提取"}
                    </span>
                    {paper.translation_exists &&
                      paper.translation_path &&
                      paper.translation_path !== project.translation_path && (
                        <button
                          className="icon-button translation-button"
                          onClick={() =>
                            openTranslation(paper.translation_path)
                          }
                        >
                          <Languages size={13} />
                          打开翻译
                        </button>
                      )}
                    {project.project_type === "collection" && (
                      <button className="icon-button delete-icon-button" onClick={() => deletePaper(paper)} disabled={busy === `delete:${paper.citation_key}`} title="从项目中删除" aria-label={`删除 ${metadata.title || paper.citation_key}`}>
                        {busy === `delete:${paper.citation_key}` ? <LoaderCircle size={13} /> : <Trash2 size={13} />}
                      </button>
                    )}
                  </div>
                </div>
                {metadata.abstract && (
                  <p className="detail-abstract">{metadata.abstract}</p>
                )}
                <div className="detail-paper-meta">
                  {metadata.doi && <span>DOI {metadata.doi}</span>}
                  {paper.citation_key && (
                    <span>Citation key {paper.citation_key}</span>
                  )}
                </div>
              </article>
            );
          })}
          {!papers.length && (
            <div className="empty-small">项目中还没有文章</div>
          )}
        </div>
        {outputsOpen && (
          <section className="detail-section">
            <div className="detail-section-head">
              <div>
                <h3>outputs/ 产出</h3>
                <p>点击文件名查看内容，或使用默认应用打开。</p>
              </div>
              <button
                className="icon-button"
                onClick={() => setOutputsOpen(false)}
                aria-label="收起 outputs"
              >
                <X size={14} />
              </button>
            </div>
            {outputs.length ? (
              <div className="output-file-list">
                {outputs.map((item) => (
                  <div className="output-file" key={item.path}>
                    <div className="output-file-name">
                      <FileText size={15} />
                      <strong>{item.path}</strong>
                      <small>
                        {item.updated_at} · {Math.ceil(item.size / 1024)} KB
                      </small>
                    </div>
                    <div className="output-file-actions">
                      <button
                        className="icon-button"
                        onClick={() => viewOutput(item.path)}
                      >
                        查看内容
                      </button>
                      <button
                        className="icon-button"
                        onClick={() => openOutput(item.path)}
                      >
                        <ExternalLink size={13} />
                        在默认应用中打开
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-small">outputs/ 为空</div>
            )}
            {outputPreview && (
              <div className="output-preview">
                <div className="output-preview-head">
                  <strong>{outputPreview.path}</strong>
                  <button
                    className="icon-button"
                    onClick={() => setOutputPreview(null)}
                    aria-label="关闭预览"
                  >
                    <X size={13} />
                  </button>
                </div>
                <pre>{outputPreview.content}</pre>
              </div>
            )}
          </section>
        )}
        {codexOpen && (
          <section className="detail-section codex-section">
            <div className="detail-section-head">
              <div>
                <h3>Codex 会话</h3>
                <p>提示词会在项目目录中执行，终端输出会实时显示。</p>
              </div>
              <button
                className="icon-button"
                onClick={() => setCodexOpen(false)}
                aria-label="收起 Codex"
              >
                <X size={14} />
              </button>
            </div>
            <label className="codex-prompt-field">
              Prompt
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                disabled={
                  !!selectedRun &&
                  ["queued", "running", "cancelling"].includes(
                    selectedRun.status,
                  )
                }
              />
            </label>
            <div className="codex-controls">
              <button
                className="button primary"
                onClick={startCodex}
                disabled={
                  busy === "codex" ||
                  (!!selectedRun &&
                    ["queued", "running", "cancelling"].includes(
                      selectedRun.status,
                    ))
                }
              >
                <Play size={14} />
                {busy === "codex" ? "提交中..." : "开始 Codex 会话"}
              </button>
              {selectedRun &&
                ["queued", "running", "cancelling"].includes(
                  selectedRun.status,
                ) && (
                  <button className="button ghost" onClick={cancelCodex}>
                    <Square size={13} />
                    取消会话
                  </button>
                )}
              {runs.length > 0 && (
                <span className="muted">已有 {runs.length} 次会话</span>
              )}
            </div>
            {selectedRun && (
              <div className="codex-run-panel">
                <div className="codex-run-status">
                  <strong>
                    {selectedRun.status === "completed"
                      ? "已完成"
                      : selectedRun.status === "failed"
                        ? "失败"
                        : selectedRun.status === "running"
                          ? "执行中"
                          : selectedRun.status === "queued"
                            ? "排队中"
                            : selectedRun.status}
                  </strong>
                  <span>
                    {selectedRun.session_id
                      ? `session ${selectedRun.session_id}`
                      : "等待 Codex 返回 session"}
                  </span>
                </div>
                <pre className="codex-terminal">
                  {selectedRun.log || "等待终端输出..."}
                </pre>
                {selectedRun.command && (
                  <div className="codex-command">
                    <code>{selectedRun.command}</code>
                    <button
                      className="icon-button"
                      onClick={() =>
                        copyText(selectedRun.command, "Codex 命令已复制")
                      }
                    >
                      <Copy size={13} />
                      复制 Codex 命令
                    </button>
                  </div>
                )}
              </div>
            )}
            {runs.length > 0 && (
              <div className="codex-history">
                <h4>历史会话</h4>
                {runs.map((run) => (
                  <button
                    className={cn(
                      "codex-history-row",
                      run.id === activeRunId && "active",
                    )}
                    key={run.id}
                    onClick={() => {
                      setActiveRunId(run.id);
                      setPrompt(run.prompt || DEFAULT_CODEX_PROMPT);
                    }}
                  >
                    <span>
                      {run.created_at?.replace("T", " ").slice(0, 16) ||
                        "未知时间"}
                    </span>
                    <b>{run.status}</b>
                    <small>{run.session_id || "尚未建立 session"}</small>
                  </button>
                ))}
              </div>
            )}
          </section>
        )}
        <footer className="detail-footer">
          <span>工作区目录中的项目详情</span>
          <button className="button ghost" onClick={onOpenZotero}>
            <BookOpen size={15} />
            打开 Zotero 库
          </button>
        </footer>
      </section>
      {addPapersOpen && <AddCollectionPapersDialog projectName={projectName} onClose={() => setAddPapersOpen(false)} onAdded={async (result) => { setNotice(`已追加 ${result.task_count} 篇文章${result.skipped?.length ? `，跳过 ${result.skipped.length} 篇重复文章` : ""}`); await onRefreshProject?.(projectName); }} />}
    </div>
  );
}

function SettingsView() {
  const [config, setConfig] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const load = async () => {
    try {
      setError("");
      setConfig(await api("/api/config"));
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
  }, []);
  const save = async (event) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await api("/api/config", {
        method: "POST",
        body: JSON.stringify({
          concurrency: Number(config.concurrency),
          codex_concurrency: Number(config.codex_concurrency),
        }),
      });
      setConfig(await api("/api/config"));
      setSaved(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };
  const change = (key, value) => {
    setConfig((previous) => ({ ...previous, [key]: value }));
    setSaved(false);
  };
  return (
    <>
      <Header
        eyebrow="LOCAL SETTINGS"
        title="设置"
        onRefresh={saving ? undefined : load}
        action={
          <button
            form="settings-form"
            type="submit"
            className="button primary"
            disabled={!config || saving}
          >
            {saving ? <LoaderCircle size={16} /> : <Check size={16} />}
            {saving ? "保存中..." : "保存设置"}
          </button>
        }
      />
      {error && (
        <div className="error-banner" role="alert">
          <CircleHelp size={16} />
          {error}
        </div>
      )}
      {saved && (
        <div className="queue-summary" role="status">
          <Check size={16} />
          <span>设置已保存</span>
        </div>
      )}
      {config ? (
        <form id="settings-form" className="settings-form" onSubmit={save}>
          <h2>任务并发</h2>
          <div className="form-grid">
            <label>
              PDF 提取最大并发数
              <input
                type="number"
                min="1"
                max="8"
                step="1"
                required
                disabled={saving}
                value={config.concurrency}
                onChange={(event) => change("concurrency", event.target.value)}
              />
            </label>
            <label>
              Codex 最大并发数
              <input
                type="number"
                min="1"
                max="8"
                step="1"
                required
                disabled={saving}
                value={config.codex_concurrency}
                onChange={(event) =>
                  change("codex_concurrency", event.target.value)
                }
              />
            </label>
          </div>
          <h2>本地目录</h2>
          <dl className="settings-paths">
            <dt>Zotero 数据目录</dt>
            <dd>{config.zotero_dir}</dd>
            <dt>工作区目录</dt>
            <dd>{config.workspaces_dir}</dd>
          </dl>
        </form>
      ) : (
        <div className="empty-small">
          {error ? "配置加载失败" : "加载中..."}
        </div>
      )}
    </>
  );
}

function CreateFolderDialog({ folders, onCreated, onClose }) {
  const dialogRef = useRef(null);
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const dialog = dialogRef.current;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  const submit = async (event) => {
    event.preventDefault();
    if (submitting || !name.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      await api("/api/folders", {
        method: "POST",
        body: JSON.stringify({
          action: "create",
          name: name.trim(),
          parent_id: parentId || null,
        }),
      });
      await onCreated();
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <dialog
      ref={dialogRef}
      className="detail-dialog folder-dialog"
      aria-labelledby="folder-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!submitting) onClose();
      }}
    >
      <form onSubmit={submit}>
        <header className="detail-header">
          <h2 id="folder-dialog-title">新建目录</h2>
          <button
            type="button"
            className="icon-button detail-close"
            aria-label="关闭"
            disabled={submitting}
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </header>
        <div className="form-grid folder-form">
          <label>
            目录名称
            <input
              autoFocus
              required
              maxLength={80}
              value={name}
              disabled={submitting}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            上级目录
            <select
              value={parentId}
              disabled={submitting}
              onChange={(event) => setParentId(event.target.value)}
            >
              <option value="">无（顶层目录）</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {"　".repeat(folder.depth || 0)}
                  {folder.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && (
          <div className="error-banner folder-error" role="alert">
            <CircleHelp size={16} />
            {error}
          </div>
        )}
        <footer className="folder-footer">
          <button
            type="button"
            className="button ghost"
            disabled={submitting}
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={submitting || !name.trim()}
          >
            {submitting ? <LoaderCircle size={16} /> : <Plus size={16} />}
            {submitting ? "创建中..." : "创建目录"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}

export default function Page() {
  const [view, setView] = useState("projects");
  const [projects, setProjects] = useState([]);
  const [folders, setFolders] = useState([]);
  const [folderData, setFolderData] = useState({
    counts: {},
    project_folders: {},
  });
  const [folderId, setFolderId] = useState("");
  const [collections, setCollections] = useState([]);
  const [selectedCollection, setSelectedCollection] = useState("");
  const [collectionData, setCollectionData] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [todos, setTodos] = useState([]);
  const [projectDetails, setProjectDetails] = useState(null);
  const [error, setError] = useState("");
  const [importResult, setImportResult] = useState("");
  const [folderDialogOpen, setFolderDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState("");
  const loadWorkspace = useCallback(async () => {
    const [projectData, folderResponse] = await Promise.all([
      api("/api/projects"),
      api("/api/folders"),
    ]);
    setProjects(projectData.projects || []);
    setTodos((projectData.projects || []).filter((project) => project.todo));
    setFolderData(folderResponse);
    const byId = Object.fromEntries(
      (folderResponse.folders || []).map((item) => [item.id, item]),
    );
    const depth = (item) =>
      item.parent_id && byId[item.parent_id]
        ? 1 + depth(byId[item.parent_id])
        : 0;
    setFolders(
      (folderResponse.folders || []).map((item) => ({
        ...item,
        depth: depth(item),
      })),
    );
  }, []);
  const loadCollections = useCallback(async () => {
    const data = await api("/api/zotero/collections");
    const items = data.collections || [];
    const byId = new Map(items.map((item) => [item.id, item]));
    const visibleIds = new Set();
    for (const item of items) {
      if (!item.item_count) continue;
      let current = item;
      while (current && !visibleIds.has(current.id)) {
        visibleIds.add(current.id);
        current = byId.get(current.parent_id);
      }
    }
    const visible = items.filter((item) => visibleIds.has(item.id));
    setCollections(visible);
    setSelectedCollection((previous) =>
      visible.some((item) => item.key === previous) ? previous : "",
    );
  }, []);
  const loadTasks = useCallback(async () => {
    const data = await api("/api/tasks");
    setTasks(data.tasks || []);
  }, []);
  const refresh = useCallback(async () => {
    try {
      setError("");
      await Promise.all([loadWorkspace(), loadCollections(), loadTasks()]);
    } catch (e) {
      setError(e.message);
    }
  }, [loadWorkspace, loadCollections, loadTasks]);
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!selectedCollection) {
      setCollectionData(null);
      return;
    }
    api(`/api/zotero/collection?key=${encodeURIComponent(selectedCollection)}`)
      .then(setCollectionData)
      .catch((e) => setError(e.message));
  }, [selectedCollection]);
  const createFromZotero = async ({
    papers,
    mode,
    projectName,
    defaultProjectName = collectionData?.collection?.name,
    folderIds,
    tags,
    translate,
    autoCodex,
    prompt,
  }) => {
    const counts = { created: 0, linked: 0, skipped: 0 };
    try {
      setError("");
      setImportResult("");
      const common = {
        tags: tags
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        folder_ids: folderIds,
        translate,
        auto_codex: autoCodex,
        codex_prompt: prompt,
      };
      if (mode === "single") {
        const uniquePapers = [
          ...new Map(
            papers.map((paper) => [
              paper.zotero_item_key || paper.citation_key || paper.path,
              paper,
            ]),
          ).values(),
        ];
        for (const paper of uniquePapers) {
          const result = await api("/api/projects", {
            method: "POST",
            body: JSON.stringify({
              ...common,
              project_type: "paper",
              papers: [paper],
              skip_existing: true,
            }),
          });
          counts[result.outcome || "created"] += 1;
          setImportResult(
            `新建 ${counts.created} 篇 · 加入目录 ${counts.linked} 篇 · 已有跳过 ${counts.skipped} 篇`,
          );
        }
      } else
        await api("/api/projects", {
          method: "POST",
          body: JSON.stringify({
            ...common,
            project_type: "collection",
            name: projectName || defaultProjectName,
            papers,
          }),
        });
      await Promise.all([loadTasks(), loadWorkspace()]);
      setView("tasks");
    } catch (e) {
      setError(e.message);
    }
  };
  const openProject = async (name, options = {}) => {
    try {
      setError("");
      const data = await api(`/api/project?name=${encodeURIComponent(name)}`);
      setProjectDetails({
        ...data,
        openCodex: Boolean(options.codexRunId),
        codexRunId: options.codexRunId || "",
      });
    } catch (e) {
      setError(e.message);
    }
  };
  const openCodexTask = (task) =>
    openProject(task.project_slug, { codexRunId: task.run_id });
  const toggleTodo = async (project, state) => {
    const name = project.name || project.citation_key;
    try {
      setError("");
      await api("/api/project/todo", {
        method: "POST",
        body: JSON.stringify({ project: name, state }),
      });
      await loadWorkspace();
    } catch (e) {
      setError(e.message);
    }
  };
  const deleteProject = async (name) => {
    setDeleteTarget("");
    setProjectDetails(null);
    setError("");
    try {
      await Promise.all([loadWorkspace(), loadTasks()]);
    } catch (cause) {
      setError(cause.message);
    }
  };
  const requestProjectDeletion = (name) => {
    setProjectDetails(null);
    setDeleteTarget(name);
  };
  const deleteFolder = async (folder) => {
    if (!window.confirm(`删除目录“${folder.name}”？其中的子目录和文章会移到未分类。`)) return;
    try {
      setError("");
      const result = await api("/api/folders", {
        method: "POST",
        body: JSON.stringify({ action: "delete", id: folder.id }),
      });
      if ((result.removed_ids || []).includes(folderId)) setFolderId("");
      await loadWorkspace();
    } catch (cause) {
      setError(cause.message);
    }
  };
  const workspaceSidebar = (
    <section className="sidebar-block">
      <div className="sidebar-block-head">
        <span>工作区目录</span>
        <button
          title="新建目录"
          aria-label="新建目录"
          onClick={() => setFolderDialogOpen(true)}
        >
          <Plus size={15} />
        </button>
      </div>
      <WorkspaceTree
        folders={folders}
        counts={{
          total: projects.length,
          unfiled: projects.filter((item) => !(item.folder_ids || []).length)
            .length,
          byFolder: folderData.counts,
        }}
        selected={folderId}
        onSelect={(value) => {
          setFolderId(value);
          setView("projects");
        }}
        onDelete={deleteFolder}
      />
    </section>
  );
  const taskCount = tasks.filter((task) =>
    ["queued", "running", "cancelling"].includes(task.status),
  ).length;
  return (
    <AppShell
      view={view}
      setView={setView}
      taskCount={taskCount}
      todoCount={todos.length}
      workspaceSidebar={workspaceSidebar}
    >
      {importResult && (
        <div className="queue-summary" role="status">
          <Check size={16} />
          <span>{importResult}</span>
          <button
            className="icon-button"
            onClick={() => setImportResult("")}
            aria-label="关闭导入结果"
          >
            <X size={15} />
          </button>
        </div>
      )}
      {error && (
        <div className="error-banner">
          <CircleHelp size={16} />
          {error}
          <button onClick={() => setError("")}>
            <X size={15} />
          </button>
        </div>
      )}
      {view === "settings" && <SettingsView />}
      {view === "projects" && (
        <ProjectsView
          projects={projects}
          folders={folders}
          folderData={folderData}
          folderId={folderId}
          setFolderId={setFolderId}
          setView={setView}
          refresh={refresh}
          onOpenProject={openProject}
          onDeleteProject={requestProjectDeletion}
          onToggleTodo={toggleTodo}
        />
      )}
      {view === "todos" && (
        <ProjectsView
          projects={todos}
          folders={folders}
          folderData={folderData}
          folderId=""
          setFolderId={setFolderId}
          setView={setView}
          refresh={refresh}
          onOpenProject={openProject}
          onDeleteProject={requestProjectDeletion}
          onToggleTodo={toggleTodo}
          todoOnly
        />
      )}
      {view === "zotero" && (
        <ZoteroView
          collections={collections}
          folders={folders}
          selectedCollection={selectedCollection}
          setSelectedCollection={setSelectedCollection}
          collectionData={collectionData}
          refresh={refresh}
          onCreate={createFromZotero}
        />
      )}
      {view === "tasks" && (
        <TasksView
          tasks={tasks}
          refresh={async () => {
            await loadTasks();
            await loadWorkspace();
          }}
          onOpenCodexTask={openCodexTask}
        />
      )}
      {view === "create" && (
        <CreateProjectView folders={folders} onCreate={createFromZotero} />
      )}
      <ProjectDetail
        details={projectDetails}
        onClose={() => setProjectDetails(null)}
        onRefreshProject={openProject}
        onOpenZotero={() => {
          setProjectDetails(null);
          setView("zotero");
        }}
        onDeleteProject={requestProjectDeletion}
      />
      {deleteTarget && (
        <DeleteProjectDialog
          projectName={deleteTarget}
          onClose={() => setDeleteTarget("")}
          onDeleted={deleteProject}
        />
      )}
      {folderDialogOpen && (
        <CreateFolderDialog
          folders={folders}
          onCreated={loadWorkspace}
          onClose={() => setFolderDialogOpen(false)}
        />
      )}
    </AppShell>
  );
}
