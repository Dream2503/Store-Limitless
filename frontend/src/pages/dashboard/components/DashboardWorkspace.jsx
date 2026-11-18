import {iconBtn} from "../constants.js";
import {formatDate, formatFileSize, getFileIcon} from "../utils.js";

/**
 * Main dashboard workspace: search, breadcrumbs, folder cards, and file rows.
 * State and interaction handlers remain owned by DashboardPage.
 */
export default function DashboardWorkspace(props) {
    const {
        sidebarOpen,
        setSidebarOpen,
        searchQuery,
        setSearchQuery,
        isTrash,
        selectionSurfaceRef,
        handleSelectionMouseDown,
        handleTouchBackgroundStart,
        handleTouchItemStart,
        handleBackgroundContextMenu,
        suppressClickRef,
        clearSelection,
        breadcrumbSegments,
        openFolder,
        dropTargetKey,
        pageError,
        fetchFiles,
        searchError,
        isSearching,
        loadingFiles,
        isSearchBusy,
        searchLoading,
        filteredFolders,
        filteredFiles,
        isEmpty,
        marquee,
        selectedItems,
        handleSelectItem,
        beginMoveDrag,
        handleItemContextMenu,
        openSelectedItem,
        handleItemMenuButton,
        draggingKeys,
        deletingId,
    } = props;

    return (<div className="flex h-screen flex-1 flex-col md:ml-60">
        {/* Header */}
        <header
            className="fixed top-0 right-0 z-40 flex h-14 w-full items-center justify-between border-b border-[#2e2e2e] bg-[#171717] px-6 md:w-[calc(100%-240px)]">
            <div className="flex w-full max-w-md items-center gap-4">
                <button
                    type="button"
                    className="text-[#898989] transition-colors hover:text-[#fafafa] md:hidden"
                    onClick={() => setSidebarOpen(true)}
                    aria-label="Open menu"
                >
                    <span className="material-symbols-outlined" aria-hidden="true">
                        menu
                    </span>
                </button>

                <div className="relative hidden w-full sm:block">
                    <span
                        className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[#707070]"
                        style={{fontSize: 18}}
                        aria-hidden="true"
                    >
                        search
                    </span>
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(event) => setSearchQuery(event.target.value)}
                        placeholder={isTrash ? "Search trash…" : "Search files…"}
                        aria-label="Search files"
                        className="h-8 w-full rounded-md border border-[#363636] bg-white/[0.026] pl-9 pr-3 text-sm text-[#fafafa] outline-none transition-colors placeholder:text-[#707070] focus:border-[#707070] focus:ring-2 focus:ring-[#4d4d4d]/50"
                    />
                </div>
            </div>
        </header>

        {/* Main View Area */}
        <main className="dashboard-main-scroll flex-1 overflow-y-auto bg-[#1c1c1c] px-4 pb-8 pt-20 md:px-8">
            <div
                ref={selectionSurfaceRef}
                onMouseDown={handleSelectionMouseDown}
                onTouchStart={handleTouchBackgroundStart}
                onContextMenu={handleBackgroundContextMenu}
                onClick={(event) => {
                    // Clicking empty space clears the selection, like the desktop.
                    // Do not clear after a marquee drag or when interacting with an item/control.
                    if (suppressClickRef.current) return;
                    if (event.target.closest("[data-selection-id], [data-selection-ignore], button, input, textarea, select, a")) return;
                    clearSelection();
                }}
                className="dashboard-selection-surface relative min-h-full select-none"
            >
                {!isTrash ? (<div
                    className="mb-6 w-full rounded-xl border border-[#343434] bg-gradient-to-r from-[#202020] via-[#1d1d1d] to-[#191919] px-4 py-3.5 shadow-[0_8px_28px_rgba(0,0,0,0.12)] sm:px-5">
                    <div className="flex min-w-0 items-center gap-3">
                        <div
                            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[#3ecf8e]/20 bg-[#3ecf8e]/10 text-[#3ecf8e]">
                            <span className="material-symbols-outlined text-[21px]" aria-hidden="true">folder_open</span>
                        </div>
                        <div className="min-w-0 flex-1">
                            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[#707070]">Current location</p>
                            <nav aria-label="Folder path" className="mt-1 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
                                {breadcrumbSegments.map(({label, path, isHome}, i) => (
                                    <span key={`${path}-${i}`} className="inline-flex min-w-0 items-center gap-1.5">
                                    {i > 0 && <span className="material-symbols-outlined text-[16px] text-[#555555]"
                                                    aria-hidden="true">chevron_right</span>}
                                        <button
                                            type="button"
                                            onClick={(event) => {
                                                if (suppressClickRef.current) {
                                                    event.preventDefault();
                                                    return;
                                                }
                                                openFolder(isHome ? "" : path);
                                            }}
                                            data-drop-path={isHome ? "/home" : path}
                                            data-drop-key={`breadcrumb-drop:${isHome ? "/home" : path}`}
                                            aria-current={i === breadcrumbSegments.length - 1 ? "location" : undefined}
                                            className={`max-w-full truncate rounded px-1 py-0.5 transition-colors hover:bg-white/[0.06] hover:text-[#fafafa] ${dropTargetKey === `breadcrumb-drop:${isHome ? "/home" : path}` ? "bg-[#3ecf8e]/15 text-[#3ecf8e] ring-1 ring-[#3ecf8e]/40" : i === breadcrumbSegments.length - 1 ? "font-medium text-[#fafafa]" : "text-[#929292]"}`}
                                            title={label}
                                        >
                                        {label}
                                    </button>
                                </span>))}
                            </nav>
                        </div>
                    </div>
                </div>) : (<div className="mb-6 flex w-full items-center gap-3 rounded-xl border border-[#343434] bg-[#202020] px-4 py-3.5 sm:px-5">
                    <div
                        className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#f5a524]/20 bg-[#f5a524]/10 text-[#f5a524]">
                        <span className="material-symbols-outlined text-[21px]" aria-hidden="true">delete</span>
                    </div>
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[#707070]">File management</p>
                        <h2 className="mt-1 text-sm font-medium text-[#fafafa]">Trash</h2>
                    </div>
                </div>)}

                {pageError && (<div
                    className="mb-6 flex items-center justify-between rounded-md border border-[#e5484d]/30 bg-[#e5484d]/10 px-4 py-3 text-sm text-[#ff6369]">
                    <span>{pageError}</span>
                    <button type="button" onClick={fetchFiles} className="text-xs underline underline-offset-4">
                        Retry
                    </button>
                </div>)}

                {searchError && isSearching && (
                    <div className="mb-6 rounded-md border border-[#e5484d]/30 bg-[#e5484d]/10 px-4 py-3 text-sm text-[#ff6369]">
                        {searchError}
                    </div>)}

                {(loadingFiles || isSearchBusy) ? (<div className="flex flex-col items-center justify-center py-24 text-sm text-[#898989]">
                    <span className="material-symbols-outlined mb-3 animate-spin text-[32px]" aria-hidden="true">
                        progress_activity
                    </span>
                    {isSearchBusy ? "Searching cached files…" : "Loading your files and folders…"}
                </div>) : (<>
                    {isSearching && !searchError && (<p className="mb-4 text-xs text-[#707070]">
                        Search results across all folders
                    </p>)}
                    {filteredFolders.length > 0 && (<div className="mb-8">
                        <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-[#b4b4b4]">
                                <span className="material-symbols-outlined text-[18px] text-[#898989]" aria-hidden="true">
                                    folder
                                </span>
                            Folders
                        </h3>

                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                            {filteredFolders.map(({path, id, is_deleted: folderIsDeleted, total_size_bytes: totalSizeBytes}) => {
                                const displayName = path.split("/").pop();
                                const itemKey = `folder:${id}`;
                                const isSelected = selectedItems.has(itemKey);
                                const selectionItem = {
                                    key: itemKey,
                                    type: "folder",
                                    id,
                                    name: displayName,
                                    path,
                                    is_deleted: Boolean(folderIsDeleted),
                                    object: {path, id, is_deleted: Boolean(folderIsDeleted), total_size_bytes: totalSizeBytes ?? 0}
                                };
                                const folderDropKey = `folder-drop:${id}`;
                                const isFolderDropTarget = dropTargetKey === folderDropKey;
                                return (<div
                                    key={path}
                                    data-selection-id={itemKey}
                                    onMouseDown={(event) => beginMoveDrag(event, selectionItem)}
                                    onTouchStart={(event) => handleTouchItemStart(event, selectionItem)}
                                    onDragStart={(event) => event.preventDefault()}
                                    data-drop-path={path}
                                    data-drop-key={folderDropKey}
                                    onClick={(event) => handleSelectItem(selectionItem, event)}
                                    onContextMenu={(event) => handleItemContextMenu(event, selectionItem)}
                                    onDoubleClick={(event) => {
                                        if (isTrash || suppressClickRef.current) return;
                                        event.preventDefault();
                                        openFolder(path);
                                    }}
                                    className={`group relative select-none cursor-default rounded-lg border p-4 text-left transition-all duration-200 ease-out motion-reduce:transition-none ${isFolderDropTarget ? "border-[#3ecf8e] bg-[#3ecf8e]/[0.14] ring-2 ring-[#3ecf8e]/30" : isSelected ? "border-[#3ecf8e]/60 bg-[#3ecf8e]/[0.08] ring-1 ring-[#3ecf8e]/30" : "border-[#2e2e2e] bg-[#1f1f1f] hover:border-[#4d4d4d] hover:bg-[#232323]"}`}
                                    style={draggingKeys.has(itemKey) ? {
                                        opacity: 0, transform: "scale(0.985)", pointerEvents: "none",
                                    } : undefined}
                                >
                                    <button
                                        type="button"
                                        draggable={false}
                                        className={`block w-full select-none text-left ${isTrash ? "cursor-default" : "cursor-pointer"}`}
                                    >
                                                <span
                                                    className="material-symbols-outlined text-[36px] text-[#898989] transition-colors group-hover:text-[#3ecf8e]"
                                                    style={{fontVariationSettings: "'FILL' 1"}}
                                                    aria-hidden="true"
                                                >
                                                    folder
                                                </span>
                                        <p className="mt-3 truncate text-sm text-[#fafafa]">
                                            {displayName}
                                        </p>
                                    </button>
                                    <div
                                        data-selection-ignore
                                        onClick={(event) => event.stopPropagation()}
                                        onDoubleClick={(event) => event.stopPropagation()}
                                        className="absolute right-2 top-2 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                                        <button
                                            type="button"
                                            onClick={(event) => handleItemMenuButton(event, selectionItem)}
                                            onContextMenu={(event) => {
                                                event.preventDefault();
                                                event.stopPropagation();
                                            }}
                                            title={isTrash ? "Folder options" : "Folder options"}
                                            aria-label={`Options for folder ${displayName}`}
                                            className={iconBtn}
                                        >
                                            <span className="material-symbols-outlined text-[18px]">more_vert</span>
                                        </button>
                                    </div>
                                </div>);
                            })}
                        </div>
                    </div>)}

                    {isEmpty ? (<div className="flex flex-col items-center justify-center py-24 text-[#898989]">
                            <span className="material-symbols-outlined mb-4 text-[48px] text-[#4d4d4d]" aria-hidden="true">
                                {isTrash ? "auto_delete" : "cloud_off"}
                            </span>
                        <p className="text-sm font-medium text-[#fafafa]">
                            {isTrash ? "Trash is empty" : searchQuery ? "No matching files" : "No files yet"}
                        </p>
                    </div>) : (filteredFiles.length > 0 && (<div>
                        <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-[#b4b4b4]">
                                    <span className="material-symbols-outlined text-[18px] text-[#898989]" aria-hidden="true">
                                        description
                                    </span>
                            Files
                        </h3>

                        <div className="overflow-hidden rounded-lg border border-[#2e2e2e] bg-[#1c1c1c]">
                            <div
                                className="grid grid-cols-12 gap-2 border-b border-[#2e2e2e] bg-[#1f1f1f] px-4 py-2.5 sm:grid-cols-[minmax(0,2.7fr)_minmax(64px,0.35fr)_minmax(72px,0.45fr)_28px]">
                                <div className="col-span-8 text-xs font-medium text-[#898989] sm:col-auto">
                                    Name
                                </div>
                                <div className="col-span-1 hidden text-xs font-medium text-[#898989] sm:col-auto sm:block">
                                    Size
                                </div>
                                <div className="col-span-3 text-xs font-medium text-[#898989] sm:col-auto">
                                    Data Center
                                </div>
                                <div className="col-span-1 sm:col-auto" aria-hidden="true"/>
                            </div>

                            {filteredFiles.map((file, index) => {
                                const {icon, color} = getFileIcon(file.name);
                                const isLast = index === filteredFiles.length - 1;
                                const isDeleting = deletingId === file.id;
                                const itemKey = `file:${file.id}`;
                                const isSelected = selectedItems.has(itemKey);
                                const selectionItem = {
                                    key: itemKey,
                                    type: "file",
                                    id: file.id,
                                    name: file.name,
                                    directory: file.directory || "",
                                    is_deleted: Boolean(file.is_deleted),
                                    object: file
                                };

                                return (<div
                                    key={file.id}
                                    data-selection-id={itemKey}
                                    draggable={false}
                                    onMouseDown={(event) => beginMoveDrag(event, selectionItem)}
                                    onTouchStart={(event) => handleTouchItemStart(event, selectionItem)}
                                    onDragStart={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                    }}
                                    onSelectStart={(event) => event.preventDefault()}
                                    onClick={(event) => handleSelectItem(selectionItem, event)}
                                    onContextMenu={(event) => handleItemContextMenu(event, selectionItem)}
                                    onDoubleClick={(event) => {
                                        if (isTrash || suppressClickRef.current) return;
                                        event.preventDefault();
                                        openSelectedItem(selectionItem);
                                    }}
                                    className={`group grid select-none cursor-default grid-cols-12 items-center gap-2 px-4 py-2.5 transition-all duration-200 ease-out motion-reduce:transition-none sm:grid-cols-[minmax(0,2.7fr)_minmax(64px,0.35fr)_minmax(72px,0.45fr)_28px] ${isSelected ? "bg-[#3ecf8e]/[0.08] ring-1 ring-inset ring-[#3ecf8e]/35" : "hover:bg-[#232323]"} ${isLast ? "" : "border-b border-[#2e2e2e]"}`}
                                    style={draggingKeys.has(itemKey) ? {
                                        opacity: 0, transform: "scale(0.995)", pointerEvents: "none",
                                    } : undefined}
                                >
                                    {/* File Name & Mobile Metadata */}
                                    <div className="col-span-8 flex min-w-0 items-center gap-3 sm:col-auto">
                                                    <span className={`material-symbols-outlined text-[18px] ${color}`} aria-hidden="true">
                                                        {icon}
                                                    </span>
                                        <div className="min-w-0">
                                            {isTrash ? (<p className="truncate text-sm text-[#fafafa]">{file.name}</p>) : (<button
                                                type="button"
                                                draggable={false}
                                                className="block max-w-full select-none truncate text-left text-sm text-[#fafafa] transition-colors hover:text-white"
                                            >
                                                {file.name}
                                            </button>)}
                                            <p className="mt-0.5 text-xs text-[#707070] sm:hidden">
                                                {formatFileSize(file.size)} • {formatDate(file.modified_at)}
                                            </p>
                                        </div>
                                    </div>

                                    {/* Desktop File Size */}
                                    <div className="col-span-1 hidden font-mono text-xs text-[#898989] sm:col-auto sm:block">
                                        {formatFileSize(file.size)}
                                    </div>

                                    {/* Data Center */}
                                    <div className="col-span-3 sm:col-auto min-w-0">
                                                    <span
                                                        className="inline-block max-w-full truncate rounded border border-[#363636] bg-[#242424] px-2 py-0.5 text-xs text-[#b4b4b4]">
                                                        {file.data_center || "—"}
                                                    </span>
                                    </div>

                                    {/* Per-file options live in a compact overflow menu, not a separate Actions column. */}
                                    <div
                                        data-selection-ignore
                                        onClick={(event) => event.stopPropagation()}
                                        onDoubleClick={(event) => event.stopPropagation()}
                                        className="col-span-1 flex justify-end sm:col-auto">
                                        <button
                                            type="button"
                                            onClick={(event) => handleItemMenuButton(event, selectionItem)}
                                            onContextMenu={(event) => {
                                                event.preventDefault();
                                                event.stopPropagation();
                                            }}
                                            title={isTrash ? "File options" : "File options"}
                                            aria-label={`Options for file ${file.name}`}
                                            className={iconBtn}
                                        >
                                            <span className="material-symbols-outlined text-[18px]">more_vert</span>
                                        </button>
                                    </div>
                                </div>);
                            })}
                        </div>
                    </div>))}
                </>)}
                {marquee && (<div
                    aria-hidden="true"
                    className="pointer-events-none absolute z-30 rounded-sm border border-[#3ecf8e] bg-[#3ecf8e]/15"
                    style={{left: marquee.left, top: marquee.top, width: marquee.width, height: marquee.height}}
                />)}
            </div>
        </main>
    </div>);
}
