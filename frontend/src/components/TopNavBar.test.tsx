import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({ pathname: "/" }));

vi.mock("next/navigation", () => ({
    usePathname: () => navigation.pathname,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
}));

vi.mock("./shell/MarketStatusChip", () => ({ default: () => null }));
vi.mock("./ThemeToggle", () => ({ ThemeToggle: () => null }));

import TopNavBar from "./TopNavBar";

const activeLinks = () => within(screen.getByRole("navigation", { name: "Primary navigation" }))
    .getAllByRole("link")
    .filter((link) => link.getAttribute("aria-current") === "page")
    .map((link) => link.textContent);

describe("TopNavBar", () => {
    beforeEach(() => {
        navigation.pathname = "/";
    });

    it("lists Themes as a first-level section", () => {
        render(<TopNavBar />);

        expect(screen.getByRole("link", { name: "Themes" })).toHaveAttribute("href", "/themes");
    });

    it.each([
        ["/themes", ["Themes"]],
        ["/themes/ai-compute", ["Themes"]],
        ["/rrg", ["Market"]],
        ["/market/index-valuation", ["Market"]],
        ["/", ["Analysis"]],
        ["/themes-archive", []],
    ])("marks the active section for %s", (pathname, expected) => {
        navigation.pathname = pathname;
        render(<TopNavBar />);

        expect(activeLinks()).toEqual(expected);
    });
});
