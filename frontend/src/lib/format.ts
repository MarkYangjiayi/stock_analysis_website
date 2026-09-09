export const formatMoney = (
    value?: number | null,
    currency?: string | null,
    maximumFractionDigits = 2,
) => {
    if (value == null || !Number.isFinite(value)) return "—";
    const normalizedCurrency = currency?.trim().toUpperCase() || "USD";
    try {
        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: normalizedCurrency,
            maximumFractionDigits,
        }).format(value);
    } catch {
        return `${normalizedCurrency} ${value.toLocaleString("en-US", { maximumFractionDigits })}`;
    }
};

export const formatSignedPercent = (value?: number | null, digits = 2) => {
    if (value == null || !Number.isFinite(value)) return "—";
    if (value === 0) return `${(0).toFixed(digits)}%`;
    return `${value > 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(digits)}%`;
};

export const formatCompactNumber = (value?: number | null, digits = 2) =>
    value == null || !Number.isFinite(value)
        ? "—"
        : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: digits }).format(value);
