"use client";

import { useSyncExternalStore } from "react";
import { useStatusBar, type StatusTone } from "@/store/useStatusBar";

const TONE_CLASS: Record<StatusTone, string> = {
    ready: "bg-up",
    stale: "bg-caution",
    error: "bg-danger",
    neutral: "bg-flat",
};

const NY_TIME = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
});

const subscribeToClock = (onChange: () => void) => {
    const timer = window.setInterval(onChange, 15_000);
    return () => window.clearInterval(timer);
};
const readClock = () => NY_TIME.format(new Date());
const readServerClock = () => "--:--";

function NewYorkClock() {
    const time = useSyncExternalStore(subscribeToClock, readClock, readServerClock);
    return <span className="ml-auto shrink-0 pl-4" aria-label="New York time">NY {time} ET</span>;
}

export default function StatusBar() {
    const { items, note } = useStatusBar();

    return (
        <footer className="status-bar" aria-label="Data status">
            {items.length === 0 && <span>Quantify · point-in-time research</span>}
            {items.map((item) => (
                <span key={item.label} className="inline-flex shrink-0 items-center gap-1.5">
                    <span className={`h-1.5 w-1.5 rounded-full ${TONE_CLASS[item.tone ?? "neutral"]}`} aria-hidden="true" />
                    <span>{item.label}</span>
                    <span className="text-fg">{item.value}</span>
                </span>
            ))}
            {note && <span className="hidden shrink-0 lg:inline">{note}</span>}
            <NewYorkClock />
        </footer>
    );
}
