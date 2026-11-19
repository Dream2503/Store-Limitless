import {createContext, useContext, useEffect, useMemo, useState} from "react";

const ThemeContext = createContext(null);

/**
 * Owns the application appearance preference.
 * Dark mode is the first-run default; the chosen mode is shared across routes
 * and saved locally so it survives refreshes and new visits on this browser.
 */
export function ThemeProvider({children}) {
    const [theme, setTheme] = useState(() => {
        try {
            return localStorage.getItem("storelimitless-theme") === "light" ? "light" : "dark";
        } catch {
            return "dark";
        }
    });

    useEffect(() => {
        document.documentElement.dataset.theme = theme;
        document.documentElement.style.colorScheme = theme;
        try {
            localStorage.setItem("storelimitless-theme", theme);
        } catch {
            // Keep the selected appearance for this session if storage is unavailable.
        }
    }, [theme]);

    const toggleTheme = () => setTheme((current) => current === "dark" ? "light" : "dark");
    const value = useMemo(() => ({theme, toggleTheme}), [theme]);

    return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/** Read the appearance preference from the application provider. */
export function useTheme() {
    const value = useContext(ThemeContext);
    if (!value) throw new Error("useTheme must be used inside ThemeProvider.");
    return value;
}

/** Compact theme switch designed to sit inside the page's existing header. */
export function ThemeToggle({className = ""}) {
    const {theme, toggleTheme} = useTheme();
    const nextTheme = theme === "dark" ? "light" : "dark";

    return <button
        type="button"
        onClick={toggleTheme}
        className={`theme-toggle inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e] ${className}`}
        aria-label={`Switch to ${nextTheme} mode`}
        aria-pressed={theme === "light"}
        title={`Switch to ${nextTheme} mode`}
    >
        <span className="material-symbols-outlined text-[19px]" aria-hidden="true">
            {theme === "dark" ? "light_mode" : "dark_mode"}
        </span>
    </button>;
}
