import {NAV_ITEMS} from "../constants.js";
import {formatFileSize} from "../utils.js";

export default function Sidebar({activeTab, onTabSelect, onLogout, onUploadClick, storageUsed}) {
    return (<nav className="flex h-screen w-60 flex-col border-r border-[#2e2e2e] bg-[#171717]">
        {/* Brand */}
        <div className="flex h-14 items-center gap-2.5 border-b border-[#2e2e2e] px-4">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#3ecf8e]">
                    <span
                        className="material-symbols-outlined text-[18px] text-[#0f0f0f]"
                        style={{fontVariationSettings: "'FILL' 1"}}
                        aria-hidden="true"
                    >
                        cloud
                    </span>
            </div>
            <div className="leading-tight">
                <h1 className="text-sm font-medium tracking-tight text-[#fafafa]">StoreLimitless</h1>
                <p className="text-[11px] text-[#707070]">File Management</p>
            </div>
        </div>

        <div className="flex flex-1 flex-col gap-4 p-3">
            <button
                type="button"
                onClick={onUploadClick}
                className="flex h-9 items-center justify-center gap-2 rounded-lg border border-[#2e8058]/70 bg-[#17643d] px-3 text-sm font-medium text-white shadow-sm shadow-black/10 transition-colors hover:border-[#3ecf8e] hover:bg-[#1d794a] focus:outline-none focus:ring-2 focus:ring-[#3ecf8e]/35"
            >
                <span className="material-symbols-outlined text-[18px]" aria-hidden="true">cloud_upload</span>
                Upload files
            </button>
            <div className="flex flex-1 flex-col gap-0.5">
                {NAV_ITEMS.map(({id, icon, label, enabled}) => {
                    const isActive = activeTab === id;
                    return (<button
                        key={id}
                        type="button"
                        disabled={!enabled}
                        onClick={enabled ? () => onTabSelect(id) : undefined}
                        title={enabled ? undefined : "Coming soon"}
                        className={`flex h-8 items-center gap-2.5 rounded-md px-2.5 text-left text-sm transition-colors ${isActive ? "bg-[#2a2a2a] text-[#fafafa]" : enabled ? "text-[#898989] hover:bg-[#232323] hover:text-[#fafafa]" : "cursor-not-allowed text-[#4d4d4d]"}`}
                    >
                                <span className="material-symbols-outlined text-[18px]" aria-hidden="true">
                                    {icon}
                                </span>
                        {label}
                    </button>);
                })}
            </div>

            <div className="rounded-lg border border-[#343434] bg-[#1d1d1d] px-3 py-3">
                <div className="flex items-center gap-2 text-[#a0a0a0]">
                    <span className="material-symbols-outlined text-[17px] text-[#3ecf8e]" aria-hidden="true">
                        database
                    </span>
                    <span className="text-xs">Storage used</span>
                </div>
                <p className="mt-2 text-lg font-medium tracking-tight text-[#fafafa]">
                    {storageUsed === null ? "Calculating…" : formatFileSize(storageUsed)}
                </p>
                <p className="mt-0.5 text-[10px] text-[#707070]">Across your files and trash</p>
            </div>

            <div className="flex flex-col gap-0.5 border-t border-[#2e2e2e] pt-3">
                <button
                    type="button"
                    onClick={onLogout}
                    className="flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm text-[#898989] transition-colors hover:bg-[#e5484d]/10 hover:text-[#ff6369]"
                >
                        <span className="material-symbols-outlined text-[18px]" aria-hidden="true">
                            logout
                        </span>
                    Sign Out
                </button>
            </div>
        </div>
    </nav>);
}
