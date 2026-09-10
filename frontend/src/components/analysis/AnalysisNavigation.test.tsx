import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";

import AnalysisNavigation from "./AnalysisNavigation";

describe("AnalysisNavigation", () => {
    it("exposes one accessible research tablist and changes sections", () => {
        const onChange = vi.fn();
        render(<AnalysisNavigation active="overview" onChange={onChange} />);

        expect(screen.getAllByRole("tab")).toHaveLength(5);
        expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
        fireEvent.click(screen.getByRole("tab", { name: "Financials" }));
        expect(onChange).toHaveBeenCalledWith("financials");
    });

    it("supports arrow, home and end keyboard navigation", () => {
        const onChange = vi.fn();
        render(<AnalysisNavigation active="overview" onChange={onChange} />);

        fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), { key: "ArrowLeft" });
        expect(onChange).toHaveBeenLastCalledWith("events");
        fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), { key: "End" });
        expect(onChange).toHaveBeenLastCalledWith("events");
    });

    it("moves focus with selection, including wrapping and home/end", () => {
        function Workspace() {
            const [active, setActive] = useState<"overview" | "valuation" | "financials" | "technical" | "events">("overview");
            return <AnalysisNavigation active={active} onChange={setActive} />;
        }
        render(<Workspace />);
        screen.getByRole("tab", { name: "Overview" }).focus();
        for (const [key, name] of [["ArrowRight", "Valuation"], ["End", "Events & Brief"], ["ArrowRight", "Overview"], ["ArrowLeft", "Events & Brief"], ["Home", "Overview"]]) {
            fireEvent.keyDown(document.activeElement!, { key });
            const selected = screen.getByRole("tab", { name });
            expect(selected).toHaveFocus();
            expect(selected).toHaveAttribute("aria-selected", "true");
            expect(selected).toHaveAttribute("tabindex", "0");
        }
    });
});
