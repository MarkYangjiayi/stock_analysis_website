import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ThemeBriefCard from "./ThemeBriefCard";
import type { ThemeBriefResponse } from "@/lib/api";

const brief = (content: string, overrides: Partial<ThemeBriefResponse> = {}): ThemeBriefResponse => ({
    date: "2026-10-09",
    model: "deepseek-flash",
    prompt_version: "theme-brief-v1",
    created_at: "2026-10-10T09:05:00",
    stale: false,
    content,
    evidence: [
        { id: "E1", kind: "theme_metric", label: "x21 vs SPY +12.40%", source_date: "2026-10-09", link: null },
        { id: "E2", kind: "news", label: "Micron raises guidance", source_date: "2026-10-08", link: "https://example.com/mu" },
    ],
    ...overrides,
});

describe("ThemeBriefCard", () => {
    it("links citations to the evidence list and links sources only from validated evidence", () => {
        render(<ThemeBriefCard brief={brief("**盘面事实**\n\n- 主题 x21 为 +12.40% [E1]。\n- 同期新闻包括美光上调指引 [E2]。")} />);

        const citation = screen.getByRole("link", { name: "E1" });
        expect(citation).toHaveAttribute("href", "#theme-evidence-E1");
        expect(screen.getByRole("link", { name: "Open source for E2" })).toHaveAttribute("href", "https://example.com/mu");
        expect(document.getElementById("theme-evidence-E2")).not.toBeNull();
    });

    it("renders links and bare URLs inside model text as plain text", () => {
        render(<ThemeBriefCard brief={brief(
            "- 详情见 [这里](https://evil.example/phish) 与 https://evil.example/bare [E1]。\n- ![x](https://evil.example/pixel.png) [E2]",
        )} />);

        const hrefs = screen.getAllByRole("link").map((link) => link.getAttribute("href"));
        expect(hrefs.some((href) => href?.includes("evil.example"))).toBe(false);
        expect(screen.getByText(/这里/)).toBeInTheDocument();
        expect(document.querySelector("img")).toBeNull();
    });

    it("lists cited evidence first and collapses the rest", () => {
        const value = brief("- 主题 x21 为 +12.40% [E1]。", {
            evidence: [
                { id: "E1", kind: "theme_metric", label: "x21 vs SPY +12.40%", source_date: "2026-10-09", link: null },
                { id: "E2", kind: "theme_metric", label: "B50 73.2%", source_date: "2026-10-09", link: null },
                { id: "E3", kind: "news", label: "Unused headline", source_date: "2026-10-08", link: null },
            ],
        });
        render(<ThemeBriefCard brief={value} />);

        const summary = screen.getByText("Other evidence the brief could cite (2)");
        const details = summary.closest("details");
        expect(details).not.toBeNull();
        expect(details).not.toHaveAttribute("open");
        expect(details?.textContent).toContain("Unused headline");
        expect(document.getElementById("theme-evidence-E1")?.closest("details")).toBeNull();
    });

    it("labels a template brief that stands in for a failed AI brief", () => {
        render(<ThemeBriefCard brief={brief("- 题材热度 heat_now：60.0。[E1]", { model: "template", prompt_version: "theme-brief-template-v1" })} />);
        expect(screen.getByText("Template brief · Chinese")).toBeInTheDocument();
        expect(screen.getByText(/did not pass validation/)).toBeInTheDocument();
    });

    it("labels a stale brief and shows the empty state without a brief", () => {
        const { rerender } = render(<ThemeBriefCard brief={brief("- 示例 [E1]", { stale: true, date: "2026-10-07" })} />);
        expect(screen.getByText(/Stale · brief from 2026-10-07/)).toBeInTheDocument();

        rerender(<ThemeBriefCard brief={null} />);
        expect(screen.getByText("No validated brief for this theme yet")).toBeInTheDocument();
    });
});
