import type { ThemeStage } from "@/lib/api";

export const STAGE_LABELS: Record<ThemeStage, string> = {
    emerging: "Emerging",
    accelerating: "Accelerating",
    leading: "Leading",
    crowded: "Crowded",
    fading: "Fading",
    neutral: "Neutral",
    insufficient: "Insufficient members",
};

export const STAGE_DESCRIPTIONS: Record<ThemeStage, string> = {
    emerging: "Heat broke out of its own history with widening breadth and at least three ignitions.",
    accelerating: "Short-window heat is running well above the 20-session trend with price and breadth support.",
    leading: "In the top quartile of trend heat for most of the last 20 sessions, with most members above their 50-day average.",
    crowded: "Hot while members move in lockstep after a large run, or a proxy ETF launched within the past year.",
    fading: "Short-window heat has rolled over from its recent peak while breadth narrows.",
    neutral: "No stage condition currently holds.",
    insufficient: "Too few priced members to rank this theme.",
};

// Semantic tokens only: info = early, up = strengthening, accent = leadership,
// caution (violet) = crowding warning, down = weakening.
export const STAGE_CLASSES: Record<ThemeStage, string> = {
    emerging: "border-info/30 bg-info/10 text-info",
    accelerating: "border-up/30 bg-up/10 text-up",
    leading: "border-accent/30 bg-accent-soft text-accent-strong",
    crowded: "border-caution/30 bg-caution/10 text-caution",
    fading: "border-down/30 bg-down/10 text-down",
    neutral: "border-line bg-surface-muted text-fg-muted",
    insufficient: "border-dashed border-line-strong text-fg-muted",
};

export const STAGE_DOTS: Record<ThemeStage, string> = {
    emerging: "bg-info",
    accelerating: "bg-up",
    leading: "bg-accent",
    crowded: "bg-caution",
    fading: "bg-down",
    neutral: "bg-flat",
    insufficient: "bg-line-strong",
};

export const STAGE_FILTERS: ThemeStage[] = ["emerging", "accelerating", "leading", "crowded", "fading", "neutral"];

const MINUS = "−";

const finite = (value: number | null | undefined): value is number => value != null && Number.isFinite(value);

export const displayTicker = (ticker: string) => ticker.replace(/\.US$/, "");

export const tickerHref = (ticker: string) => `/?ticker=${encodeURIComponent(ticker)}`;

export const toneClass = (value: number | null | undefined) =>
    !finite(value) || value === 0 ? "text-flat" : value > 0 ? "text-up" : "text-down";

export const formatHeat = (value: number | null | undefined, digits = 1) =>
    finite(value) ? value.toFixed(digits) : "—";

/** Fraction (0.64) → "64%". */
export const formatShare = (value: number | null | undefined, digits = 0) =>
    finite(value) ? `${(value * 100).toFixed(digits)}%` : "—";

/** Signed fraction change in percentage points (0.12 → "+12 pts"). */
export const formatPointChange = (value: number | null | undefined, digits = 0) => {
    if (!finite(value)) return "—";
    const points = value * 100;
    if (Number(points.toFixed(digits)) === 0) return `${(0).toFixed(digits)} pts`;
    return `${points > 0 ? "+" : MINUS}${Math.abs(points).toFixed(digits)} pts`;
};

/** Signed z-like scores and composite components. */
export const formatSigned = (value: number | null | undefined, digits = 2) => {
    if (!finite(value)) return "—";
    if (Number(value.toFixed(digits)) === 0) return (0).toFixed(digits);
    return `${value > 0 ? "+" : MINUS}${Math.abs(value).toFixed(digits)}`;
};

export const formatMultiple = (value: number | null | undefined, digits = 2) =>
    finite(value) ? `${value.toFixed(digits)}×` : "—";

/** Rank improvement is positive (rank 5 → 2 is +3). */
export const formatRankChange = (value: number | null | undefined) => {
    if (!finite(value)) return "—";
    if (value === 0) return "0";
    return `${value > 0 ? "↑" : "↓"}${Math.abs(value)}`;
};

/** Sparkline input: nulls become NaN so the drawn line skips them instead of plotting zero. */
export const sparkValues = (values: Array<number | null>) => values.map((value) => (finite(value) ? value : Number.NaN));

/** Direction of a series from its first to its last finite value. */
export const seriesChange = (values: Array<number | null>) => {
    const points = values.filter(finite);
    return points.length < 2 ? null : points[points.length - 1] - points[0];
};
