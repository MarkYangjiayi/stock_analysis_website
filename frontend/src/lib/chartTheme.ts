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
});

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
