export type FilterOperator = "eq" | "in" | "lt" | "lte" | "gt" | "gte" | "between";

export interface ScreenerFilter {
    field: string;
    operator: FilterOperator;
    value: string | number | Array<string | number>;
}

export interface ScreenerField {
    id: string;
    label: string;
    category: "Descriptive" | "Fundamental" | "Technical";
    type: "number" | "enum" | "date";
    unit: "number" | "currency" | "percent" | "integer" | "date" | "text";
    operators: FilterOperator[];
    finviz_field?: string;
    presets: Array<{ label: string; operator: FilterOperator; value: string | number | number[] }>;
    options: Array<{ value: string; label: string }>;
    coverage: number;
    available: boolean;
    default_column: boolean;
    result_column: boolean;
}

export interface ScreenerMetadata {
    as_of_date: string | null;
    freshness: { status: "current" | "stale"; lag_sessions: number; latest_completed_session: string } | null;
    universe: string;
    record_count: number;
    supported_finviz_fields: number;
    fields: ScreenerField[];
    default_columns: string[];
}

export interface ScreenerQueryResponse {
    total: number;
    items: Array<Record<string, unknown>>;
    limit: number;
    offset: number;
    as_of_date: string | null;
    freshness: ScreenerMetadata["freshness"];
}

export function decodeFilters(value: string | null): ScreenerFilter[] {
    if (!value) return [];
    try {
        const parsed = JSON.parse(value);
        if (!Array.isArray(parsed)) return [];
        return parsed.filter((item) =>
            item && typeof item.field === "string" && typeof item.operator === "string"
        ) as ScreenerFilter[];
    } catch {
        return [];
    }
}

export function encodeFilters(filters: ScreenerFilter[]): string {
    return JSON.stringify(filters);
}

export function formatScreenerValue(value: unknown, field?: ScreenerField): string {
    if (value === null || value === undefined || value === "") return "—";
    if (!field) return String(value);
    if (field.unit === "date" || field.unit === "text") return String(value);
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    if (field.unit === "percent") return `${(number * 100).toFixed(Math.abs(number) < 0.1 ? 1 : 0)}%`;
    if (field.unit === "currency") {
        if (Math.abs(number) >= 1e12) return `$${(number / 1e12).toFixed(2)}T`;
        if (Math.abs(number) >= 1e9) return `$${(number / 1e9).toFixed(2)}B`;
        if (Math.abs(number) >= 1e6) return `$${(number / 1e6).toFixed(2)}M`;
        return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(number);
    }
    if (field.unit === "integer") return Math.round(number).toLocaleString();
    return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function filterLabel(filter: ScreenerFilter, field: ScreenerField): string {
    const operators: Record<FilterOperator, string> = {
        eq: "=",
        in: "in",
        lt: "<",
        lte: "≤",
        gt: ">",
        gte: "≥",
        between: "between",
    };
    const render = (value: string | number) => {
        if (field.type === "enum") {
            return field.options.find((option) => option.value === String(value))?.label ?? String(value);
        }
        return formatScreenerValue(value, field);
    };
    const rendered = Array.isArray(filter.value) ? filter.value.map(render).join(" · ") : render(filter.value);
    return `${field.label} ${operators[filter.operator]} ${rendered}`;
}
