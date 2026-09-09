"use client";

import type { KeyboardEvent } from "react";
import { BarChart3, Calculator, CalendarDays, ChartCandlestick, Landmark } from "lucide-react";

export type AnalysisSection = "overview" | "valuation" | "financials" | "technical" | "events";

const SECTIONS = [
    { value: "overview", label: "Overview", icon: BarChart3 },
    { value: "valuation", label: "Valuation", icon: Calculator },
    { value: "financials", label: "Financials", icon: Landmark },
    { value: "technical", label: "Price & Factors", icon: ChartCandlestick },
    { value: "events", label: "Events & Brief", icon: CalendarDays },
] satisfies Array<{ value: AnalysisSection; label: string; icon: typeof BarChart3 }>;

export const isAnalysisSection = (value: string | null): value is AnalysisSection =>
    SECTIONS.some((section) => section.value === value);

export default function AnalysisNavigation({
    active,
    onChange,
}: {
    active: AnalysisSection;
    onChange: (section: AnalysisSection) => void;
}) {
    const move = (event: KeyboardEvent<HTMLButtonElement>, direction: -1 | 1) => {
        event.preventDefault();
        const index = SECTIONS.findIndex((section) => section.value === active);
        onChange(SECTIONS[(index + direction + SECTIONS.length) % SECTIONS.length].value);
    };

    return (
        <nav className="analysis-navigation" aria-label="Security research sections">
            <div className="scrollbar-hide flex min-w-max items-stretch" role="tablist" aria-label="Security research sections">
                {SECTIONS.map((section) => {
                    const Icon = section.icon;
                    const selected = section.value === active;
                    return (
                        <button
                            key={section.value}
                            id={`analysis-tab-${section.value}`}
                            type="button"
                            role="tab"
                            aria-selected={selected}
                            aria-controls={`analysis-panel-${section.value}`}
                            tabIndex={selected ? 0 : -1}
                            onClick={() => onChange(section.value)}
                            onKeyDown={(event) => {
                                if (event.key === "ArrowLeft") move(event, -1);
                                if (event.key === "ArrowRight") move(event, 1);
                                if (event.key === "Home") { event.preventDefault(); onChange(SECTIONS[0].value); }
                                if (event.key === "End") { event.preventDefault(); onChange(SECTIONS.at(-1)!.value); }
                            }}
                            className="analysis-navigation-item"
                        >
                            <Icon size={16} aria-hidden="true" />
                            {section.label}
                        </button>
                    );
                })}
            </div>
        </nav>
    );
}
