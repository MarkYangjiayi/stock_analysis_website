"use client";

import {
    AlertTriangle,
    ChevronLeft,
    ChevronRight,
    Columns3,
    Filter,
    Info,
    RefreshCw,
    Search,
    SlidersHorizontal,
    X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { API_BASE_URL } from "@/lib/api";
import {
    decodeFilters,
    encodeFilters,
    filterLabel,
    formatScreenerValue,
    MAX_SCREENER_FILTERS,
    sanitizeFilters,
    ScreenerField,
    ScreenerFilter,
    ScreenerMetadata,
    ScreenerQueryResponse,
} from "@/lib/screener";
import {
    FieldControl,
    parseColumns,
    parsePage,
    updateScreenerFilters,
} from "./screener-controls";

const PAGE_SIZE = 50;
const EMPTY_COLUMNS_SENTINEL = "none";
const CATEGORIES = ["Descriptive", "Fundamental", "Technical"] as const;
const CORE_COLUMNS = ["ticker", "name"];
function ScreenerContent() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [metadata, setMetadata] = useState<ScreenerMetadata | null>(null);
    const [result, setResult] = useState<ScreenerQueryResponse | null>(null);
    const [activeCategory, setActiveCategory] = useState<(typeof CATEGORIES)[number]>("Descriptive");
    const [search, setSearch] = useState("");
    const [filters, setFilters] = useState<ScreenerFilter[]>(() => decodeFilters(searchParams.get("filters")));
    const [columns, setColumns] = useState<string[]>(() => parseColumns(searchParams.get("columns")));
    const [sort, setSort] = useState(() => {
        const [rawField, direction] = (searchParams.get("sort") ?? "").split(":");
        return { field: rawField || "market_cap", direction: direction === "asc" ? "asc" as const : "desc" as const };
    });
    const [page, setPage] = useState(() => parsePage(searchParams.get("page")));
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
    const querySequence = useRef(0);
    const metadataLoaded = useRef(false);
    const [explicitEmptyColumns] = useState(
        () => searchParams.get("columns") === EMPTY_COLUMNS_SENTINEL,
    );

    const loadMetadata = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError(null);
        try {
            const response = await fetch(`${API_BASE_URL}/api/stocks/screener/metadata`, { signal });
            if (!response.ok) throw new Error("Unable to load screener fields.");
            const data = await response.json() as ScreenerMetadata;
            const isInitialMetadataLoad = !metadataLoaded.current;
            const validSortFields = new Set([
                ...CORE_COLUMNS,
                ...data.fields
                    .filter((field) => field.result_column && field.available)
                    .map((field) => field.id),
            ]);
            const availableDefaultColumns = data.default_columns.filter((column) =>
                !CORE_COLUMNS.includes(column) && validSortFields.has(column)
            );
            setSort((current) => validSortFields.has(current.field)
                ? current
                : validSortFields.has("market_cap")
                    ? { field: "market_cap", direction: "desc" }
                    : { field: "ticker", direction: "asc" });
            setColumns((current) => {
                const validColumns = current.filter((column) =>
                    !CORE_COLUMNS.includes(column) && validSortFields.has(column)
                ).slice(0, 30);
                return validColumns.length
                    ? validColumns
                    : isInitialMetadataLoad && !explicitEmptyColumns
                        ? availableDefaultColumns
                        : [];
            });
            setFilters((current) => sanitizeFilters(current, data.fields));
            metadataLoaded.current = true;
            setMetadata(data);
        } catch (reason) {
            if (reason instanceof DOMException && reason.name === "AbortError") return;
            setError(reason instanceof Error ? reason.message : "Unable to load screener fields.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [explicitEmptyColumns]);

    useEffect(() => {
        const controller = new AbortController();
        void loadMetadata(controller.signal);
        return () => controller.abort();
    }, [loadMetadata]);

    useEffect(() => {
        const params = new URLSearchParams();
        if (filters.length) params.set("filters", encodeFilters(filters));
        if (sort.field !== "market_cap" || sort.direction !== "desc") params.set("sort", `${sort.field}:${sort.direction}`);
        const availableResultColumns = new Set(
            metadata?.fields
                .filter((field) => field.result_column && field.available)
                .map((field) => field.id) ?? []
        );
        const defaultColumns = metadata?.default_columns.filter((column) =>
            !CORE_COLUMNS.includes(column) && availableResultColumns.has(column)
        ) ?? [];
        if (metadata && columns.length === 0) {
            params.set("columns", EMPTY_COLUMNS_SENTINEL);
        } else if (columns.length && columns.join(",") !== defaultColumns.join(",")) {
            params.set("columns", columns.join(","));
        }
        if (page > 0) params.set("page", String(page + 1));
        const query = params.toString();
        router.replace(query ? `?${query}` : "/screener", { scroll: false });
    }, [columns, filters, metadata, page, router, sort]);

    const runQuery = useCallback(async (
        signal?: AbortSignal,
        requestId = ++querySequence.current,
    ) => {
        if (!metadata) return;
        setLoading(true);
        setError(null);
        try {
            const response = await fetch(`${API_BASE_URL}/api/stocks/screener/query`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal,
                body: JSON.stringify({
                    as_of_date: metadata.as_of_date,
                    filters,
                    sort,
                    columns,
                    limit: PAGE_SIZE,
                    offset: page * PAGE_SIZE,
                }),
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.detail ?? "The screener query failed.");
            }
            const data = await response.json() as ScreenerQueryResponse;
            if (requestId !== querySequence.current) return;
            const lastPage = Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1);
            if (page > lastPage) {
                setPage(lastPage);
                return;
            }
            setResult(data);
        } catch (reason) {
            if (reason instanceof DOMException && reason.name === "AbortError") return;
            if (requestId === querySequence.current) {
                setError(reason instanceof Error ? reason.message : "The screener query failed.");
            }
        } finally {
            if (!signal?.aborted && requestId === querySequence.current) setLoading(false);
        }
    }, [columns, filters, metadata, page, sort]);

    useEffect(() => {
        const controller = new AbortController();
        const requestId = ++querySequence.current;
        const timeout = window.setTimeout(
            () => void runQuery(controller.signal, requestId),
            250,
        );
        return () => {
            window.clearTimeout(timeout);
            controller.abort();
        };
    }, [runQuery]);

    const fieldMap = useMemo(
        () => new Map(metadata?.fields.map((field) => [field.id, field]) ?? []),
        [metadata],
    );
    const visibleFields = useMemo(
        () => metadata?.fields.filter((field) =>
            field.category === activeCategory &&
            field.label.toLowerCase().includes(search.toLowerCase())
        ) ?? [],
        [activeCategory, metadata, search],
    );
    const selectedColumns = [...CORE_COLUMNS, ...columns.filter((column) => !CORE_COLUMNS.includes(column))];
    const totalPages = Math.max(1, Math.ceil((result?.total ?? 0) / PAGE_SIZE));
    const filterLimitReached = filters.length >= MAX_SCREENER_FILTERS;
    const updateFilter = (fieldId: string, next?: ScreenerFilter) => {
        setFilters((current) => updateScreenerFilters(current, fieldId, next));
        setPage(0);
    };
    const applyPreset = (field: ScreenerField, presetIndex: number) => {
        if (presetIndex < 0) return;
        const preset = field.presets[presetIndex];
        updateFilter(field.id, { field: field.id, operator: preset.operator, value: preset.value });
    };

    return (
        <main className="app-page">
            <div className="page-container max-w-[1580px]">
                <header className="surface-panel overflow-hidden">
                    <div className="flex flex-col gap-4 p-5 md:flex-row md:items-center md:justify-between md:p-6">
                        <div>
                            <div className="eyebrow mb-1 flex items-center gap-2">
                                <SlidersHorizontal size={14} />
                                Quantify Market Intelligence
                            </div>
                            <h1 className="page-title">Stock Screener</h1>
                            <p className="mt-1 text-sm text-fg-muted">
                                {metadata?.universe === "RUSSELL3000_NASDAQ100"
                                    ? "Russell 3000 + Nasdaq-100"
                                    : "Russell 3000"} · {metadata?.supported_finviz_fields ?? "—"} Finviz-aligned fields
                            </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <div className="surface-subtle rounded-lg border px-4 py-2">
                                <div className="eyebrow">Matches</div>
                                <div className="font-mono text-xl font-semibold tabular-nums text-fg">{loading ? "···" : (result?.total ?? 0).toLocaleString()}</div>
                            </div>
                            <div className="surface-subtle rounded-lg border px-4 py-2">
                                <div className="eyebrow">Published snapshot</div>
                                <div className="font-mono text-sm font-semibold tabular-nums text-fg">{result?.as_of_date ?? metadata?.as_of_date ?? "No data"}</div>
                            </div>
                            <button
                                onClick={() => void loadMetadata()}
                                aria-label="Refresh results"
                                className="grid size-11 place-items-center rounded-md border bg-surface text-fg-muted transition-colors hover:border-accent hover:text-accent"
                            >
                                <RefreshCw size={17} className={loading ? "animate-spin" : ""} />
                            </button>
                        </div>
                    </div>
                    {(result?.freshness?.status === "stale" || metadata?.freshness?.status === "stale") && (
                        <div className="flex items-center gap-2 border-t border-caution/30 bg-caution/6 px-5 py-2.5 text-sm text-caution">
                            <AlertTriangle size={16} />
                            Data is {(result?.freshness ?? metadata?.freshness)?.lag_sessions} market sessions behind the latest completed session.
                        </div>
                    )}
                </header>

                <section className="surface-panel">
                    <div className="flex items-center justify-between border-b p-3 md:hidden">
                        <button onClick={() => setMobileFiltersOpen(true)} className="primary-button px-4">
                            <Filter size={16} /> Filters {filters.length ? `(${filters.length})` : ""}
                        </button>
                    </div>
                    <div className={`${mobileFiltersOpen ? "fixed inset-0 z-50 overflow-auto bg-canvas p-4" : "hidden"} md:block`}>
                        <div className="mb-4 flex items-center justify-between md:hidden">
                            <h2 className="text-lg font-semibold text-fg">Filters</h2>
                            <button aria-label="Close filters" onClick={() => setMobileFiltersOpen(false)} className="rounded-md p-1 text-fg-muted hover:bg-surface-muted hover:text-fg"><X /></button>
                        </div>
                        <div className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-center lg:justify-between">
                            <div className="segmented-control gap-0.5 self-start">
                                {CATEGORIES.map((category) => (
                                    <button
                                        key={category}
                                        onClick={() => setActiveCategory(category)}
                                        className={`min-h-8 rounded px-3.5 text-sm font-medium transition-colors ${activeCategory === category ? "bg-accent-soft text-accent-strong" : "text-fg-muted hover:text-fg"}`}
                                    >
                                        {category}
                                    </button>
                                ))}
                            </div>
                            <label className="relative block w-full lg:w-72">
                                <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" />
                                <input
                                    value={search}
                                    onChange={(event) => setSearch(event.target.value)}
                                    placeholder="Search fields"
                                    className="control-field h-10 pl-9"
                                />
                            </label>
                        </div>

                        {filters.length > 0 && (
                            <div className="flex flex-wrap items-center gap-2 border-b bg-surface-muted px-4 py-3">
                                {filters.map((filter) => {
                                    const field = fieldMap.get(filter.field);
                                    if (!field) return null;
                                    return (
                                        <button key={filter.field} onClick={() => updateFilter(filter.field)} className="flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent-soft px-3 py-1.5 text-xs font-medium text-accent-strong transition-colors hover:border-accent">
                                            {filterLabel(filter, field)} <X size={12} />
                                        </button>
                                    );
                                })}
                                <button onClick={() => { setFilters([]); setPage(0); }} className="px-2 text-xs font-semibold text-fg-muted transition-colors hover:text-danger">Clear all</button>
                                {filterLimitReached && (
                                    <span className="text-xs font-medium text-caution">
                                        Maximum {MAX_SCREENER_FILTERS} filters reached.
                                    </span>
                                )}
                            </div>
                        )}

                        <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
                            {visibleFields.map((field) => {
                                const active = filters.find((filter) => filter.field === field.id);
                                return (
                                    <div key={field.id} className={`min-w-0 rounded-lg border p-3 transition-colors ${active ? "border-accent/60 bg-accent-soft" : "bg-surface hover:border-line-strong"} ${!field.available ? "opacity-50" : ""}`}>
                                        <div className="mb-2 flex items-start justify-between gap-2">
                                            <div>
                                                <div className="flex items-center gap-1">
                                                    <label className="block text-xs font-semibold text-fg">{field.label}</label>
                                                    {field.description && (
                                                        <span className="group/help relative">
                                                            <button
                                                                type="button"
                                                                aria-label={`About ${field.label}`}
                                                                className="grid size-4 place-items-center rounded-full text-fg-muted hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                                                            >
                                                                <Info size={12} />
                                                            </button>
                                                            <span
                                                                role="tooltip"
                                                                className="pointer-events-none absolute left-0 top-5 z-[60] hidden w-72 rounded-md border bg-surface-raised px-3 py-2 text-xs font-normal leading-relaxed text-fg shadow-xl group-hover/help:block group-focus-within/help:block"
                                                            >
                                                                {field.description}
                                                            </span>
                                                        </span>
                                                    )}
                                                </div>
                                                <span className={`font-mono text-xs tabular-nums ${field.coverage === 0 ? "text-fg-muted opacity-70" : field.coverage < 0.5 ? "text-caution" : "text-fg-muted"}`}>{Math.round(field.coverage * 100)}% coverage</span>
                                            </div>
                                            {field.presets.length > 0 && (
                                                <select
                                                    aria-label={`${field.label} preset`}
                                                    value={String(active
                                                        ? field.presets.findIndex((preset) =>
                                                            preset.operator === active.operator &&
                                                            JSON.stringify(preset.value) === JSON.stringify(active.value)
                                                        )
                                                        : -1)}
                                                    onChange={(event) => applyPreset(field, Number(event.target.value))}
                                                    disabled={!field.available || (filterLimitReached && !active)}
                                                    className="max-w-28 rounded-md border-0 bg-transparent text-xs text-fg-muted outline-none hover:text-fg focus-visible:text-fg"
                                                >
                                                    <option value="-1">Preset</option>
                                                    {field.presets.map((preset, index) => <option value={index} key={preset.label}>{preset.label}</option>)}
                                                </select>
                                            )}
                                        </div>
                                        <fieldset className="min-w-0" disabled={!field.available || (filterLimitReached && !active)}>
                                            <FieldControl
                                                field={field}
                                                filter={active}
                                                onChange={(next) => updateFilter(field.id, next)}
                                            />
                                        </fieldset>
                                    </div>
                                );
                            })}
                        </div>
                        <div className="sticky bottom-0 z-50 border-t bg-surface p-4 md:hidden">
                            <button onClick={() => setMobileFiltersOpen(false)} className="primary-button min-h-11 w-full">Show {result?.total ?? 0} matches</button>
                        </div>
                    </div>
                </section>

                <section className="surface-panel overflow-hidden">
                    <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                            <h2 className="font-semibold text-fg">Screening results</h2>
                            <p className="text-xs text-fg-muted">Click a column to sort. Results are calculated from the published snapshot.</p>
                        </div>
                        <details className="relative">
                            <summary className="secondary-button cursor-pointer list-none">
                                <Columns3 size={15} /> Columns
                            </summary>
                            <div className="absolute right-0 z-30 mt-2 max-h-80 w-72 overflow-auto rounded-lg border bg-surface-raised p-2 shadow-2xl">
                                {metadata?.fields.filter((field) => field.available && field.result_column).map((field) => (
                                    <label key={field.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm text-fg hover:bg-surface-muted">
                                        <input
                                            type="checkbox"
                                            checked={columns.includes(field.id)}
                                            onChange={() => setColumns((current) => current.includes(field.id) ? current.filter((value) => value !== field.id) : [...current, field.id].slice(0, 30))}
                                            className="accent-[var(--brand)]"
                                        />
                                        <span className="flex-1">{field.label}</span>
                                        <span className="font-mono text-xs uppercase text-fg-muted">{field.category.slice(0, 4)}</span>
                                    </label>
                                ))}
                            </div>
                        </details>
                    </div>

                    {error ? (
                        <div className="error-panel m-4 flex items-center justify-between p-4">
                            <span>{error}</span>
                            <button onClick={() => void loadMetadata()} className="font-semibold underline">Retry</button>
                        </div>
                    ) : (
                        <div className="max-h-[680px] overflow-auto">
                            <table className="min-w-full whitespace-nowrap text-left text-sm">
                                <thead className="sticky top-0 z-10 bg-surface-muted font-mono text-xs font-medium uppercase tracking-[0.08em] text-fg-muted">
                                    <tr>
                                        {selectedColumns.map((column) => {
                                            const field = fieldMap.get(column);
                                            return (
                                                <th key={column} className={`border-b px-3 py-2 font-medium ${column === "ticker" ? "sticky left-0 z-20 bg-surface-muted" : column === "name" ? "" : "text-right"}`}>
                                                    <button
                                                        title={field?.description ?? undefined}
                                                        onClick={() => {
                                                            if (column === "name" || column === "ticker" || field) {
                                                                setSort((current) => ({ field: column, direction: current.field === column && current.direction === "desc" ? "asc" : "desc" }));
                                                                setPage(0);
                                                            }
                                                        }}
                                                        className={`flex items-center gap-1 transition-colors hover:text-accent ${sort.field === column ? "text-fg" : ""} ${column === "name" || column === "ticker" ? "" : "ml-auto"}`}
                                                    >
                                                        {column === "ticker" ? "Ticker" : column === "name" ? "Company" : field?.label ?? column}
                                                        {sort.field === column && <span className="text-accent">{sort.direction === "desc" ? "↓" : "↑"}</span>}
                                                    </button>
                                                </th>
                                            );
                                        })}
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-line">
                                    {loading && !result ? (
                                        Array.from({ length: 8 }).map((_, index) => (
                                            <tr key={index}>{selectedColumns.map((column) => <td key={column} className="px-3 py-2.5"><div className="h-4 w-24 animate-pulse rounded bg-surface-muted" /></td>)}</tr>
                                        ))
                                    ) : result?.items.length ? result.items.map((row, rowIndex) => (
                                        <tr key={String(row.ticker ?? rowIndex)} className="group transition-colors hover:bg-surface-muted">
                                            {selectedColumns.map((column) => (
                                                <td key={column} className={`px-3 py-2 ${column === "ticker" ? "sticky left-0 z-[5] bg-surface font-mono font-semibold text-accent-strong group-hover:bg-surface-muted" : column === "name" ? "max-w-64 truncate font-medium text-fg" : "text-right font-mono tabular-nums text-fg"}`}>
                                                    {column === "ticker" ? (
                                                        <Link
                                                            href={`/?ticker=${encodeURIComponent(String(row[column] ?? ""))}`}
                                                            className="hover:underline"
                                                        >
                                                            {String(row[column] ?? "").replace(".US", "")}
                                                        </Link>
                                                    ) : formatScreenerValue(row[column], fieldMap.get(column))}
                                                </td>
                                            ))}
                                        </tr>
                                    )) : (
                                        <tr><td colSpan={selectedColumns.length} className="px-6 py-20 text-center"><Filter className="mx-auto mb-3 text-fg-muted opacity-50" size={32} /><p className="font-semibold text-fg">No stocks match these filters</p><p className="mt-1 text-sm text-fg-muted">Remove one or more conditions and try again.</p></td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    )}

                    <footer className="flex items-center justify-between border-t px-4 py-3 text-sm">
                        <span className="font-mono text-xs tabular-nums text-fg-muted">
                            {result?.total ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, result.total)} of ${result.total.toLocaleString()}` : "0 results"}
                        </span>
                        <div className="flex items-center gap-2">
                            <button aria-label="Previous page" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))} className="grid size-9 place-items-center rounded-md border bg-surface text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-30"><ChevronLeft size={16} /></button>
                            <span className="min-w-24 text-center font-mono text-xs tabular-nums text-fg-muted">Page {page + 1} / {totalPages}</span>
                            <button aria-label="Next page" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))} className="grid size-9 place-items-center rounded-md border bg-surface text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-30"><ChevronRight size={16} /></button>
                        </div>
                    </footer>
                </section>
            </div>
        </main>
    );
}

export default function ScreenerPage() {
    return (
        <Suspense fallback={<div className="h-full bg-canvas" />}>
            <ScreenerContent />
        </Suspense>
    );
}
