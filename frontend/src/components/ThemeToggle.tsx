"use client";

import * as React from "react";
import { Moon, Sun, Monitor } from "lucide-react";
import { useTheme } from "next-themes";

const OPTIONS = [
    { value: "light", label: "Use light theme", title: "Light Mode", Icon: Sun },
    { value: "system", label: "Use system theme", title: "System Theme", Icon: Monitor },
    { value: "dark", label: "Use dark theme", title: "Dark Mode", Icon: Moon },
] as const;

export function ThemeToggle() {
    const [mounted, setMounted] = React.useState(false);
    const { theme, setTheme } = useTheme();

    // Prevent hydration mismatch
    React.useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted) {
        return <div className="h-8 w-[86px] shrink-0" />; // Placeholder to avoid layout shift
    }

    return (
        <div className="flex h-8 shrink-0 items-center gap-0.5 rounded-md border bg-surface p-0.5" role="group" aria-label="Color theme">
            {OPTIONS.map(({ value, label, title, Icon }) => (
                <button
                    key={value}
                    onClick={() => setTheme(value)}
                    className={`flex h-6 w-6 items-center justify-center rounded transition-colors ${theme === value ? "bg-accent-soft text-accent" : "text-fg-muted hover:text-fg"}`}
                    title={title}
                    aria-label={label}
                    aria-pressed={theme === value}
                >
                    <Icon className="h-3.5 w-3.5" />
                </button>
            ))}
        </div>
    );
}
