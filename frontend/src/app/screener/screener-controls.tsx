"use client";

import { useEffect, useRef, useState } from "react";

import {
    filterOperatorLabel,
    MAX_FILTER_VALUES,
    MAX_SCREENER_FILTERS,
    ScreenerField,
    ScreenerFilter,
} from "@/lib/screener";

const MAX_OFFSET = 1_000_000;
const PAGE_SIZE = 50;
const MAX_PAGE = Math.floor(MAX_OFFSET / PAGE_SIZE);
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
