import { AlertCircle, CheckCircle2, Clock3, LoaderCircle } from "lucide-react";

export type DataStateTone = "ready" | "loading" | "stale" | "error" | "neutral";

const ICONS = {
    ready: CheckCircle2,
    loading: LoaderCircle,
    stale: Clock3,
    error: AlertCircle,
    neutral: Clock3,
};

export function DataState({ label, tone = "neutral" }: { label: string; tone?: DataStateTone }) {
    const Icon = ICONS[tone];
    return (
        <span className="data-state" data-tone={tone}>
            <Icon size={13} className={tone === "loading" ? "animate-spin" : ""} aria-hidden="true" />
            {label}
        </span>
    );
}
