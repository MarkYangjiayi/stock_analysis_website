/** Minimal inline trend line; inherits its colour from the surrounding text colour. */
export function Sparkline({ values, className = "", height = 18 }: { values: number[]; className?: string; height?: number }) {
    const finite = values.filter((value) => Number.isFinite(value));
    if (finite.length < 2) return <span className={`block ${className}`} style={{ height }} aria-hidden="true" />;
    const min = Math.min(...finite);
    const max = Math.max(...finite);
    const span = max - min || 1;
    const points = finite
        .map((value, index) => `${((index / (finite.length - 1)) * 100).toFixed(2)},${(((max - value) / span) * 90 + 5).toFixed(2)}`)
        .join(" ");
    return (
        <svg className={`block w-full ${className}`} height={height} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
    );
}
