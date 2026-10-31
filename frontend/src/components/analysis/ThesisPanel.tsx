"use client";

import { useEffect, useState, type ReactNode } from "react";
import { LockKeyhole, NotebookPen, Plus, Target, Trash2 } from "lucide-react";
import {
    ApiError,
    fetchPersonalThesis,
    fetchPersonalThesisVersions,
    savePersonalThesis,
    type ThesisContent,
    type ThesisVersion,
} from "@/lib/api";
import { formatMoney } from "@/lib/format";

// Same caps as the API, so a full list stops offering another row.
const LIMITS = { reasons: 5, metrics: 12, conditions: 12, catalysts: 12 } as const;

const CONDITION_LABELS = { sell: "Sell", warning: "Warning" } as const;

/** Editable copy of a thesis; numbers stay strings until the draft is saved. */
interface Draft {
    reasons: string[];
    metrics: Array<{ name: string; threshold: string }>;
    conditions: Array<{ kind: "sell" | "warning"; text: string }>;
    valuation: { bear: string; base: string; bull: string; note: string };
    catalysts: Array<{ date: string; event: string }>;
    note: string;
}

const toDraft = (content: ThesisContent | null): Draft => ({
    reasons: content?.reasons.length ? [...content.reasons] : ["", ""],
    metrics: content?.metrics.length ? content.metrics.map((metric) => ({ ...metric })) : [{ name: "", threshold: "" }],
    conditions: content?.conditions.length ? content.conditions.map((condition) => ({ ...condition })) : [{ kind: "sell", text: "" }],
    valuation: {
        bear: content?.valuation.bear?.toString() ?? "",
        base: content?.valuation.base?.toString() ?? "",
        bull: content?.valuation.bull?.toString() ?? "",
        note: content?.valuation.note ?? "",
    },
    catalysts: content?.catalysts.length ? content.catalysts.map((catalyst) => ({ date: catalyst.date ?? "", event: catalyst.event })) : [{ date: "", event: "" }],
    note: "",
});

/** Drops empty rows, or says why the draft cannot be saved. */
const fromDraft = (draft: Draft): { content: ThesisContent } | { error: string } => {
    const amount = (value: string) => (value.trim() ? Number(value) : null);
    const valuation = {
        bear: amount(draft.valuation.bear),
        base: amount(draft.valuation.base),
        bull: amount(draft.valuation.bull),
        note: draft.valuation.note.trim(),
    };
    const values = [valuation.bear, valuation.base, valuation.bull].filter((value): value is number => value !== null);
    if (values.some((value) => !Number.isFinite(value) || value <= 0)) return { error: "Valuation values must be positive numbers." };
    if (values.some((value, index) => index > 0 && value < values[index - 1])) return { error: "Valuation values must run bear ≤ base ≤ bull." };
    const metrics = draft.metrics
        .map((metric) => ({ name: metric.name.trim(), threshold: metric.threshold.trim() }))
        .filter((metric) => metric.name || metric.threshold);
    if (metrics.some((metric) => !metric.name || !metric.threshold)) return { error: "Each metric needs a name and a threshold." };
    const catalysts = draft.catalysts
        .map((catalyst) => ({ date: catalyst.date || null, event: catalyst.event.trim() }))
        .filter((catalyst) => catalyst.date || catalyst.event);
    if (catalysts.some((catalyst) => !catalyst.event)) return { error: "Each catalyst needs a description." };
    return {
        content: {
            reasons: draft.reasons.map((reason) => reason.trim()).filter(Boolean),
            metrics,
            conditions: draft.conditions
                .map((condition) => ({ kind: condition.kind, text: condition.text.trim() }))
                .filter((condition) => condition.text),
            valuation,
            catalysts,
        },
    };
};

const pricePosition = (price: number, valuation: ThesisContent["valuation"]) => {
    if (valuation.bear != null && price < valuation.bear) return "below the bear value";
    if (valuation.bull != null && price > valuation.bull) return "above the bull value";
    if (valuation.base != null) return price < valuation.base ? "below the base value" : "at or above the base value";
    return null;
};

const savedAt = (iso: string) => {
    const parsed = new Date(iso);
    return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
};

function ViewSection({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
    return (
        <div className={`min-w-0 rounded-lg border p-3 ${className}`}>
            <h3 className="eyebrow">{title}</h3>
            <div className="mt-2">{children}</div>
        </div>
    );
}

const Empty = ({ children }: { children: ReactNode }) => <p className="text-sm text-fg-muted">{children}</p>;

function ThesisView({ content, currency, price }: { content: ThesisContent; currency: string; price: number | null }) {
    const { valuation } = content;
    const position = price != null ? pricePosition(price, valuation) : null;
    const catalysts = [...content.catalysts].sort((left, right) => (left.date ?? "9999").localeCompare(right.date ?? "9999"));
    return (
        <div className="grid gap-3 lg:grid-cols-2">
            <ViewSection title="Why I own it">
                {content.reasons.length ? <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-6">{content.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ol> : <Empty>No reasons written.</Empty>}
            </ViewSection>
            <ViewSection title="What to watch">
                {content.metrics.length ? <dl className="space-y-1.5 text-sm">{content.metrics.map((metric, index) => (
                    <div key={index} className="flex flex-wrap justify-between gap-x-3 gap-y-0.5">
                        <dt className="font-medium">{metric.name}</dt>
                        <dd className="font-mono text-xs leading-5 text-fg-muted">{metric.threshold}</dd>
                    </div>
                ))}</dl> : <Empty>No metrics yet.</Empty>}
            </ViewSection>
            <ViewSection title="Sell and warning conditions">
                {content.conditions.length ? <ul className="space-y-1.5 text-sm leading-6">{content.conditions.map((condition, index) => (
                    <li key={index} className="flex gap-2">
                        <span className={`mt-0.5 h-fit shrink-0 rounded px-1.5 text-[10px] font-semibold uppercase leading-5 tracking-wide ${condition.kind === "sell" ? "bg-danger/10 text-danger" : "bg-accent-soft text-accent"}`}>{CONDITION_LABELS[condition.kind]}</span>
                        <span>{condition.text}</span>
                    </li>
                ))}</ul> : <Empty>No conditions yet.</Empty>}
            </ViewSection>
            <ViewSection title="Valuation range">
                <dl className="grid grid-cols-3 gap-2 text-center">
                    {(["bear", "base", "bull"] as const).map((name) => (
                        <div key={name} className="rounded-md bg-surface-muted px-2 py-1.5">
                            <dt className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">{name}</dt>
                            <dd className="font-mono text-sm font-semibold">{formatMoney(valuation[name], currency)}</dd>
                        </div>
                    ))}
                </dl>
                {position && price != null && <p className="mt-2 text-xs text-fg-muted">Price {formatMoney(price, currency)} is {position}.</p>}
                {valuation.note && <p className="mt-1 text-xs text-fg-muted">{valuation.note}</p>}
            </ViewSection>
            <ViewSection title="Catalysts" className="lg:col-span-2">
                {catalysts.length ? <ul className="space-y-1 text-sm">{catalysts.map((catalyst, index) => (
                    <li key={index} className="flex gap-3"><span className="w-24 shrink-0 font-mono text-xs leading-5 text-fg-muted">{catalyst.date ?? "No date"}</span><span>{catalyst.event}</span></li>
                ))}</ul> : <Empty>No catalysts yet.</Empty>}
            </ViewSection>
        </div>
    );
}

function EditorSection({ title, hint, addLabel, onAdd, children }: { title: string; hint?: string; addLabel?: string; onAdd?: () => void; children: ReactNode }) {
    return (
        <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">{title}</legend>
            {hint && <p className="text-xs text-fg-muted">{hint}</p>}
            {children}
            {addLabel && onAdd && <button type="button" className="secondary-button min-h-8 px-2.5 text-xs" onClick={onAdd}><Plus size={13} /> {addLabel}</button>}
        </fieldset>
    );
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
    return <button type="button" onClick={onClick} className="shrink-0 rounded-md p-2 text-fg-muted hover:bg-danger/10 hover:text-danger" aria-label={label}><Trash2 size={14} /></button>;
}

function ThesisEditor({ draft, onChange }: { draft: Draft; onChange: (next: Draft) => void }) {
    const set = <K extends keyof Draft>(key: K, value: Draft[K]) => onChange({ ...draft, [key]: value });
    const replace = <T,>(items: T[], index: number, value: T) => items.map((item, position) => (position === index ? value : item));
    const without = <T,>(items: T[], index: number) => items.filter((_, position) => position !== index);
    return (
        <div className="space-y-5">
            <EditorSection title="Why I own it" hint="Two or three reasons." addLabel="Add reason" onAdd={draft.reasons.length < LIMITS.reasons ? () => set("reasons", [...draft.reasons, ""]) : undefined}>
                {draft.reasons.map((reason, index) => (
                    <div key={index} className="flex items-start gap-2">
                        <textarea aria-label={`Reason ${index + 1}`} className="control-field min-h-16" maxLength={500} value={reason} onChange={(event) => set("reasons", replace(draft.reasons, index, event.target.value))} />
                        <RemoveButton label={`Remove reason ${index + 1}`} onClick={() => set("reasons", without(draft.reasons, index))} />
                    </div>
                ))}
            </EditorSection>
            <EditorSection title="What to watch" hint="Write thresholds as numbers where you can, e.g. “< 20% YoY for two quarters”." addLabel="Add metric" onAdd={draft.metrics.length < LIMITS.metrics ? () => set("metrics", [...draft.metrics, { name: "", threshold: "" }]) : undefined}>
                {draft.metrics.map((metric, index) => (
                    <div key={index} className="flex items-start gap-2">
                        <input aria-label={`Metric ${index + 1}`} className="control-field" maxLength={200} placeholder="Metric" value={metric.name} onChange={(event) => set("metrics", replace(draft.metrics, index, { ...metric, name: event.target.value }))} />
                        <input aria-label={`Threshold ${index + 1}`} className="control-field" maxLength={300} placeholder="Threshold" value={metric.threshold} onChange={(event) => set("metrics", replace(draft.metrics, index, { ...metric, threshold: event.target.value }))} />
                        <RemoveButton label={`Remove metric ${index + 1}`} onClick={() => set("metrics", without(draft.metrics, index))} />
                    </div>
                ))}
            </EditorSection>
            <EditorSection title="Sell and warning conditions" addLabel="Add condition" onAdd={draft.conditions.length < LIMITS.conditions ? () => set("conditions", [...draft.conditions, { kind: "warning", text: "" }]) : undefined}>
                {draft.conditions.map((condition, index) => (
                    <div key={index} className="flex items-start gap-2">
                        <select aria-label={`Condition ${index + 1} type`} className="control-field w-28 shrink-0" value={condition.kind} onChange={(event) => set("conditions", replace(draft.conditions, index, { ...condition, kind: event.target.value as "sell" | "warning" }))}>
                            <option value="sell">Sell</option>
                            <option value="warning">Warning</option>
                        </select>
                        <input aria-label={`Condition ${index + 1}`} className="control-field" maxLength={500} value={condition.text} onChange={(event) => set("conditions", replace(draft.conditions, index, { ...condition, text: event.target.value }))} />
                        <RemoveButton label={`Remove condition ${index + 1}`} onClick={() => set("conditions", without(draft.conditions, index))} />
                    </div>
                ))}
            </EditorSection>
            <EditorSection title="Valuation range" hint="Per-share values you have confirmed, in the listing currency.">
                <div className="grid gap-2 sm:grid-cols-3">
                    {(["bear", "base", "bull"] as const).map((name) => (
                        <label key={name} className="text-xs capitalize">{name}
                            <input className="control-field mt-1 font-mono" inputMode="decimal" value={draft.valuation[name]} onChange={(event) => set("valuation", { ...draft.valuation, [name]: event.target.value })} />
                        </label>
                    ))}
                </div>
                <label className="block text-xs">How you got there
                    <input className="control-field mt-1" maxLength={500} value={draft.valuation.note} onChange={(event) => set("valuation", { ...draft.valuation, note: event.target.value })} />
                </label>
            </EditorSection>
            <EditorSection title="Catalysts" addLabel="Add catalyst" onAdd={draft.catalysts.length < LIMITS.catalysts ? () => set("catalysts", [...draft.catalysts, { date: "", event: "" }]) : undefined}>
                {draft.catalysts.map((catalyst, index) => (
                    <div key={index} className="flex items-start gap-2">
                        <input aria-label={`Catalyst ${index + 1} date`} type="date" className="control-field w-40 shrink-0" value={catalyst.date} onChange={(event) => set("catalysts", replace(draft.catalysts, index, { ...catalyst, date: event.target.value }))} />
                        <input aria-label={`Catalyst ${index + 1}`} className="control-field" maxLength={300} value={catalyst.event} onChange={(event) => set("catalysts", replace(draft.catalysts, index, { ...catalyst, event: event.target.value }))} />
                        <RemoveButton label={`Remove catalyst ${index + 1}`} onClick={() => set("catalysts", without(draft.catalysts, index))} />
                    </div>
                ))}
            </EditorSection>
            <label className="block text-xs">What changed (optional, kept with this version)
                <input className="control-field mt-1" maxLength={300} value={draft.note} onChange={(event) => set("note", event.target.value)} />
            </label>
        </div>
    );
}

function ThesisHistory({ ticker, currency, onUnauthorized }: { ticker: string; currency: string; onUnauthorized: () => void }) {
    const [versions, setVersions] = useState<ThesisVersion[] | null>(null);
    const [error, setError] = useState("");
    const [selected, setSelected] = useState<number | null>(null);
    const load = () => {
        setError("");
        fetchPersonalThesisVersions(ticker)
            .then((result) => setVersions(result.versions))
            .catch((caught: unknown) => {
                if (caught instanceof ApiError && caught.status === 401) onUnauthorized();
                else setError(caught instanceof Error ? caught.message : "History unavailable.");
            });
    };
    return (
        <details className="research-details" onToggle={(event) => { if (event.currentTarget.open && !versions) load(); }}>
            <summary>History</summary>
            <div className="space-y-2 pt-2">
                {error && <p className="text-sm text-danger">{error}</p>}
                {!versions && !error && <p className="text-sm text-fg-muted">Loading versions…</p>}
                {versions?.map((version) => (
                    <div key={version.version}>
                        <button type="button" className="w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-surface-muted" aria-expanded={selected === version.version} onClick={() => setSelected(selected === version.version ? null : version.version)}>
                            <span className="font-mono font-semibold">v{version.version}</span> · {savedAt(version.created_at)}{version.note ? ` · ${version.note}` : ""}
                        </button>
                        {selected === version.version && <div className="mt-2"><ThesisView content={version.content} currency={currency} price={null} /></div>}
                    </div>
                ))}
            </div>
        </details>
    );
}

/** A core ticker's investment thesis: read, edit and keep every version. */
export default function ThesisPanel({
    ticker,
    currency,
    price,
    unlocked,
    inWatchlist,
    isCore,
    onUnlock,
    onUnauthorized,
    onToggleCore,
}: {
    ticker: string;
    currency: string;
    price: number | null;
    unlocked: boolean;
    inWatchlist: boolean;
    isCore: boolean;
    onUnlock: () => void;
    onUnauthorized: () => void;
    onToggleCore: (core: boolean) => void;
}) {
    const [loaded, setLoaded] = useState<{ ticker: string; thesis: ThesisVersion | null; error: string }>({ ticker: "", thesis: null, error: "" });
    const [reloadVersion, setReloadVersion] = useState(0);
    const [draft, setDraft] = useState<Draft | null>(null);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<{ message: string; conflict: boolean } | null>(null);

    useEffect(() => {
        if (!unlocked) return;
        const controller = new AbortController();
        fetchPersonalThesis(ticker, controller.signal)
            .then((result) => setLoaded({ ticker, thesis: result.thesis, error: "" }))
            .catch((caught: unknown) => {
                if (caught instanceof DOMException && caught.name === "AbortError") return;
                if (caught instanceof ApiError && caught.status === 401) {
                    onUnauthorized();
                    return;
                }
                setLoaded({ ticker, thesis: null, error: caught instanceof Error ? caught.message : "Thesis unavailable." });
            });
        return () => controller.abort();
    }, [onUnauthorized, reloadVersion, ticker, unlocked]);

    const loading = loaded.ticker !== ticker;
    const thesis = loading ? null : loaded.thesis;

    const startEditing = () => {
        setDraft(toDraft(thesis?.content ?? null));
        setSaveError(null);
    };
    const cancelEditing = () => {
        setDraft(null);
        setSaveError(null);
    };
    const reloadLatest = () => {
        cancelEditing();
        setReloadVersion((version) => version + 1);
    };
    const save = async () => {
        if (!draft) return;
        const parsed = fromDraft(draft);
        if ("error" in parsed) {
            setSaveError({ message: parsed.error, conflict: false });
            return;
        }
        setSaving(true);
        setSaveError(null);
        try {
            const result = await savePersonalThesis(ticker, parsed.content, thesis?.version ?? 0, draft.note.trim());
            setLoaded({ ticker, thesis: result.thesis, error: "" });
            setDraft(null);
        } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) {
                onUnauthorized();
                return;
            }
            setSaveError({
                message: caught instanceof Error ? caught.message : "Unable to save the thesis.",
                conflict: caught instanceof ApiError && caught.status === 409,
            });
        } finally {
            setSaving(false);
        }
    };

    return (
        <section className="research-panel" aria-labelledby="thesis-title">
            <header className="flex flex-wrap items-start justify-between gap-3 px-4 pb-3 pt-4 sm:px-5">
                <div className="min-w-0">
                    <p className="eyebrow">Thesis</p>
                    <h2 id="thesis-title" className="text-base font-semibold tracking-tight">投资逻辑</h2>
                    <p className="mt-1 max-w-2xl text-xs leading-5 text-fg-muted">Why you own it, what to watch and what would change your mind. Only you edit it; the tracker can only propose changes for you to confirm.</p>
                </div>
                {unlocked && <div className="flex flex-wrap items-center gap-2">
                    {inWatchlist
                        ? <button type="button" aria-pressed={isCore} onClick={() => onToggleCore(!isCore)} className={`${isCore ? "primary-button" : "secondary-button"} min-h-9 px-3 text-xs`} title={isCore ? "Core: thesis tracking. Click to move back to rule alerts." : "Thesis tracking covers core tickers only"}><Target size={14} /> {isCore ? "Core" : "Mark as core"}</button>
                        : <span className="text-xs text-fg-muted">Add to the watchlist to make it core</span>}
                    {!draft && !loading && !loaded.error && <button type="button" className="secondary-button min-h-9 px-3 text-xs" onClick={startEditing}><NotebookPen size={14} /> {thesis ? "Edit" : "Write thesis"}</button>}
                </div>}
            </header>

            <div className="px-4 pb-4 sm:px-5">
                {!unlocked ? (
                    <div className="rounded-lg border border-dashed p-5 text-center">
                        <p className="text-sm text-fg-muted">Theses are personal. Unlock the workspace to read or write one.</p>
                        <button type="button" className="secondary-button mt-3 min-h-9 px-3 text-xs" onClick={onUnlock}><LockKeyhole size={14} /> Unlock</button>
                    </div>
                ) : loading ? (
                    <div className="h-48 animate-pulse rounded-lg bg-surface-muted" aria-label="Loading thesis" />
                ) : loaded.error ? (
                    <div className="space-y-2">
                        <p className="text-sm text-fg-muted" role="alert">{loaded.error}</p>
                        <button type="button" className="secondary-button min-h-9 px-3 text-xs" onClick={reloadLatest}>Try again</button>
                    </div>
                ) : draft ? (
                    <div className="space-y-4">
                        <ThesisEditor draft={draft} onChange={setDraft} />
                        <div className="flex flex-wrap items-center gap-2 border-t pt-3">
                            <button type="button" className="primary-button min-h-9 px-3 text-xs" onClick={() => void save()} disabled={saving}>{saving ? "Saving…" : thesis ? `Save as version ${thesis.version + 1}` : "Save first version"}</button>
                            <button type="button" className="secondary-button min-h-9 px-3 text-xs" onClick={cancelEditing} disabled={saving}>Cancel</button>
                            {saveError && <p role="alert" className="text-xs text-danger">
                                {saveError.message}
                                {saveError.conflict && <> <button type="button" className="underline" onClick={reloadLatest}>Reload latest</button> (discards this draft)</>}
                            </p>}
                        </div>
                    </div>
                ) : thesis ? (
                    <div className="space-y-3">
                        <ThesisView content={thesis.content} currency={currency} price={price} />
                        <p className="text-xs text-fg-muted">Version {thesis.version} · saved {savedAt(thesis.created_at)}{thesis.note ? ` · ${thesis.note}` : ""}</p>
                        <ThesisHistory key={thesis.version} ticker={ticker} currency={currency} onUnauthorized={onUnauthorized} />
                    </div>
                ) : (
                    <div className="rounded-lg border border-dashed p-5 text-center">
                        <p className="text-sm text-fg-muted">No thesis for {ticker} yet. Write down why you own it, what to watch, when you would sell, a valuation range and the next catalysts.</p>
                        {!isCore && <p className="mt-1 text-xs text-fg-muted">Tracking runs on core tickers only.</p>}
                    </div>
                )}
            </div>
        </section>
    );
}
