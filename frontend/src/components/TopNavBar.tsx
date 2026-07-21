"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChartNoAxesCombined, Menu, Search, X } from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";

const NAV_LINKS = [
    { name: "Analysis", path: "/" },
    { name: "Screener", path: "/screener" },
    { name: "Anomalies", path: "/anomalies" },
    { name: "Market Rotation", path: "/rrg" },
    { name: "Factor Lab", path: "/research" },
];

export default function TopNavBar() {
    const pathname = usePathname();
    const router = useRouter();
    const [searchInput, setSearchInput] = useState("");
    const [menuOpen, setMenuOpen] = useState(false);

    const handleSearch = (event: React.FormEvent) => {
        event.preventDefault();
        const ticker = searchInput.trim().toUpperCase();
        if (!ticker) return;
        router.push(`/?ticker=${encodeURIComponent(ticker)}`);
        setSearchInput("");
        setMenuOpen(false);
    };

    const navLinks = (mobile = false) => (
        <div className={mobile ? "grid gap-1" : "flex h-full items-center gap-1"}>
            {NAV_LINKS.map((link) => {
                const active = pathname === link.path;
                return (
                    <Link
                        key={link.path}
                        href={link.path}
                        onClick={() => setMenuOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={mobile
                            ? `rounded-xl px-4 py-3 text-sm font-semibold ${active ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`
                            : `relative flex h-full items-center px-3 text-sm font-semibold transition-colors ${active ? "text-emerald-700 dark:text-emerald-300" : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"}`
                        }
                    >
                        {link.name}
                        {!mobile && active && <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-emerald-500" />}
                    </Link>
                );
            })}
        </div>
    );

    return (
        <nav className="relative z-50 shrink-0 border-b bg-white/95 px-4 py-3 backdrop-blur-xl dark:bg-[#0b1116]/95 md:h-16 md:px-6 md:py-0" aria-label="Primary navigation">
            <div className="mx-auto flex h-full max-w-[1600px] flex-wrap items-center gap-3 md:flex-nowrap md:justify-between">
                <div className="flex min-w-0 flex-1 items-center gap-5 md:h-full lg:flex-none">
                    <Link href="/" onClick={() => setMenuOpen(false)} className="flex shrink-0 items-center gap-2" aria-label="Quantify home">
                        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500 text-slate-950 shadow-sm">
                            <ChartNoAxesCombined size={19} strokeWidth={2.5} />
                        </span>
                        <span className="text-lg font-black tracking-[-0.04em] text-slate-900 dark:text-white">Quantify</span>
                    </Link>
                    <div className="hidden h-full lg:block">{navLinks()}</div>
                </div>

                <div className="ml-auto flex shrink-0 items-center gap-2">
                    <ThemeToggle />
                    <button
                        type="button"
                        onClick={() => setMenuOpen((open) => !open)}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-lg border bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-200 lg:hidden"
                        aria-label={menuOpen ? "Close navigation" : "Open navigation"}
                        aria-expanded={menuOpen}
                    >
                        {menuOpen ? <X size={19} /> : <Menu size={19} />}
                    </button>
                </div>

                <form onSubmit={handleSearch} className="relative order-3 w-full md:order-none md:w-64 xl:w-72" role="search">
                    <input
                        type="search"
                        className="control-field py-2 pl-3 pr-10"
                        placeholder="Search ticker, e.g. AAPL.US"
                        aria-label="Search stock ticker"
                        value={searchInput}
                        onChange={(event) => setSearchInput(event.target.value)}
                    />
                    <button type="submit" className="absolute right-1.5 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-emerald-600 dark:hover:bg-slate-800 dark:hover:text-emerald-300" aria-label="Search ticker">
                        <Search size={15} />
                    </button>
                </form>
            </div>

            {menuOpen && (
                <div className="absolute inset-x-0 top-full border-b bg-white p-3 shadow-xl dark:bg-[#10171d] lg:hidden">
                    {navLinks(true)}
                </div>
            )}
        </nav>
    );
}
