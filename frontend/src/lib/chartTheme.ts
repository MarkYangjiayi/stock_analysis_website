// Mirrors the Graphite amber tokens in globals.css for chart libraries that need literal colors.
export const chartTheme = (dark: boolean) => ({
    background: dark ? "#141416" : "#ffffff",
    backgroundMuted: dark ? "#101012" : "#f2f2ef",
    text: dark ? "#ededef" : "#18181b",
    textMuted: dark ? "#9a9aa2" : "#66666c",
    grid: dark ? "#222226" : "#ecece8",
    border: dark ? "#36363c" : "#d0d0ca",
    brand: dark ? "#f5a524" : "#b45309",
    brandSoft: dark ? "#2a2010" : "#fdf1d8",
    positive: dark ? "#3fcf8e" : "#15803d",
    negative: dark ? "#f2555a" : "#dc2626",
    caution: dark ? "#c084fc" : "#9333ea",
    info: dark ? "#60a5fa" : "#2563eb",
    tooltipBackground: dark ? "#1a1a1d" : "#ffffff",
    // Categorical series: accent first, then hues that stay distinct from up/down green and red.
    series: dark
        ? ["#f5a524", "#60a5fa", "#c084fc", "#f472b6", "#2dd4bf", "#a3e635", "#fb923c", "#a1a1aa"]
        : ["#b45309", "#2563eb", "#9333ea", "#db2777", "#0d9488", "#65a30d", "#ea580c", "#71717a"],
    // Wider categorical palette for many-entity charts (e.g. 11 GICS sectors). Order maximizes
    // contrast between neighbours; green/red hues are muted so they don't read as up/down.
    categorical: dark
        ? ["#f5a524", "#60a5fa", "#c084fc", "#f472b6", "#2dd4bf", "#a3e635", "#fb923c", "#818cf8", "#facc15", "#22d3ee", "#e879f9", "#a1a1aa"]
        : ["#b45309", "#2563eb", "#9333ea", "#db2777", "#0d9488", "#65a30d", "#ea580c", "#4f46e5", "#a16207", "#0891b2", "#c026d3", "#71717a"],
    // Diverging heat scale (cold → neutral → hot) for theme maps. Blue to amber keeps it off the
    // up/down green and red; each stop keeps `heatText` readable on top of it.
    heatDiverging: dark ? ["#1d4ed8", "#2a2a2e", "#b45309"] : ["#60a5fa", "#ecece8", "#f59e0b"],
    heatText: dark ? "#ededef" : "#18181b",
    heatMissing: dark ? "#1a1a1d" : "#f2f2ef",
});

const hexToRgb = (hex: string) => {
    const value = Number.parseInt(hex.slice(1), 16);
    return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
};

/**
 * Maps a value onto evenly spaced `stops` across `[min, max]` by linear RGB interpolation.
 * Missing values return null so callers pick an explicit "no data" fill instead of a fake midpoint.
 */
export const divergingColor = (value: number | null | undefined, stops: string[], min = 0, max = 100) => {
    if (value == null || !Number.isFinite(value) || stops.length === 0) return null;
    if (stops.length === 1) return stops[0];
    const position = Math.min(Math.max((value - min) / (max - min || 1), 0), 1) * (stops.length - 1);
    const index = Math.min(Math.floor(position), stops.length - 2);
    const share = position - index;
    const from = hexToRgb(stops[index]);
    const to = hexToRgb(stops[index + 1]);
    return `#${from.map((channel, offset) => Math.round(channel + (to[offset] - channel) * share).toString(16).padStart(2, "0")).join("")}`;
};

const MONO_FALLBACK = "ui-monospace, SFMono-Regular, Menlo, monospace";

/**
 * Canvas renderers cannot resolve CSS variables, and next/font registers Geist Mono under a
 * hashed family name, so read the resolved family from the root element at render time.
 */
export const chartMonoFont = () => {
    if (typeof document === "undefined") return MONO_FALLBACK;
    const family = getComputedStyle(document.documentElement).getPropertyValue("--font-geist-mono").trim();
    return family ? `${family}, ${MONO_FALLBACK}` : MONO_FALLBACK;
};
