"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChartCandlestick, Menu, Search, X } from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";
import CommandPalette, { NAV_PAGES } from "./shell/CommandPalette";

const isEditable = (target: EventTarget | null) =>
    target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

export default function TopNavBar() {
    const pathname = usePathname();
    const [menuOpen, setMenuOpen] = useState(false);
    const [paletteOpen, setPaletteOpen] = useState(false);

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
                event.preventDefault();
                setPaletteOpen((open) => !open);
            } else if (event.key === "/" && !isEditable(event.target)) {
                event.preventDefault();
                setPaletteOpen(true);
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, []);

    const navLinks = (mobile = false) => (
        <div className={mobile ? "grid gap-0.5" : "flex h-full items-center gap-0.5"}>
            {NAV_PAGES.map((link) => {
                const active = pathname === link.path || link.aliases?.includes(pathname);
                return (
                    <Link
                        key={link.path}
                        href={link.path}
                        onClick={() => setMenuOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={mobile
                            ? `rounded-md px-3 py-2.5 text-sm font-medium ${active ? "bg-accent-soft text-accent-strong" : "text-fg-muted hover:bg-surface-muted hover:text-fg"}`
                            : `relative flex h-full items-center px-2.5 text-[13px] font-medium transition-colors ${active ? "text-fg" : "text-fg-muted hover:text-fg"}`
                        }
                    >
                        {link.name}
                        {!mobile && active && <span className="absolute inset-x-2.5 bottom-0 h-0.5 bg-accent" />}
                    </Link>
                );
            })}
        </div>
    );

    return (
        <nav className="relative z-50 shrink-0 border-b bg-canvas px-3 md:px-5" aria-label="Primary navigation">
            <div className="mx-auto flex h-11 max-w-[1600px] items-center gap-3">
                <Link href="/" onClick={() => setMenuOpen(false)} className="flex shrink-0 items-center gap-2" aria-label="Quantify home">
                    <span className="flex h-6 w-6 items-center justify-center rounded border border-accent text-accent">
                        <ChartCandlestick size={14} strokeWidth={2.25} />
                    </span>
                    <span className="text-[15px] font-semibold tracking-[-0.02em] text-fg">Quantify</span>
                </Link>
                <div className="ml-3 hidden h-full lg:block">{navLinks()}</div>

                <button
                    type="button"
                    onClick={() => setPaletteOpen(true)}
                    className="ml-auto flex h-8 min-w-0 flex-1 items-center gap-2 rounded-md border bg-surface px-2.5 text-left text-[13px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg sm:max-w-64 md:flex-none md:w-64"
                    aria-label="Search stock ticker"
                    aria-keyshortcuts="Meta+K Control+K /"
                >
                    <Search size={14} className="shrink-0" aria-hidden="true" />
                    <span className="flex-1 truncate">Search ticker or page</span>
                    <span className="kbd hidden sm:inline-flex">⌘K</span>
                </button>

                <ThemeToggle />
                <button
                    type="button"
                    onClick={() => setMenuOpen((open) => !open)}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-surface text-fg-muted lg:hidden"
                    aria-label={menuOpen ? "Close navigation" : "Open navigation"}
                    aria-expanded={menuOpen}
                    aria-controls="mobile-navigation"
                >
                    {menuOpen ? <X size={17} /> : <Menu size={17} />}
                </button>
            </div>
            {menuOpen && <div id="mobile-navigation" className="absolute inset-x-0 top-full border-b bg-surface p-2 shadow-xl lg:hidden">{navLinks(true)}</div>}
            {paletteOpen && <Suspense fallback={null}>
                <CommandPalette onClose={() => setPaletteOpen(false)} />
            </Suspense>}
        </nav>
    );
}
