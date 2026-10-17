import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeInsufficientTheme, makeThemeListItem } from "@/test/themesFixture";
import ThemeLeaderboard, { ComponentBars, HeatBar } from "./ThemeLeaderboard";

describe("ThemeLeaderboard", () => {
    it("ranks by the selected window and flags attention-only themes", () => {
        const theme = makeThemeListItem({ attention_only: true, stage_changed: true });
        const { rerender } = render(<ThemeLeaderboard themes={[theme]} view="now" />);

        const row = screen.getByRole("link", { name: "AI compute" }).closest("tr")!;
        expect(within(row).getAllByRole("cell")[0]).toHaveTextContent("1↑2");
        expect(within(row).getByText("78.4")).toBeInTheDocument();
        expect(within(row).getByText("Attention only")).toBeInTheDocument();
        expect(within(row).getByText("· new")).toBeInTheDocument();
        expect(within(row).getByText("14")).toBeInTheDocument();

        rerender(<ThemeLeaderboard themes={[theme]} view="trend" />);
        expect(screen.getByRole("columnheader", { name: "Heat 20D" })).toBeInTheDocument();
        expect(within(screen.getByRole("link", { name: "AI compute" }).closest("tr")!).getAllByRole("cell")[0]).toHaveTextContent("2↑2");
        expect(screen.getByText("71.9")).toBeInTheDocument();
    });

    it("shows skeleton rows while loading and an explicit empty state", () => {
        const { container, rerender } = render(<ThemeLeaderboard themes={[]} view="now" loading />);
        expect(container.querySelectorAll("tbody tr")).toHaveLength(6);
        expect(container.querySelector("[aria-busy=true]")).toBeInTheDocument();
        // The scroller is the containing block for absolutely positioned cell content, so it clips with the table.
        expect(container.firstElementChild).toHaveClass("relative", "overflow-x-auto");

        rerender(<ThemeLeaderboard themes={[]} view="now" />);
        expect(screen.getByText("No themes match this filter.")).toBeInTheDocument();
    });
});

describe("ComponentBars", () => {
    it("draws zero-centred bars and leaves missing components blank", () => {
        const { container } = render(<ComponentBars theme={{ score_price: 1.5, score_breadth: -3.6, score_volume: 0, score_attention: null }} />);

        const summary = screen.getByText("Price +1.50, Breadth −3.60, Volume 0.00, Attention —");
        expect(summary).toHaveClass("sr-only");
        // sr-only is absolutely positioned: without a positioned parent it escapes the table scroller and widens phone pages.
        expect(summary.parentElement).toHaveClass("relative");
        const fill = (label: string) => container.querySelector(`[data-component="${label}"]`)?.children[1] as HTMLElement | undefined;
        expect(fill("Price")).toHaveClass("bg-up");
        expect(fill("Price")?.style.height).toBe("25%");
        expect(fill("Price")?.style.bottom).toBe("50%");
        expect(fill("Breadth")).toHaveClass("bg-down");
        expect(fill("Breadth")?.style.height).toBe("50%");
        expect(fill("Breadth")?.style.top).toBe("50%");
        expect(fill("Volume")).toBeUndefined();
        expect(fill("Attention")).toBeUndefined();
    });
});

describe("HeatBar", () => {
    it("clamps the bar to 0–100 and prints a dash for missing heat", () => {
        const { container, rerender } = render(<HeatBar value={124} />);
        expect((container.querySelector(".bg-accent") as HTMLElement).style.width).toBe("100%");
        expect(screen.getByText("124.0")).toBeInTheDocument();

        rerender(<HeatBar value={makeInsufficientTheme().heat_now} />);
        expect((container.querySelector(".bg-accent") as HTMLElement).style.width).toBe("0%");
        expect(screen.getByText("—")).toBeInTheDocument();
    });
});
