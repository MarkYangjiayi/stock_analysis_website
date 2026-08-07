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
    filterOperatorLabel,
    formatScreenerValue,
    MAX_FILTER_VALUES,
    MAX_SCREENER_FILTERS,
    sanitizeFilters,
    ScreenerField,
    ScreenerFilter,
    ScreenerMetadata,
    ScreenerQueryResponse,
} from "@/lib/screener";

const PAGE_SIZE = 50;
const MAX_OFFSET = 1_000_000;
const MAX_PAGE = Math.floor(MAX_OFFSET / PAGE_SIZE);
const CATEGORIES = ["Descriptive", "Fundamental", "Technical"] as const;
const CORE_COLUMNS = ["ticker", "name"];
const EMPTY_COLUMNS_SENTINEL = "none";

export function parseColumns(value: string | null): string[] {
    return value && value !== EMPTY_COLUMNS_SENTINEL
        ? [...new Set(value.split(",").filter(Boolean))].slice(0, 30)
        : [];
}

export function parsePage(value: string | null): number {
    if (value === null) return 0;
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed > 0 ? Math.min(parsed - 1, MAX_PAGE) : 0;
}

export function updateScreenerFilters(
    current: ScreenerFilter[],
    fieldId: string,
    next?: ScreenerFilter,
): ScreenerFilter[] {
    const without = current.filter((filter) => filter.field !== fieldId);
    if (next && without.length >= MAX_SCREENER_FILTERS) return current;
    return next ? [...without, next] : without;
}

export function FieldControl({
    field,
    filter,
    onChange,
}: {
    field: ScreenerField;
    filter?: ScreenerFilter;
    onChange: (filter?: ScreenerFilter) => void;
}) {
    const displayValue = (value: string | number | undefined) => {
        if (value === undefined) return "";
        if (field.unit === "percent") return String(Number(value) * 100);
        return String(value);
    };
    const initialValues = Array.isArray(filter?.value)
        ? filter.value
        : filter
            ? [filter.value]
            : [];
    const [draftOperator, setDraftOperator] = useState<ScreenerFilter["operator"]>(filter?.operator ?? "gte");
    const [draftValues, setDraftValues] = useState<string[]>(() => initialValues.map(displayValue));
    const [enumOpen, setEnumOpen] = useState(false);
    const enumControlRef = useRef<HTMLDivElement>(null);
    const enumButtonRef = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        if (!enumOpen) return;
        const closeOnOutsidePointer = (event: PointerEvent) => {
            if (
                event.target instanceof Node
                && !enumControlRef.current?.contains(event.target)
            ) {
                setEnumOpen(false);
            }
        };
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            setEnumOpen(false);
            enumButtonRef.current?.focus();
        };
        document.addEventListener("pointerdown", closeOnOutsidePointer);
        document.addEventListener("keydown", closeOnEscape);
        return () => {
            document.removeEventListener("pointerdown", closeOnOutsidePointer);
            document.removeEventListener("keydown", closeOnEscape);
        };
    }, [enumOpen]);

    useEffect(() => {
        const nextValues = Array.isArray(filter?.value)
            ? filter.value
            : filter
                ? [filter.value]
                : [];
        const timeout = window.setTimeout(() => {
            setDraftOperator(filter?.operator ?? "gte");
            setDraftValues(nextValues.map((value) =>
                field.unit === "percent" ? String(Number(value) * 100) : String(value)
            ));
        }, 0);
        return () => window.clearTimeout(timeout);
    }, [field.unit, filter]);

    if (field.type === "enum") {
        const selected = filter && Array.isArray(filter.value) ? filter.value.map(String) : filter ? [String(filter.value)] : [];
        return (
            <div ref={enumControlRef} className="relative min-w-0">
                <button
                    ref={enumButtonRef}
                    type="button"
                    aria-label={`${field.label} options`}
                    aria-expanded={enumOpen}
                    onClick={() => setEnumOpen((current) => !current)}
                    className="flex min-h-10 w-full cursor-pointer items-center justify-between rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none transition hover:border-emerald-400 focus-visible:border-emerald-400 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
                >
                    <span className="truncate">{selected.length ? `${selected.length} selected` : "Any"}</span>
                    <span className="text-slate-400">⌄</span>
                </button>
                {enumOpen && (
                    <div
                        role="group"
                        aria-label={`${field.label} choices`}
                        className="absolute z-40 mt-2 max-h-64 w-64 max-w-[calc(100vw-2rem)] overflow-auto rounded-xl border border-slate-200 bg-white p-2 shadow-2xl dark:border-slate-700 dark:bg-slate-900"
                    >
                        {field.options.length === 0 ? (
                            <p className="px-2 py-3 text-xs text-slate-500">No values in this snapshot.</p>
                        ) : field.options.map((option) => {
                            const checked = selected.includes(option.value);
                            const selectionLimitReached = !checked && selected.length >= MAX_FILTER_VALUES;
                            return (
                                <label
                                    key={option.value}
                                    className={`flex items-center gap-2 rounded-lg px-2 py-2 text-sm ${
                                        selectionLimitReached
                                            ? "cursor-not-allowed opacity-50"
                                            : "cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800"
                                    }`}
                                >
                                    <input
                                        type="checkbox"
                                        checked={checked}
                                        disabled={selectionLimitReached}
                                        onChange={() => {
                                            const next = checked ? selected.filter((value) => value !== option.value) : [...selected, option.value];
                                            onChange(next.length ? { field: field.id, operator: "in", value: next } : undefined);
                                        }}
                                        className="accent-emerald-500"
                                    />
                                    <span>{option.label}</span>
                                </label>
                            );
                        })}
                    </div>
                )}
            </div>
        );
    }

    const operator = draftOperator;
    const canonicalValue = (value: string) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return value;
        return field.unit === "percent" ? number / 100 : number;
    };
    const commit = (nextOperator: ScreenerFilter["operator"], values: string[]) => {
        if (!values.length || values.every((value) => value === "")) {
            onChange(undefined);
            return;
        }
        if (values.some((value) => value === "")) return;
        if (
            field.type === "number"
            && values.some((value) =>
                value.trim().endsWith(".") || !Number.isFinite(Number(value))
            )
        ) {
            return;
        }
        const canonicalValues = values.map(canonicalValue);
        onChange({
            field: field.id,
            operator: nextOperator,
            value: nextOperator === "between" ? canonicalValues : canonicalValues[0],
        });
    };
    const updateDraft = (index: number, value: string) => {
        const next = [...draftValues];
        next[index] = value;
        setDraftValues(next);
        commit(operator, operator === "between" ? [next[0] ?? "", next[1] ?? ""] : [next[0] ?? ""]);
    };

    return (
        <div className="flex min-w-0 gap-1.5">
            <select
                aria-label={`${field.label} operator`}
                value={operator}
                onChange={(event) => {
                    const next = event.target.value as ScreenerFilter["operator"];
                    setDraftOperator(next);
                    const values = next === "between"
                        ? [draftValues[0] ?? "", draftValues[1] ?? ""]
                        : [draftValues[0] ?? ""];
                    setDraftValues(values);
                    commit(next, values);
                }}
                className="w-[128px] shrink-0 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-600 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300"
            >
                {field.operators.map((value) => (
                    <option value={value} key={value}>{filterOperatorLabel(value)}</option>
                ))}
            </select>
            <input
                aria-label={`${field.label} value`}
                type={field.type === "date" ? "date" : "text"}
                inputMode={field.type === "number" ? "decimal" : undefined}
                value={draftValues[0] ?? ""}
                onChange={(event) => updateDraft(0, event.target.value)}
                placeholder={field.unit === "percent" ? "%" : "Value"}
                className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 text-sm outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-950"
            />
            {operator === "between" && (
                <input
                    aria-label={`${field.label} maximum`}
                    type={field.type === "date" ? "date" : "text"}
                    inputMode={field.type === "number" ? "decimal" : undefined}
                    value={draftValues[1] ?? ""}
                    onChange={(event) => updateDraft(1, event.target.value)}
                    placeholder="Max"
                    className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 text-sm outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-950"
                />
            )}
        </div>
    );
}

export function ScreenerContent() {
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
        <main className="h-full overflow-y-auto bg-[#f5f7f8] px-3 py-5 text-slate-900 dark:bg-[#0b1014] dark:text-slate-100 md:px-7 md:py-7">
            <div className="mx-auto max-w-[1580px] space-y-4">
                <header className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-[#111820]">
                    <div className="flex flex-col gap-4 p-5 md:flex-row md:items-center md:justify-between md:p-6">
                        <div>
                            <div className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-600 dark:text-emerald-400">
                                <SlidersHorizontal size={14} />
                                Quantify Market Intelligence
                            </div>
                            <h1 className="text-2xl font-bold tracking-tight md:text-3xl">Stock Screener</h1>
                            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                                S&P 500 + Russell 2000 · {metadata?.supported_finviz_fields ?? "—"} Finviz-aligned fields
                            </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2 dark:border-slate-700 dark:bg-slate-900">
                                <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Matches</div>
                                <div className="font-mono text-xl font-semibold">{loading ? "···" : (result?.total ?? 0).toLocaleString()}</div>
                            </div>
                            <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2 dark:border-slate-700 dark:bg-slate-900">
                                <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Published snapshot</div>
                                <div className="font-mono text-sm font-semibold">{result?.as_of_date ?? metadata?.as_of_date ?? "No data"}</div>
                            </div>
                            <button
                                onClick={() => void loadMetadata()}
                                aria-label="Refresh results"
                                className="grid size-11 place-items-center rounded-xl border border-slate-200 bg-white text-slate-500 transition hover:border-emerald-400 hover:text-emerald-500 dark:border-slate-700 dark:bg-slate-900"
                            >
                                <RefreshCw size={17} className={loading ? "animate-spin" : ""} />
                            </button>
                        </div>
                    </div>
                    {(result?.freshness?.status === "stale" || metadata?.freshness?.status === "stale") && (
                        <div className="flex items-center gap-2 border-t border-amber-200 bg-amber-50 px-5 py-2.5 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300">
                            <AlertTriangle size={16} />
                            Data is {(result?.freshness ?? metadata?.freshness)?.lag_sessions} market sessions behind the latest completed session.
                        </div>
                    )}
                </header>

                <section className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-[#111820]">
                    <div className="flex items-center justify-between border-b border-slate-200 p-3 dark:border-slate-800 md:hidden">
                        <button onClick={() => setMobileFiltersOpen(true)} className="flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950">
                            <Filter size={16} /> Filters {filters.length ? `(${filters.length})` : ""}
                        </button>
                    </div>
                    <div className={`${mobileFiltersOpen ? "fixed inset-0 z-50 overflow-auto bg-white p-4 dark:bg-[#0b1014]" : "hidden"} md:block`}>
                        <div className="mb-4 flex items-center justify-between md:hidden">
                            <h2 className="text-lg font-semibold">Filters</h2>
                            <button aria-label="Close filters" onClick={() => setMobileFiltersOpen(false)}><X /></button>
                        </div>
                        <div className="flex flex-col gap-3 border-b border-slate-200 p-4 dark:border-slate-800 lg:flex-row lg:items-center lg:justify-between">
                            <div className="flex gap-1 rounded-xl bg-slate-100 p-1 dark:bg-slate-900">
                                {CATEGORIES.map((category) => (
                                    <button
                                        key={category}
                                        onClick={() => setActiveCategory(category)}
                                        className={`rounded-lg px-4 py-2 text-sm font-semibold transition ${activeCategory === category ? "bg-white text-emerald-600 shadow-sm dark:bg-slate-800 dark:text-emerald-400" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}
                                    >
                                        {category}
                                    </button>
                                ))}
                            </div>
                            <label className="relative block w-full lg:w-72">
                                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input
                                    value={search}
                                    onChange={(event) => setSearch(event.target.value)}
                                    placeholder="Search fields"
                                    className="h-10 w-full rounded-xl border border-slate-200 bg-slate-50 pl-9 pr-3 text-sm outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900"
                                />
                            </label>
                        </div>

                        {filters.length > 0 && (
                            <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50/70 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/40">
                                {filters.map((filter) => {
                                    const field = fieldMap.get(filter.field);
                                    if (!field) return null;
                                    return (
                                        <button key={filter.field} onClick={() => updateFilter(filter.field)} className="flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-800 hover:border-emerald-400 dark:border-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-300">
                                            {filterLabel(filter, field)} <X size={12} />
                                        </button>
                                    );
                                })}
                                <button onClick={() => { setFilters([]); setPage(0); }} className="px-2 text-xs font-semibold text-slate-500 hover:text-rose-500">Clear all</button>
                                {filterLimitReached && (
                                    <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
                                        Maximum {MAX_SCREENER_FILTERS} filters reached.
                                    </span>
                                )}
                            </div>
                        )}

                        <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
                            {visibleFields.map((field) => {
                                const active = filters.find((filter) => filter.field === field.id);
                                return (
                                    <div key={field.id} className={`min-w-0 rounded-xl border p-3 transition ${active ? "border-emerald-400 bg-emerald-50/50 dark:bg-emerald-950/20" : "border-slate-200 dark:border-slate-800"} ${!field.available ? "opacity-50" : ""}`}>
                                        <div className="mb-2 flex items-start justify-between gap-2">
                                            <div>
                                                <div className="flex items-center gap-1">
                                                    <label className="block text-xs font-semibold text-slate-700 dark:text-slate-200">{field.label}</label>
                                                    {field.description && (
                                                        <span className="group/help relative">
                                                            <button
                                                                type="button"
                                                                aria-label={`About ${field.label}`}
                                                                className="grid size-4 place-items-center rounded-full text-slate-400 hover:text-emerald-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                                                            >
                                                                <Info size={12} />
                                                            </button>
                                                            <span
                                                                role="tooltip"
                                                                className="pointer-events-none absolute left-0 top-5 z-[60] hidden w-72 rounded-lg bg-slate-950 px-3 py-2 text-[11px] font-normal leading-relaxed text-white shadow-xl group-hover/help:block group-focus-within/help:block dark:bg-slate-100 dark:text-slate-900"
                                                            >
                                                                {field.description}
                                                            </span>
                                                        </span>
                                                    )}
                                                </div>
                                                <span className={`text-[10px] ${field.coverage < 0.5 ? "text-amber-500" : "text-slate-400"}`}>{Math.round(field.coverage * 100)}% coverage</span>
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
                                                    className="max-w-24 rounded-md border-0 bg-transparent text-[10px] text-slate-500 outline-none"
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
                        <div className="sticky bottom-0 z-50 border-t border-slate-200 bg-white p-4 md:hidden dark:border-slate-800 dark:bg-[#0b1014]">
                            <button onClick={() => setMobileFiltersOpen(false)} className="w-full rounded-xl bg-emerald-500 py-3 font-semibold text-slate-950">Show {result?.total ?? 0} matches</button>
                        </div>
                    </div>
                </section>

                <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-[#111820]">
                    <div className="flex flex-col gap-3 border-b border-slate-200 p-4 dark:border-slate-800 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                            <h2 className="font-semibold">Screening results</h2>
                            <p className="text-xs text-slate-500">Click a column to sort. Results are calculated from the published snapshot.</p>
                        </div>
                        <details className="relative">
                            <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium hover:border-emerald-400 dark:border-slate-700">
                                <Columns3 size={15} /> Columns
                            </summary>
                            <div className="absolute right-0 z-30 mt-2 max-h-80 w-72 overflow-auto rounded-xl border border-slate-200 bg-white p-2 shadow-2xl dark:border-slate-700 dark:bg-slate-900">
                                {metadata?.fields.filter((field) => field.available && field.result_column).map((field) => (
                                    <label key={field.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800">
                                        <input
                                            type="checkbox"
                                            checked={columns.includes(field.id)}
                                            onChange={() => setColumns((current) => current.includes(field.id) ? current.filter((value) => value !== field.id) : [...current, field.id].slice(0, 30))}
                                            className="accent-emerald-500"
                                        />
                                        <span className="flex-1">{field.label}</span>
                                        <span className="text-[10px] text-slate-400">{field.category.slice(0, 4)}</span>
                                    </label>
                                ))}
                            </div>
                        </details>
                    </div>

                    {error ? (
                        <div className="m-4 flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
                            <span>{error}</span>
                            <button onClick={() => void loadMetadata()} className="font-semibold underline">Retry</button>
                        </div>
                    ) : (
                        <div className="max-h-[680px] overflow-auto">
                            <table className="min-w-full whitespace-nowrap text-left text-sm">
                                <thead className="sticky top-0 z-10 bg-slate-100/95 text-[11px] uppercase tracking-wider text-slate-500 backdrop-blur dark:bg-slate-900/95">
                                    <tr>
                                        {selectedColumns.map((column) => {
                                            const field = fieldMap.get(column);
                                            return (
                                                <th key={column} className="px-4 py-3 font-semibold">
                                                    <button
                                                        title={field?.description ?? undefined}
                                                        onClick={() => {
                                                            if (column === "name" || column === "ticker" || field) {
                                                                setSort((current) => ({ field: column, direction: current.field === column && current.direction === "desc" ? "asc" : "desc" }));
                                                                setPage(0);
                                                            }
                                                        }}
                                                        className="flex items-center gap-1 hover:text-emerald-500"
                                                    >
                                                        {column === "ticker" ? "Ticker" : column === "name" ? "Company" : field?.label ?? column}
                                                        {sort.field === column && <span>{sort.direction === "desc" ? "↓" : "↑"}</span>}
                                                    </button>
                                                </th>
                                            );
                                        })}
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                    {loading && !result ? (
                                        Array.from({ length: 8 }).map((_, index) => (
                                            <tr key={index}>{selectedColumns.map((column) => <td key={column} className="px-4 py-4"><div className="h-4 w-24 animate-pulse rounded bg-slate-200 dark:bg-slate-800" /></td>)}</tr>
                                        ))
                                    ) : result?.items.length ? result.items.map((row, rowIndex) => (
                                        <tr key={String(row.ticker ?? rowIndex)} className="transition hover:bg-emerald-50/50 dark:hover:bg-emerald-950/20">
                                            {selectedColumns.map((column) => (
                                                <td key={column} className={`px-4 py-3 ${column === "ticker" ? "font-mono font-bold text-emerald-600 dark:text-emerald-400" : column === "name" ? "max-w-64 truncate font-medium" : "font-mono text-slate-700 dark:text-slate-300"}`}>
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
                                        <tr><td colSpan={selectedColumns.length} className="px-6 py-20 text-center"><Filter className="mx-auto mb-3 text-slate-300" size={32} /><p className="font-semibold">No stocks match these filters</p><p className="mt-1 text-sm text-slate-500">Remove one or more conditions and try again.</p></td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    )}

                    <footer className="flex items-center justify-between border-t border-slate-200 px-4 py-3 text-sm dark:border-slate-800">
                        <span className="text-slate-500">
                            {result?.total ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, result.total)} of ${result.total.toLocaleString()}` : "0 results"}
                        </span>
                        <div className="flex items-center gap-2">
                            <button aria-label="Previous page" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))} className="grid size-9 place-items-center rounded-lg border border-slate-200 disabled:opacity-30 dark:border-slate-700"><ChevronLeft size={16} /></button>
                            <span className="min-w-24 text-center font-mono text-xs">Page {page + 1} / {totalPages}</span>
                            <button aria-label="Next page" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))} className="grid size-9 place-items-center rounded-lg border border-slate-200 disabled:opacity-30 dark:border-slate-700"><ChevronRight size={16} /></button>
                        </div>
                    </footer>
                </section>
            </div>
        </main>
    );
}

export default function ScreenerPage() {
    return (
        <Suspense fallback={<div className="h-full bg-[#f5f7f8] dark:bg-[#0b1014]" />}>
            <ScreenerContent />
        </Suspense>
    );
}
