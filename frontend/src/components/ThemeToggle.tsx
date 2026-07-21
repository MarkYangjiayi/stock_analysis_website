"use client";

import * as React from "react";
import { Moon, Sun, Monitor } from "lucide-react";
import { useTheme } from "next-themes";

export function ThemeToggle() {
    const [mounted, setMounted] = React.useState(false);
    const { theme, setTheme } = useTheme();

    // Prevent hydration mismatch
    React.useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted) {
        return <div className="w-9 h-9" />; // Placeholder to avoid layout shift
    }

    return (
        <div className="flex items-center gap-0.5 rounded-full border bg-slate-100 p-1 transition-colors dark:bg-slate-900" role="group" aria-label="Color theme">
            <button
                onClick={() => setTheme("light")}
                className={`p-1.5 rounded-full transition-all ${theme === "light"
                        ? "bg-white text-emerald-500 shadow-sm"
                        : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
                    }`}
                title="Light Mode"
                aria-label="Use light theme"
                aria-pressed={theme === "light"}
            >
                <Sun className="w-4 h-4" />
            </button>
            <button
                onClick={() => setTheme("system")}
                className={`p-1.5 rounded-full transition-all ${theme === "system"
                        ? "bg-white dark:bg-[#252b3d] text-emerald-500 dark:text-emerald-400 shadow-sm"
                        : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
                    }`}
                title="System Theme"
                aria-label="Use system theme"
                aria-pressed={theme === "system"}
            >
                <Monitor className="w-4 h-4" />
            </button>
            <button
                onClick={() => setTheme("dark")}
                className={`p-1.5 rounded-full transition-all ${theme === "dark"
                        ? "bg-[#252b3d] text-emerald-400 shadow-sm"
                        : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
                    }`}
                title="Dark Mode"
                aria-label="Use dark theme"
                aria-pressed={theme === "dark"}
            >
                <Moon className="w-4 h-4" />
            </button>
        </div>
    );
}
