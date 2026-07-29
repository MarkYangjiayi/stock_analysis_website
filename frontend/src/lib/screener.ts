export type FilterOperator = "eq" | "in" | "lt" | "lte" | "gt" | "gte" | "between";

export const MAX_SCREENER_FILTERS = 64;
export const MAX_FILTER_VALUES = 100;

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

export function sanitizeFilters(
    filters: ScreenerFilter[],
    fields: ScreenerField[],
): ScreenerFilter[] {
    const fieldMap = new Map(fields.map((field) => [field.id, field]));
    const isIsoDate = (value: unknown) => {
        if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
        const parsed = new Date(`${value}T00:00:00Z`);
        return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
    };

    return filters.filter((filter) => {
        const field = fieldMap.get(filter.field);
        if (!field?.available || !field.operators.includes(filter.operator)) return false;
        const isBetween = filter.operator === "between";
        const isIn = filter.operator === "in";

        if (field.type === "enum") {
            const values = isIn ? filter.value : [filter.value];
            if (
                !Array.isArray(values) ||
                values.length === 0 ||
                values.length > MAX_FILTER_VALUES ||
                (!isIn && Array.isArray(filter.value))
            ) {
                return false;
            }
            const options = new Set(field.options.map((option) => option.value));
            return values.every((value) => typeof value === "string" && options.has(value));
        }

        const values = isBetween ? filter.value : [filter.value];
        if (
            !Array.isArray(values) ||
            (isBetween && values.length !== 2) ||
            (!isBetween && Array.isArray(filter.value))
        ) {
            return false;
        }
        if (field.type === "date") return values.every(isIsoDate);
        return values.every((value) =>
            typeof value !== "boolean" &&
            value !== null &&
            value !== "" &&
            Number.isFinite(Number(value))
        );
    }).slice(0, MAX_SCREENER_FILTERS);
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
