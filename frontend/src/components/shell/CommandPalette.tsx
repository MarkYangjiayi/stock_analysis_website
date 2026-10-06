"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, CornerDownLeft, LayoutGrid, LineChart, Search, Star } from "lucide-react";
import { readLegacyWatchlist } from "@/hooks/usePersonalWorkspace";

export const NAV_PAGES = [
    { name: "Analysis", path: "/" },
    { name: "Screener", path: "/screener" },
    { name: "Anomalies", path: "/anomalies" },
    { name: "Market", path: "/market", aliases: ["/rrg", "/market/yield-curve", "/market/index-valuation"] },
    { name: "Factor Lab", path: "/research" },
];

const MARKET_PAGES = [
    { name: "Sector rotation", path: "/rrg" },
    { name: "Treasury yield curve", path: "/market/yield-curve" },
    { name: "S&P 500 historical P/E", path: "/market/index-valuation" },
];

const SECTIONS = [
    { name: "Overview", value: "overview" },
    { name: "Valuation", value: "valuation" },
    { name: "Financials", value: "financials" },
    { name: "Price & Factors", value: "technical" },
    { name: "Events & Brief", value: "events" },
];

const TICKER_PATTERN = /^[A-Z0-9][A-Z0-9.\-^]{0,14}$/;

type Command = {
    id: string;
    group: "Ticker" | "Watchlist" | "Section" | "Go to";
    label: string;
    hint?: string;
    href: string;
};

const canonicalTicker = (value: string) => {
    const symbol = value.trim().toUpperCase();
    return symbol.includes(".") ? symbol : `${symbol}.US`;
};

const matches = (query: string, ...fields: string[]) =>
    !query || fields.some((field) => field.toLowerCase().includes(query.toLowerCase()));

/** Mounted only while open, so every opening starts from a fresh query. */
export default function CommandPalette({ onClose }: { onClose: () => void }) {
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const listId = useId();
    const inputRef = useRef<HTMLInputElement>(null);
    const [query, setQuery] = useState("");
    const [active, setActive] = useState(0);
    const [watchlist] = useState<string[]>(() => readLegacyWatchlist());

    useEffect(() => {
        const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
        return () => {
            window.cancelAnimationFrame(frame);
            previousFocus?.focus({ preventScroll: true });
        };
    }, []);

    const currentTicker = pathname === "/" ? searchParams.get("ticker") : null;

    const commands = useMemo<Command[]>(() => {
        const trimmed = query.trim();
        const upper = trimmed.toUpperCase();
        const result: Command[] = [];
        if (trimmed && TICKER_PATTERN.test(upper)) {
            const ticker = canonicalTicker(upper);
            result.push({ id: `ticker-${ticker}`, group: "Ticker", label: `Analyze ${ticker}`, hint: "Open in Analysis", href: `/?ticker=${encodeURIComponent(ticker)}` });
        }
        for (const ticker of watchlist) {
            if (matches(trimmed, ticker, ticker.replace(".US", "")) && !result.some((command) => command.id === `ticker-${ticker}`)) {
                result.push({ id: `watch-${ticker}`, group: "Watchlist", label: ticker, href: `/?ticker=${encodeURIComponent(ticker)}` });
            }
        }
        if (currentTicker) {
            for (const section of SECTIONS) {
                if (!matches(trimmed, section.name, section.value)) continue;
                const params = new URLSearchParams({ ticker: currentTicker });
                if (section.value !== "overview") params.set("section", section.value);
                result.push({ id: `section-${section.value}`, group: "Section", label: section.name, hint: currentTicker, href: `/?${params.toString()}` });
            }
        }
        for (const page of [...NAV_PAGES, ...MARKET_PAGES]) {
            if (matches(trimmed, page.name, page.path)) result.push({ id: `page-${page.path}`, group: "Go to", label: page.name, hint: page.path, href: page.path });
        }
        // "fin" or "valu" names a section more often than a ticker; ≥3 chars keeps "F" or "MU" ticker-first.
        if (trimmed.length >= 3) {
            const prefix = trimmed.toLowerCase();
            const named = (command: Command) => (command.group === "Section" || command.group === "Go to") && command.label.toLowerCase().startsWith(prefix);
            return [...result.filter(named), ...result.filter((command) => !named(command))].slice(0, 30);
        }
        return result.slice(0, 30);
    }, [currentTicker, query, watchlist]);

    const activeIndex = Math.min(active, Math.max(commands.length - 1, 0));

    const run = (command: Command | undefined) => {
        if (!command) return;
        router.push(command.href);
        onClose();
    };

    const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
        if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive(commands.length ? (activeIndex + 1) % commands.length : 0);
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive(commands.length ? (activeIndex - 1 + commands.length) % commands.length : 0);
        } else if (event.key === "Enter") {
            event.preventDefault();
            run(commands[activeIndex]);
        } else if (event.key === "Escape") {
            event.preventDefault();
            onClose();
        }
    };

    const iconFor = (group: Command["group"]) => group === "Ticker" ? LineChart : group === "Watchlist" ? Star : group === "Section" ? LayoutGrid : ArrowRight;

    return (
        <div className="fixed inset-0 z-[90] flex items-start justify-center bg-black/50 px-3 pt-[12vh]" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
            <div role="dialog" aria-modal="true" aria-label="Command palette" className="w-full max-w-xl overflow-hidden rounded-lg border bg-surface-raised shadow-2xl shadow-black/40">
                <div className="flex items-center gap-2.5 border-b px-3.5">
                    <Search size={16} className="shrink-0 text-fg-muted" aria-hidden="true" />
                    <input
                        ref={inputRef}
                        type="text"
                        role="combobox"
                        aria-expanded="true"
                        aria-controls={listId}
                        aria-activedescendant={commands[activeIndex] ? `${listId}-${commands[activeIndex].id}` : undefined}
                        aria-label="Search tickers, pages and sections"
                        placeholder="Ticker, page or section…"
                        className="h-12 min-w-0 flex-1 bg-transparent font-mono text-sm text-fg outline-none placeholder:font-sans placeholder:text-fg-muted"
                        value={query}
                        onChange={(event) => { setQuery(event.target.value); setActive(0); }}
                        onKeyDown={onKeyDown}
                        autoComplete="off"
                        spellCheck={false}
                        // Focus during commit so keystrokes typed right after ⌘K aren't lost.
                        autoFocus
                    />
                    <span className="kbd">Esc</span>
                </div>
                <ul id={listId} role="listbox" aria-label="Commands" className="custom-scrollbar max-h-[50vh] overflow-y-auto py-1.5">
                    {commands.length === 0 && <li className="px-4 py-6 text-center text-sm text-fg-muted">No matches. Type a ticker such as NVDA.</li>}
                    {commands.map((command, index) => {
                        const Icon = iconFor(command.group);
                        const showGroup = index === 0 || commands[index - 1].group !== command.group;
                        return (
                            <li key={command.id} role="presentation">
                                {showGroup && <p className="eyebrow px-4 pb-1 pt-2.5" aria-hidden="true">{command.group}</p>}
                                <div
                                    id={`${listId}-${command.id}`}
                                    role="option"
                                    aria-selected={index === activeIndex}
                                    onMouseMove={() => setActive(index)}
                                    onClick={() => run(command)}
                                    className={`mx-1.5 flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-2 text-sm ${index === activeIndex ? "bg-accent-soft text-fg" : "text-fg-muted"}`}
                                >
                                    <Icon size={15} className={index === activeIndex ? "text-accent" : ""} aria-hidden="true" />
                                    <span className={command.group === "Ticker" || command.group === "Watchlist" ? "font-mono" : ""}>{command.label}</span>
                                    {command.hint && <span className="ml-auto truncate font-mono text-xs text-fg-muted">{command.hint}</span>}
                                    {index === activeIndex && <CornerDownLeft size={13} className="shrink-0 text-fg-muted" aria-hidden="true" />}
                                </div>
                            </li>
                        );
                    })}
                </ul>
                <div className="flex items-center gap-3 border-t px-3.5 py-2 text-xs text-fg-muted">
                    <span className="inline-flex items-center gap-1"><span className="kbd">↑</span><span className="kbd">↓</span> navigate</span>
                    <span className="inline-flex items-center gap-1"><span className="kbd">↵</span> open</span>
                </div>
            </div>
        </div>
    );
}
