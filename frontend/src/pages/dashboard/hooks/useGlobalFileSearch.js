import {useMemo} from "react";

/** Search the already-loaded in-memory file cache without making network calls. */
export default function useGlobalFileSearch(searchQuery, activeTab, files) {
    const query = searchQuery.trim().toLocaleLowerCase();
    const searchResults = useMemo(() => {
        if (!query) return [];
        return (Array.isArray(files) ? files : [])
            .filter((file) => {
                if (!file || !file.name || file.name === ".__folder__") return false;
                if (activeTab === "trash" ? !file.is_deleted : file.is_deleted) return false;
                return file.name.toLocaleLowerCase().includes(query);
            })
            .map((file) => ({
                ...file, directory: file.directory || "", is_deleted: Boolean(file.is_deleted),
            }));
    }, [query, activeTab, files]);

    return {searchResults, searchLoading: false, searchError: ""};
}
