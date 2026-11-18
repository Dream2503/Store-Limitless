import {API_URL} from "../../api.js";
export {API_URL};

export const DASHBOARD_SELECTION_STORAGE_KEY = "storelimitless_dashboard_selection_restore";
export const DASHBOARD_SELECTIONS_BY_VIEW_KEY = "storelimitless_dashboard_selections_by_view";

export const NAV_ITEMS = [{id: "files", icon: "folder_open", label: "All Files", enabled: true}, {
    id: "trash", icon: "delete", label: "Trash", enabled: true
},];

export const btnSecondary = "inline-flex h-8 items-center gap-1.5 rounded-md border border-[#363636] bg-[#242424] px-3 text-xs text-[#fafafa] transition-colors hover:border-[#4d4d4d] hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-50";
export const btnPrimary = "inline-flex h-8 items-center gap-1.5 rounded-md border border-[#3ecf8e]/40 bg-[#006239] px-3 text-xs text-[#fafafa] transition-colors hover:border-[#3ecf8e]/70 hover:bg-[#007a46] disabled:cursor-not-allowed disabled:opacity-50";
export const iconBtn = "flex h-7 w-7 items-center justify-center rounded-md text-[#898989] transition-colors hover:bg-[#2a2a2a] hover:text-[#fafafa] disabled:opacity-50";
export const iconBtnDanger = "flex h-7 w-7 items-center justify-center rounded-md text-[#898989] transition-colors hover:bg-[#e5484d]/10 hover:text-[#ff6369] disabled:opacity-50";
export const inputClass = "w-full h-[38px] rounded-md border border-[#363636] bg-white/[0.026] px-3 text-sm text-[#fafafa] placeholder:text-[#707070] outline-none transition-colors focus:border-[#707070] focus:ring-2 focus:ring-[#4d4d4d]/50 disabled:opacity-60";

export const FILE_ICONS = {
    pdf: {icon: "picture_as_pdf", color: "text-[#ff6369]"},
    jpg: {icon: "image", color: "text-[#3ecf8e]"},
    jpeg: {icon: "image", color: "text-[#3ecf8e]"},
    png: {icon: "image", color: "text-[#3ecf8e]"},
    gif: {icon: "image", color: "text-[#3ecf8e]"},
    webp: {icon: "image", color: "text-[#3ecf8e]"},
    txt: {icon: "subject", color: "text-[#898989]"},
    zip: {icon: "folder_zip", color: "text-[#f5a524]"},
    rar: {icon: "folder_zip", color: "text-[#f5a524]"},
    mp4: {icon: "movie", color: "text-[#70b8ff]"},
    mkv: {icon: "movie", color: "text-[#70b8ff]"},
    mp3: {icon: "music_note", color: "text-[#70b8ff]"},
    wav: {icon: "music_note", color: "text-[#70b8ff]"},
    py: {icon: "code", color: "text-[#b4b4b4]"},
    js: {icon: "javascript", color: "text-[#b4b4b4]"},
    jsx: {icon: "code", color: "text-[#b4b4b4]"},
    default: {icon: "draft", color: "text-[#898989]"},
};
