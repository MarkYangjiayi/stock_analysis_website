import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ThesisVersion } from "@/lib/api";
import ThesisPanel from "./ThesisPanel";

const apiMocks = vi.hoisted(() => ({
    fetchPersonalThesis: vi.fn(),
    savePersonalThesis: vi.fn(),
    fetchPersonalThesisVersions: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@/lib/api")>()),
    ...apiMocks,
}));

const thesis = (version: number, reasons = ["Fintech take rate keeps rising"]): ThesisVersion => ({
    version,
    note: version === 1 ? "First draft" : null,
    created_at: "2026-10-31T06:00:00+00:00",
    content: {
        reasons,
        metrics: [{ name: "Revenue growth", threshold: "< 20% YoY for two quarters" }],
        conditions: [{ kind: "sell", text: "Credit losses above 8% of the loan book" }],
        valuation: { bear: 1500, base: 2100, bull: 2800, note: "DCF, November 2026" },
        catalysts: [{ date: "2026-11-05", event: "Q3 earnings" }],
    },
});

const renderPanel = (overrides: Partial<Parameters<typeof ThesisPanel>[0]> = {}) => {
    const props = {
        ticker: "MELI.US",
        currency: "USD",
        price: 1850,
        unlocked: true,
        inWatchlist: true,
        isCore: true,
        onUnlock: vi.fn(),
        onUnauthorized: vi.fn(),
        onToggleCore: vi.fn(),
        ...overrides,
    };
    render(<ThesisPanel {...props} />);
    return props;
};

describe("ThesisPanel", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.fetchPersonalThesis.mockResolvedValue({ ticker: "MELI.US", thesis: null });
    });

    it("asks to unlock instead of loading a personal thesis", () => {
        const props = renderPanel({ unlocked: false });
        fireEvent.click(screen.getByRole("button", { name: /Unlock/ }));
        expect(props.onUnlock).toHaveBeenCalled();
        expect(apiMocks.fetchPersonalThesis).not.toHaveBeenCalled();
    });

    it("locks the workspace when the session is rejected", async () => {
        apiMocks.fetchPersonalThesis.mockRejectedValue(new ApiError("Invalid API key", 401));
        const props = renderPanel();
        await waitFor(() => expect(props.onUnauthorized).toHaveBeenCalled());
    });

    it("writes a first version and drops blank rows", async () => {
        apiMocks.savePersonalThesis.mockResolvedValue({ ticker: "MELI.US", thesis: thesis(1), saved: true });
        renderPanel();
        fireEvent.click(await screen.findByRole("button", { name: /Write thesis/ }));
        fireEvent.change(screen.getByLabelText("Reason 1"), { target: { value: "  Fintech take rate keeps rising " } });
        fireEvent.click(screen.getByRole("button", { name: "Save first version" }));

        await waitFor(() => expect(apiMocks.savePersonalThesis).toHaveBeenCalledWith(
            "MELI.US",
            {
                reasons: ["Fintech take rate keeps rising"],
                metrics: [],
                conditions: [],
                valuation: { bear: null, base: null, bull: null, note: "" },
                catalysts: [],
            },
            0,
            "",
        ));
        expect(await screen.findByText("Fintech take rate keeps rising")).toBeInTheDocument();
        expect(screen.getByText(/Version 1 · saved .* · First draft/)).toBeInTheDocument();
    });

    it("shows the thesis against the price and saves an edit from its version", async () => {
        apiMocks.fetchPersonalThesis.mockResolvedValue({ ticker: "MELI.US", thesis: thesis(2) });
        apiMocks.savePersonalThesis.mockResolvedValue({ ticker: "MELI.US", thesis: thesis(3, ["Margin expansion"]), saved: true });
        renderPanel();
        expect(await screen.findByText("Credit losses above 8% of the loan book")).toBeInTheDocument();
        expect(screen.getByText("Price $1,850.00 is below the base value.")).toBeInTheDocument();
        expect(screen.getByText("$2,800.00")).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: /Edit/ }));
        fireEvent.change(screen.getByLabelText("Reason 1"), { target: { value: "Margin expansion" } });
        fireEvent.change(screen.getByLabelText(/What changed/), { target: { value: "Q3 review" } });
        fireEvent.click(screen.getByRole("button", { name: "Save as version 3" }));

        await waitFor(() => expect(apiMocks.savePersonalThesis).toHaveBeenCalledTimes(1));
        const [, content, baseVersion, note] = apiMocks.savePersonalThesis.mock.calls[0];
        expect(content.reasons).toEqual(["Margin expansion"]);
        expect(content.metrics).toEqual([{ name: "Revenue growth", threshold: "< 20% YoY for two quarters" }]);
        expect(baseVersion).toBe(2);
        expect(note).toBe("Q3 review");
        expect(await screen.findByText("Margin expansion")).toBeInTheDocument();
    });

    it("refuses to overwrite a thesis saved elsewhere and reloads it on request", async () => {
        apiMocks.fetchPersonalThesis.mockResolvedValue({ ticker: "MELI.US", thesis: thesis(2) });
        apiMocks.savePersonalThesis.mockRejectedValue(new ApiError("The thesis was saved elsewhere and is now at version 3. Reload it before saving.", 409));
        renderPanel();
        fireEvent.click(await screen.findByRole("button", { name: /Edit/ }));
        fireEvent.click(screen.getByRole("button", { name: "Save as version 3" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("now at version 3");

        apiMocks.fetchPersonalThesis.mockResolvedValue({ ticker: "MELI.US", thesis: thesis(3, ["Saved from another tab"]) });
        fireEvent.click(screen.getByRole("button", { name: "Reload latest" }));
        expect(await screen.findByText("Saved from another tab")).toBeInTheDocument();
        expect(apiMocks.fetchPersonalThesis).toHaveBeenCalledTimes(2);
    });

    it("checks the valuation order and incomplete metrics before saving", async () => {
        renderPanel();
        fireEvent.click(await screen.findByRole("button", { name: /Write thesis/ }));
        fireEvent.change(screen.getByLabelText("bear"), { target: { value: "2500" } });
        fireEvent.change(screen.getByLabelText("base"), { target: { value: "2100" } });
        fireEvent.click(screen.getByRole("button", { name: "Save first version" }));
        expect(screen.getByRole("alert")).toHaveTextContent("bear ≤ base ≤ bull");

        fireEvent.change(screen.getByLabelText("bear"), { target: { value: "1500" } });
        fireEvent.change(screen.getByLabelText("Metric 1"), { target: { value: "Take rate" } });
        fireEvent.click(screen.getByRole("button", { name: "Save first version" }));
        expect(screen.getByRole("alert")).toHaveTextContent("needs a name and a threshold");
        expect(apiMocks.savePersonalThesis).not.toHaveBeenCalled();
    });

    it("lists earlier versions and opens one", async () => {
        apiMocks.fetchPersonalThesis.mockResolvedValue({ ticker: "MELI.US", thesis: thesis(2) });
        apiMocks.fetchPersonalThesisVersions.mockResolvedValue({ ticker: "MELI.US", versions: [thesis(2), thesis(1, ["The first reason"])] });
        renderPanel();
        const details = (await screen.findByText("History")).closest("details")!;
        details.open = true;
        fireEvent(details, new Event("toggle"));

        fireEvent.click(await screen.findByRole("button", { name: /v1 · .* · First draft/ }));
        expect(screen.getByText("The first reason")).toBeInTheDocument();
        expect(apiMocks.fetchPersonalThesisVersions).toHaveBeenCalledWith("MELI.US");
    });

    it("offers the core toggle only for listed tickers", async () => {
        const props = renderPanel({ isCore: false });
        fireEvent.click(await screen.findByRole("button", { name: /Mark as core/ }));
        expect(props.onToggleCore).toHaveBeenCalledWith(true);
    });

    it("explains how to make an unlisted ticker core", async () => {
        renderPanel({ inWatchlist: false, isCore: false });
        expect(await screen.findByText("Add to the watchlist to make it core")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Mark as core/ })).not.toBeInTheDocument();
    });
});
