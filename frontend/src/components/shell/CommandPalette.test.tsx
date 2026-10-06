import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import CommandPalette from "./CommandPalette";

const push = vi.fn();
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push }),
    usePathname: () => "/",
    useSearchParams: () => new URLSearchParams("ticker=NVDA.US&section=valuation"),
}));

beforeEach(() => {
    push.mockReset();
    const values = new Map([["my_watchlist", JSON.stringify(["AAPL.US", "MSFT.US"])]]);
    Object.defineProperty(window, "localStorage", {
        configurable: true,
        value: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value) },
    });
});

it("focuses immediately so keystrokes typed right after opening are kept", async () => {
    render(<CommandPalette onClose={() => undefined} />);
    expect(screen.getByRole("combobox")).toHaveFocus();
    await userEvent.keyboard("fin{Enter}");
    expect(push).toHaveBeenCalledWith("/?ticker=NVDA.US&section=financials");
});

it("keeps tickers first for short or non-matching queries", async () => {
    const onClose = vi.fn();
    render(<CommandPalette onClose={onClose} />);
    await userEvent.keyboard("nvda{Enter}");
    expect(push).toHaveBeenCalledWith("/?ticker=NVDA.US");
    expect(onClose).toHaveBeenCalled();
});
