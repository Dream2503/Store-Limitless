import {Fragment, useEffect, useMemo, useState} from "react";
import {formatFileSize} from "../utils.js";

const HOME_PATH = "/home";
const TWO_PI = Math.PI * 2;

function normalizePath(value) {
    let path = String(value ?? "").trim().split(String.fromCharCode(92)).join("/");
    while (path.includes("//")) path = path.replace("//", "/");
    if (path.endsWith("/")) path = path.slice(0, -1);
    if (!path || path === "/" || path === "home" || path === HOME_PATH) return HOME_PATH;
    if (path.startsWith("home/")) return "/" + path;
    if (path.startsWith(HOME_PATH + "/")) return path;
    while (path.startsWith("/")) path = path.slice(1);
    return HOME_PATH + "/" + path;
}

function readFileSize(file) {
    const raw = file?.size_bytes ?? file?.file_size_bytes ?? file?.size ?? file?.file_size ?? 0;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function createFolderNode(path, name, parent = null) {
    return {
        kind: "folder",
        path,
        name,
        parent,
        children: [],
        childMap: new Map(),
        files: [],
        isDeleted: false,
        metadataPresent: false,
        bytesAll: 0,
        bytesActive: 0,
        bytesTrash: 0,
        directAllBytes: 0,
        directActiveBytes: 0,
        directTrashBytes: 0,
        allFileCount: 0,
        activeFileCount: 0,
        trashFileCount: 0,
        folderCount: 0,
        activeFolderCount: 0,
        trashFolderCount: 0,
    };
}

function ensureFolder(root, folderMap, path) {
    const normalized = normalizePath(path);
    if (folderMap.has(normalized)) return folderMap.get(normalized);

    const parts = normalized.split("/").filter(Boolean).slice(1);
    let current = root;
    let currentPath = HOME_PATH;

    for (const part of parts) {
        currentPath += "/" + part;
        let child = folderMap.get(currentPath);
        if (!child) {
            child = createFolderNode(currentPath, part, current);
            current.children.push(child);
            current.childMap.set(part, child);
            folderMap.set(currentPath, child);
        }
        current = child;
    }
    return current;
}

function calculateNodeTotals(node, inheritedTrash = false) {
    const folderIsTrash = inheritedTrash || (node !== null && node.isDeleted);
    let directAll = 0;
    let directActive = 0;
    let directTrash = 0;
    let directAllCount = 0;
    let directActiveCount = 0;
    let directTrashCount = 0;

    for (const file of node.files) {
        file.isTrash = folderIsTrash || Boolean(file.isDeleted);
        directAll += file.size;
        directAllCount += file.isUnassigned ? 0 : 1;
        if (file.isTrash) {
            directTrash += file.size;
            directTrashCount += file.isUnassigned ? 0 : 1;
        } else {
            directActive += file.size;
            directActiveCount += file.isUnassigned ? 0 : 1;
        }
    }

    node.directAllBytes = directAll;
    node.directActiveBytes = directActive;
    node.directTrashBytes = directTrash;
    node.bytesAll = directAll;
    node.bytesActive = directActive;
    node.bytesTrash = directTrash;
    node.allFileCount = directAllCount;
    node.activeFileCount = directActiveCount;
    node.trashFileCount = directTrashCount;
    node.folderCount = 0;
    node.activeFolderCount = node !== null && node !== undefined && node.path !== HOME_PATH && !folderIsTrash ? 1 : 0;
    node.trashFolderCount = node !== null && node !== undefined && node.path !== HOME_PATH && folderIsTrash ? 1 : 0;

    for (const child of node.children) {
        calculateNodeTotals(child, folderIsTrash);
        node.bytesAll += child.bytesAll;
        node.bytesActive += child.bytesActive;
        node.bytesTrash += child.bytesTrash;
        node.allFileCount += child.allFileCount;
        node.activeFileCount += child.activeFileCount;
        node.trashFileCount += child.trashFileCount;
        node.folderCount += 1 + child.folderCount;
        node.activeFolderCount += child.activeFolderCount;
        node.trashFolderCount += child.trashFolderCount;
    }

    node.children.sort((a, b) => b.bytesAll - a.bytesAll || a.name.localeCompare(b.name));
    node.files.sort((a, b) => b.size - a.size || a.name.localeCompare(b.name));
}

function buildStorageTree(files, folders, reportedStorage) {
    const root = createFolderNode(HOME_PATH, "Home");
    const folderMap = new Map([[HOME_PATH, root]]);

    const folderRecords = Array.isArray(folders) ? [...folders] : [];
    folderRecords.sort((a, b) => normalizePath(a.path).length - normalizePath(b.path).length);

    for (const record of folderRecords) {
        if (!record || typeof record.path !== "string") continue;
        const path = normalizePath(record.path);
        if (path === HOME_PATH) continue;
        const node = ensureFolder(root, folderMap, path);
        node.name = path.split("/").filter(Boolean).pop() || node.name;
        node.id = record.id;
        node.metadataPresent = true;
        node.isDeleted = Boolean(record.is_deleted ?? record.deleted_at);
        node.modifiedAt = record.modified_at ?? record.updated_at ?? null;
    }

    const seenFiles = new Set();
    for (const file of Array.isArray(files) ? files : []) {
        if (!file || file.name === ".__folder__") continue;
        const directory = normalizePath(file.directory ?? file.directory_path ?? HOME_PATH);
        const node = ensureFolder(root, folderMap, directory);
        const identity = file.id ?? file.fid ?? file.file_id;
        const uniqueKey = identity !== undefined && identity !== null
            ? "id:" + String(identity)
            : "path:" + directory + "/" + String(file.name ?? "") + ":" + String(file.modified_at ?? "") + ":" + String(file.size ?? "");
        if (seenFiles.has(uniqueKey)) continue;
        seenFiles.add(uniqueKey);
        node.files.push({
            kind: "file",
            id: identity ?? uniqueKey,
            key: uniqueKey,
            name: String(file.name ?? "Unnamed file"),
            size: readFileSize(file),
            isDeleted: Boolean(file.is_deleted ?? file.deleted_at),
            directory,
            isUnassigned: false,
            isTrash: false,
        });
    }

    calculateNodeTotals(root);
    const fileRecordBytes = root.bytesAll;
    const reported = Number(reportedStorage);
    const hasReportedTotal = reportedStorage !== null && reportedStorage !== undefined && reportedStorage !== ""
        && Number.isFinite(reported) && reported >= 0;
    const reportedBytes = hasReportedTotal ? reported : fileRecordBytes;
    const unassignedBytes = Math.max(0, Math.floor(reportedBytes - fileRecordBytes));

    if (unassignedBytes > 0) {
        root.files.push({
            kind: "file",
            id: "unassigned-storage",
            key: "unassigned-storage",
            name: "Unassigned / unindexed storage",
            size: unassignedBytes,
            isDeleted: false,
            directory: HOME_PATH,
            isUnassigned: true,
            isTrash: false,
        });
        calculateNodeTotals(root);
    }

    return {
        root,
        reportedBytes,
        fileRecordBytes,
        unassignedBytes,
        hasReportedTotal,
        discrepancyBytes: Math.max(0, fileRecordBytes - reportedBytes),
    };
}

function bytesFor(node, filter) {
    if (filter === "active") return node.bytesActive;
    if (filter === "trash") return node.bytesTrash;
    return node.bytesAll;
}

function directBytesFor(node, filter) {
    if (filter === "active") return node.directActiveBytes;
    if (filter === "trash") return node.directTrashBytes;
    return node.directAllBytes;
}

function fileCountFor(node, filter) {
    if (filter === "active") return node.activeFileCount;
    if (filter === "trash") return node.trashFileCount;
    return node.allFileCount;
}

function fileMatchesFilter(file, filter) {
    if (filter === "active") return !file.isTrash;
    if (filter === "trash") return file.isTrash;
    return true;
}

function findFolder(root, path) {
    if (!path || path === HOME_PATH) return root;
    const parts = normalizePath(path).split("/").filter(Boolean).slice(1);
    let current = root;
    for (const part of parts) {
        current = current.childMap.get(part);
        if (!current) return root;
    }
    return current;
}

function folderColor(path, depth = 0, kind = "folder") {
    let hash = 0;
    const key = String(path || "root") + ":" + kind;
    for (let i = 0; i < key.length; i += 1) {
        hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0;
    }
    const hue = Math.abs(hash) % 360;
    const lightness = Math.max(43, 62 - depth * 5);
    const saturation = kind === "files" ? 58 : 64;
    return "hsl(" + hue + " " + saturation + "% " + lightness + "%)";
}

function chartChildren(node, filter) {
    const result = node.children
        .filter((child) => bytesFor(child, filter) > 0)
        .map((child) => ({
            kind: "folder",
            name: child.name,
            path: child.path,
            size: bytesFor(child, filter),
            count: fileCountFor(child, filter),
            node: child,
            color: folderColor(child.path, child.path.split("/").length - 2),
        }));

    const directBytes = directBytesFor(node, filter);
    if (directBytes > 0) {
        result.push({
            kind: "files",
            name: "Files in " + (node.name === "Home" ? "root" : node.name),
            path: node.path,
            size: directBytes,
            count: node.files.filter((file) => fileMatchesFilter(file, filter) && !file.isUnassigned).length,
            node,
            color: folderColor(node.path, node.path.split("/").length, "files"),
        });
    }
    return result.sort((a, b) => b.size - a.size);
}

function polarPoint(cx, cy, radius, angle) {
    return {
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle),
    };
}

function donutArcPath(cx, cy, innerRadius, outerRadius, startAngle, endAngle) {
    const span = endAngle - startAngle;
    if (span >= TWO_PI - 0.001) {
        const outerStart = polarPoint(cx, cy, outerRadius, startAngle);
        const outerMiddle = polarPoint(cx, cy, outerRadius, startAngle + Math.PI);
        const innerStart = polarPoint(cx, cy, innerRadius, startAngle);
        const innerMiddle = polarPoint(cx, cy, innerRadius, startAngle + Math.PI);
        return "M " + outerStart.x + " " + outerStart.y
            + " A " + outerRadius + " " + outerRadius + " 0 1 1 " + outerMiddle.x + " " + outerMiddle.y
            + " A " + outerRadius + " " + outerRadius + " 0 1 1 " + outerStart.x + " " + outerStart.y
            + " L " + innerStart.x + " " + innerStart.y
            + " A " + innerRadius + " " + innerRadius + " 0 1 0 " + innerMiddle.x + " " + innerMiddle.y
            + " A " + innerRadius + " " + innerRadius + " 0 1 0 " + innerStart.x + " " + innerStart.y + " Z";
    }

    const outerStart = polarPoint(cx, cy, outerRadius, startAngle);
    const outerEnd = polarPoint(cx, cy, outerRadius, endAngle);
    const innerEnd = polarPoint(cx, cy, innerRadius, endAngle);
    const innerStart = polarPoint(cx, cy, innerRadius, startAngle);
    const largeArc = span > Math.PI ? 1 : 0;
    return "M " + outerStart.x + " " + outerStart.y
        + " A " + outerRadius + " " + outerRadius + " 0 " + largeArc + " 1 " + outerEnd.x + " " + outerEnd.y
        + " L " + innerEnd.x + " " + innerEnd.y
        + " A " + innerRadius + " " + innerRadius + " 0 " + largeArc + " 0 " + innerStart.x + " " + innerStart.y + " Z";
}

function layoutRings(node, filter, startAngle = -Math.PI / 2, endAngle = Math.PI * 1.5, depth = 0, output = []) {
    const children = chartChildren(node, filter);
    const total = children.reduce((sum, child) => sum + child.size, 0);
    if (!total || depth >= 4) return output;

    let angle = startAngle;
    for (const child of children) {
        const nextAngle = angle + ((endAngle - startAngle) * child.size / total);
        const gap = Math.min(0.006, (nextAngle - angle) / 8);
        output.push({
            item: child,
            depth,
            startAngle: angle + gap,
            endAngle: nextAngle - gap,
            innerRadius: 70 + depth * 55,
            outerRadius: 121 + depth * 55,
        });
        if (child.kind === "folder") {
            layoutRings(child.node, filter, angle, nextAngle, depth + 1, output);
        }
        angle = nextAngle;
    }
    return output;
}

function layoutTreemap(node, x, y, width, height, depth, filter, output = []) {
    const children = chartChildren(node, filter);
    const total = children.reduce((sum, child) => sum + child.size, 0);
    if (!total || width < 5 || height < 5 || depth >= 5) return output;

    const horizontalSplit = width >= height;
    let cursor = horizontalSplit ? x : y;
    const usableLength = Math.max(0, (horizontalSplit ? width : height) - 3 * children.length);
    for (const child of children) {
        const fraction = child.size / total;
        const length = usableLength * fraction;
        const rect = horizontalSplit
            ? {x: cursor + 1.5, y: y + 1.5, width: Math.max(0, length), height: Math.max(0, height - 3)}
            : {x: x + 1.5, y: cursor + 1.5, width: Math.max(0, width - 3), height: Math.max(0, length)};
        output.push({item: child, depth, ...rect});
        if (child.kind === "folder") {
            layoutTreemap(child.node, rect.x + 2, rect.y + 2, rect.width - 4, rect.height - 4, depth + 1, filter, output);
        }
        cursor += length + 3;
    }
    return output;
}

function folderTrail(node) {
    const parts = [];
    let current = node;
    while (current) {
        parts.unshift(current);
        current = current.parent;
    }
    return parts;
}

function percentOf(size, total) {
    if (!total || !size) return 0;
    return Math.min(100, Math.max(0, size / total * 100));
}

export default function StorageAnalyzer({files, folders, storageUsed, loading, onClose}) {
    const [filter, setFilter] = useState("all");
    const [chartType, setChartType] = useState("rings");
    const [focusedPath, setFocusedPath] = useState(HOME_PATH);
    const [expandedPaths, setExpandedPaths] = useState(() => new Set([HOME_PATH]));

    const tree = useMemo(
        () => buildStorageTree(files, folders, storageUsed),
        [files, folders, storageUsed],
    );
    const root = tree.root;
    const focusedNode = findFolder(root, focusedPath);
    const totalBytes = bytesFor(root, filter);
    const totalFiles = fileCountFor(root, filter);
    const totalFolders = filter === "active" ? root.activeFolderCount : filter === "trash" ? root.trashFolderCount : root.folderCount;
    const largestFolder = [...root.children]
        .filter((folder) => filter === "trash" ? folder.bytesTrash > 0 : filter === "active" ? folder.bytesActive > 0 : folder.bytesAll > 0)
        .sort((a, b) => bytesFor(b, filter) - bytesFor(a, filter))[0] || null;
    const chartRootBytes = bytesFor(focusedNode, filter);
    const ringSegments = useMemo(() => layoutRings(focusedNode, filter), [focusedNode, filter]);
    const treemapRects = useMemo(
        () => layoutTreemap(focusedNode, 8, 8, 984, 584, 0, filter),
        [focusedNode, filter],
    );
    const trail = folderTrail(focusedNode);

    useEffect(() => {
        const onKeyDown = (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                onClose();
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [onClose]);

    useEffect(() => {
        setFocusedPath(HOME_PATH);
        setExpandedPaths(new Set([HOME_PATH]));
    }, [filter]);

    const expandToPath = (path) => {
        const normalized = normalizePath(path);
        const next = new Set(expandedPaths);
        next.add(HOME_PATH);
        let current = HOME_PATH;
        for (const part of normalized.split("/").filter(Boolean).slice(1)) {
            current += "/" + part;
            next.add(current);
        }
        setExpandedPaths(next);
    };

    const focusFolder = (node) => {
        setFocusedPath(node.path);
        expandToPath(node.path);
    };

    const toggleExpanded = (path) => {
        setExpandedPaths((current) => {
            const next = new Set(current);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };

    const folderRow = (node, depth) => {
        const isExpanded = expandedPaths.has(node.path);
        const size = bytesFor(node, filter);
        const percentage = percentOf(size, totalBytes);
        const visibleFiles = node.files.filter((file) => fileMatchesFilter(file, filter));
        const visibleChildren = node.children.filter((child) => filter === "all" || bytesFor(child, filter) > 0);
        const hasChildren = visibleChildren.length > 0 || visibleFiles.length > 0;
        if (filter !== "all" && size === 0 && !hasChildren) return null;

        return <Fragment key={node.path}>
            <div className="storage-tree-row" style={{"--row-indent": String(depth * 17) + "px"}}>
                <div className="storage-tree-main">
                    <span className={"storage-tree-chevron" + (hasChildren ? "" : " is-empty")}>
                        <button type="button" aria-label={(isExpanded ? "Collapse " : "Expand ") + node.name}
                                onClick={() => toggleExpanded(node.path)}
                                disabled={!hasChildren}>
                            <span className={"material-symbols-outlined" + (isExpanded ? " is-expanded" : "")}>chevron_right</span>
                        </button>
                    </span>
                    <button type="button" className="storage-folder-label" onClick={() => focusFolder(node)} title={"Open " + node.path + " in chart"}>
                        <span className="material-symbols-outlined storage-tree-icon" aria-hidden="true">folder</span>
                        <span className="storage-tree-name">{node.name}</span>
                    </button>
                    {node.isDeleted && <span className="storage-trash-badge">Trash</span>}
                    <span className="storage-tree-size">{formatFileSize(size)}</span>
                </div>
                <div className="storage-tree-meter-track">
                    <div className="storage-tree-meter" style={{width: percentage + "%", background: folderColor(node.path, depth)}}/>
                </div>
                <div className="storage-tree-meta">
                    <span>{percentage.toFixed(1)}%</span>
                    <span>{fileCountFor(node, filter).toLocaleString()} files</span>
                </div>
            </div>
            {isExpanded && <div className="storage-tree-children" style={{"--row-indent": String(depth * 17) + "px"}}>
                {visibleChildren.map((child) => folderRow(child, depth + 1))}
                {visibleFiles.map((file) => fileRow(file, depth + 1))}
            </div>}
        </Fragment>;
    };

    const fileRow = (file, depth) => {
        const percentage = percentOf(file.size, totalBytes);
        return <div className="storage-tree-file" key={file.key} style={{"--row-indent": String(depth * 17) + "px"}}>
            <span className="material-symbols-outlined storage-file-icon" aria-hidden="true">{file.isUnassigned ? "database" : "draft"}</span>
            <span className="storage-tree-name" title={file.name}>{file.name}</span>
            {file.isTrash && <span className="storage-trash-badge">Trash</span>}
            <span className="storage-tree-size">{formatFileSize(file.size)}</span>
            <span className="storage-file-percent">{percentage.toFixed(1)}%</span>
        </div>;
    };

    const renderTree = () => {
        const rootFiles = root.files.filter((file) => fileMatchesFilter(file, filter));
        return <>
            <div className="storage-tree-root">
                <span className="material-symbols-outlined" aria-hidden="true">home</span>
                <span className="storage-tree-name">/</span>
                <span className="storage-tree-size">{formatFileSize(totalBytes)}</span>
                <span className="storage-tree-meta">{totalFiles.toLocaleString()} files · {totalFolders.toLocaleString()} folders</span>
            </div>
            {root.children.map((child) => folderRow(child, 0))}
            {rootFiles.map((file) => fileRow(file, 0))}
            {root.children.length === 0 && rootFiles.length === 0 && (
                <div className="storage-empty-tree">
                    <span className="material-symbols-outlined">folder_open</span>
                    <p>No files or folders in this view.</p>
                </div>
            )}
        </>;
    };

    const currentChartChildren = chartChildren(focusedNode, filter);
    const canGoUp = focusedNode.path !== HOME_PATH;
    const chartClick = (item) => {
        if (item.kind === "folder") focusFolder(item.node);
    };

    return <div className="storage-explorer fixed inset-0 z-[700] flex items-center justify-center p-2 sm:p-4"
                onMouseDown={(event) => {if (event.target === event.currentTarget) onClose();}}>
        <section role="dialog" aria-modal="true" aria-labelledby="storage-analysis-title"
                 className="storage-window flex h-[min(96vh,1040px)] min-h-[520px] w-full max-w-[1720px] flex-col overflow-hidden rounded-2xl border shadow-2xl">
            <header className="storage-topbar flex shrink-0 items-center justify-between gap-4 px-4 py-3 sm:px-6">
                <div className="flex min-w-0 items-center gap-3">
                    <div className="storage-brand-icon flex h-10 w-10 shrink-0 items-center justify-center rounded-xl">
                        <span className="material-symbols-outlined text-[22px]" aria-hidden="true">database</span>
                    </div>
                    <div className="min-w-0">
                        <h2 id="storage-analysis-title" className="text-base font-semibold">Storage analysis</h2>
                        <p className="storage-muted mt-0.5 truncate text-xs">Folder-by-folder breakdown of your stored files</p>
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <span className="storage-live-badge hidden sm:inline-flex">Live dashboard data</span>
                    <button type="button" className="storage-close-button flex h-9 w-9 items-center justify-center rounded-lg"
                            onClick={onClose} aria-label="Close storage analysis" title="Close storage analysis">
                        <span className="material-symbols-outlined text-[20px]" aria-hidden="true">close</span>
                    </button>
                </div>
            </header>

            <div className="storage-summary-grid grid shrink-0 grid-cols-2 gap-2 border-b p-3 sm:grid-cols-4 sm:px-5 sm:py-3">
                <div className="storage-summary-card">
                    <span className="storage-summary-label">Storage used</span>
                    <strong>{formatFileSize(totalBytes)}</strong>
                    <small>{filter === "all" ? "Across files and trash" : filter === "active" ? "Active files only" : "Deleted files only"}</small>
                </div>
                <div className="storage-summary-card">
                    <span className="storage-summary-label">Files</span>
                    <strong>{totalFiles.toLocaleString()}</strong>
                    <small>{filter === "all" ? "All tracked file records" : filter === "active" ? "Active file records" : "Files in trash"}</small>
                </div>
                <div className="storage-summary-card">
                    <span className="storage-summary-label">Folders</span>
                    <strong>{totalFolders.toLocaleString()}</strong>
                    <small>Including nested folders</small>
                </div>
                <div className="storage-summary-card">
                    <span className="storage-summary-label">Largest folder</span>
                    <strong className="storage-largest-value">{largestFolder ? largestFolder.name : "—"}</strong>
                    <small>{largestFolder ? formatFileSize(bytesFor(largestFolder, filter)) : "No folder data yet"}</small>
                </div>
            </div>

            <div className="storage-filterbar flex shrink-0 flex-wrap items-center justify-between gap-3 px-4 py-2.5 sm:px-5">
                <div className="flex items-center gap-1.5">
                    <span className="storage-muted mr-1 text-xs">Show</span>
                    {[
                        {id: "all", label: "All content"},
                        {id: "active", label: "Active"},
                        {id: "trash", label: "Trash"},
                    ].map((item) => <button key={item.id} type="button"
                        className={"storage-filter-button" + (filter === item.id ? " is-active" : "")}
                        onClick={() => setFilter(item.id)}>{item.label}</button>)}
                </div>
                <div className="storage-muted text-xs">
                    {loading ? "Refreshing storage data…" : "Updates as folders and files change"}
                </div>
            </div>

            <div className="storage-content-grid grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(350px,0.92fr)_minmax(0,1.08fr)]">
                <section className="storage-tree-panel flex min-h-0 flex-col border-r">
                    <div className="storage-panel-heading flex shrink-0 items-center justify-between gap-3 px-4 py-3 sm:px-5">
                        <div>
                            <h3 className="text-sm font-semibold">Folders and files</h3>
                            <p className="storage-muted mt-0.5 text-[11px]">Expand folders to inspect nested usage</p>
                        </div>
                        <span className="storage-muted text-[11px]">{root.children.length.toLocaleString()} top-level folders</span>
                    </div>
                    <div className="storage-tree-head grid shrink-0 grid-cols-[minmax(0,1fr)_90px] gap-2 px-4 py-2 sm:px-5">
                        <span>Folder / File</span>
                        <span className="text-right">Size</span>
                    </div>
                    <div className="storage-tree-scroll min-h-0 flex-1 overflow-y-auto px-2 py-2 sm:px-3">
                        {renderTree()}
                    </div>
                </section>

                <section className="storage-chart-panel flex min-h-0 flex-col">
                    <div className="storage-panel-heading flex shrink-0 flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5">
                        <div className="min-w-0">
                            <h3 className="text-sm font-semibold">Storage distribution</h3>
                            <div className="storage-breadcrumb mt-1 flex min-w-0 flex-wrap items-center gap-1 text-[11px]">
                                {trail.map((part, index) => <Fragment key={part.path}>
                                    {index > 0 && <span className="material-symbols-outlined storage-muted text-[13px]" aria-hidden="true">chevron_right</span>}
                                    <button type="button" className={index === trail.length - 1 ? "is-current" : ""}
                                            onClick={() => focusFolder(part)}>{part.name === "Home" ? "/" : part.name}</button>
                                </Fragment>)}
                            </div>
                        </div>
                        <div className="storage-chart-switch flex shrink-0 items-center gap-1">
                            <button type="button" className={chartType === "rings" ? "is-active" : ""}
                                    onClick={() => setChartType("rings")}>
                                <span className="material-symbols-outlined text-[15px]" aria-hidden="true">donut_large</span>
                                Rings chart
                            </button>
                            <button type="button" className={chartType === "treemap" ? "is-active" : ""}
                                    onClick={() => setChartType("treemap")}>
                                <span className="material-symbols-outlined text-[15px]" aria-hidden="true">view_quilt</span>
                                Treemap
                            </button>
                        </div>
                    </div>

                    <div className="storage-chart-stage relative flex min-h-0 flex-1 items-center justify-center overflow-hidden p-2 sm:p-4">
                        {currentChartChildren.length === 0 ? (
                            <div className="storage-chart-empty flex flex-col items-center gap-2 text-center">
                                <span className="material-symbols-outlined text-[40px]" aria-hidden="true">donut_large</span>
                                <p className="text-sm font-medium">No storage to visualize here</p>
                                <p className="storage-muted text-xs">Choose another folder or content filter.</p>
                            </div>
                        ) : chartType === "rings" ? (
                            <svg viewBox="0 0 640 640" role="img" aria-label="Nested storage rings chart"
                                 className="storage-chart-svg h-full w-full">
                                {ringSegments.map((segment, index) => {
                                    const path = donutArcPath(
                                        320, 320, segment.innerRadius, segment.outerRadius,
                                        segment.startAngle, segment.endAngle,
                                    );
                                    const label = segment.item.name + ": " + formatFileSize(segment.item.size)
                                        + " (" + percentOf(segment.item.size, chartRootBytes).toFixed(1) + "%)";
                                    return <path key={segment.item.path + "-" + segment.depth + "-" + index}
                                        d={path}
                                        fill={segment.item.color || folderColor(segment.item.path, segment.depth, segment.item.kind)}
                                        stroke="var(--storage-border)"
                                        strokeWidth="1.5"
                                        className="storage-chart-segment"
                                        role={segment.item.kind === "folder" ? "button" : undefined}
                                        tabIndex={segment.item.kind === "folder" ? 0 : undefined}
                                        aria-label={label}
                                        onClick={() => chartClick(segment.item)}
                                        onKeyDown={(event) => {
                                            if (segment.item.kind === "folder" && (event.key === "Enter" || event.key === " ")) {
                                                event.preventDefault();
                                                chartClick(segment.item);
                                            }
                                        }}
                                    >
                                        <title>{label}</title>
                                    </path>;
                                })}
                                <circle cx="320" cy="320" r="66" fill="var(--storage-chart-hole)" stroke="var(--storage-border)" strokeWidth="1"/>
                                <text x="320" y="300" textAnchor="middle" className="storage-chart-center-label">
                                    {focusedNode.name === "Home" ? "Total storage" : focusedNode.name}
                                </text>
                                <text x="320" y="330" textAnchor="middle" className="storage-chart-center-value">
                                    {formatFileSize(chartRootBytes)}
                                </text>
                                <text x="320" y="352" textAnchor="middle" className="storage-chart-center-sub">
                                    {fileCountFor(focusedNode, filter).toLocaleString()} files
                                </text>
                            </svg>
                        ) : (
                            <svg viewBox="0 0 1000 600" role="img" aria-label="Nested storage treemap chart"
                                 className="storage-chart-svg h-full w-full">
                                {treemapRects.map((rect, index) => {
                                    // Direct files are grouped into a leaf tile for their
                                    // containing folder. Display the folder name, not
                                    // the internal "Files in ..." grouping label.
                                    const tileName = rect.item.kind === "files"
                                        ? (rect.item.node.name === "Home" ? "/" : rect.item.node.name)
                                        : rect.item.name;
                                    const label = tileName + ": " + formatFileSize(rect.item.size)
                                        + " (" + percentOf(rect.item.size, chartRootBytes).toFixed(1) + "%)";
                                    const canShowLabel = rect.width > 78 && rect.height > 36;
                                    return <g key={rect.item.path + "-" + rect.depth + "-" + index}
                                               role="button"
                                               tabIndex={0}
                                               className="storage-chart-segment"
                                               aria-label={"Double-click to open " + (rect.item.node.name === "Home" ? "root" : rect.item.node.name)}
                                               onDoubleClick={() => focusFolder(rect.item.node)}
                                               onKeyDown={(event) => {
                                                   if (event.key === "Enter" || event.key === " ") {
                                                       event.preventDefault();
                                                       focusFolder(rect.item.node);
                                                   }
                                               }}>
                                        <title>{"Double-click to open " + (rect.item.node.name === "Home" ? "root" : rect.item.node.name) + " · " + label}</title>
                                        <rect x={rect.x} y={rect.y} width={rect.width} height={rect.height}
                                              rx="3" fill={rect.item.color || folderColor(rect.item.path, rect.depth, rect.item.kind)}
                                              fillOpacity={rect.item.kind === "folder" && rect.depth < 3 ? "0.72" : "0.94"}
                                              stroke="var(--storage-chart-hole)" strokeWidth="2"/>
                                        {canShowLabel && <text x={rect.x + 10} y={rect.y + 21} className="storage-treemap-label">
                                            {tileName.length > Math.max(8, Math.floor(rect.width / 9))
                                                ? tileName.slice(0, Math.max(7, Math.floor(rect.width / 9) - 1)) + "…"
                                                : tileName}
                                        </text>}
                                        {canShowLabel && rect.height > 54 && <text x={rect.x + 10} y={rect.y + 40} className="storage-treemap-size">
                                            {formatFileSize(rect.item.size)}
                                        </text>}
                                    </g>;
                                })}
                            </svg>
                        )}
                    </div>

                    <div className="storage-chart-footer flex shrink-0 flex-wrap items-center justify-between gap-2 px-4 py-2.5 sm:px-5">
                        <div className="storage-muted text-[11px]">
                            Click a folder segment to explore its contents
                        </div>
                        <button type="button" className="storage-reset-button" disabled={!canGoUp}
                                onClick={() => focusFolder(focusedNode.parent || root)}>
                            <span className="material-symbols-outlined text-[15px]" aria-hidden="true">arrow_upward</span>
                            Parent folder
                        </button>
                    </div>
                </section>
            </div>

            <footer className="storage-bottom-bar flex shrink-0 flex-wrap items-center justify-between gap-2 px-4 py-2.5 sm:px-6">
                <div className="storage-muted text-[11px]">
                    {tree.unassignedBytes > 0
                        ? formatFileSize(tree.unassignedBytes) + " of the account total is not linked to a file record."
                        : tree.discrepancyBytes > 0
                            ? "File-record sizes exceed the account metadata total by " + formatFileSize(tree.discrepancyBytes) + "."
                            : "Folder sizes are calculated from unique file records."}
                </div>
                <span className="storage-muted text-[11px]">Sizes use the server-reported file byte counts</span>
            </footer>
        </section>
    </div>;
}
