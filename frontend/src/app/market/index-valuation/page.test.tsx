import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeIndexValuationFixture } from "@/test/indexValuationFixture";

const apiMocks = vi.hoisted(() => ({
    fetchIndexValuation: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    fetchIndexValuation: apiMocks.fetchIndexValuation,
}));

vi.mock("@/components/IndexValuationChart", () => ({
    default: () => <div data-testid="index-pe-chart" />,
}));

import IndexValuationPage from "./page";

describe("IndexValuationPage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.fetchIndexValuation.mockResolvedValue(makeIndexValuationFixture());
    });

    it("renders summary metrics, methodology and the valuation tab", async () => {
        render(<IndexValuationPage />);

        expect(await screen.findByTestId("index-pe-chart")).toBeInTheDocument();
        expect(screen.getByText("Latest index P/E")).toBeInTheDocument();
        expect(screen.getByText("28.4×")).toBeInTheDocument();
        expect(screen.getByText("Full-history median")).toBeInTheDocument();
        expect(screen.getByText("28.1×")).toBeInTheDocument();
        expect(screen.getByText("Valid months")).toBeInTheDocument();
        expect(screen.getByText("2 / 3")).toBeInTheDocument();
        expect(screen.getByRole("link", { name: "Valuation" })).toHaveAttribute("href", "/market/index-valuation");
        expect(screen.getByText(/Forward P\/E requires archived analyst expectations/)).toBeInTheDocument();
        expect(screen.getByText("Month-end 2026-09-30")).toBeInTheDocument();
        expect(screen.getByText(/split-only prices since 2016/)).toBeInTheDocument();
        expect(apiMocks.fetchIndexValuation).toHaveBeenCalledWith(expect.any(AbortSignal));
    });

    it("flags stale data and loss makers in the chart caption", async () => {
        const fixture = makeIndexValuationFixture();
        fixture.meta.stale = true;
        fixture.meta.warnings = [
            "IR.US: Complete split history has not been verified.",
            "WAB.US: Complete split history has not been verified.",
        ];
        apiMocks.fetchIndexValuation.mockResolvedValueOnce(fixture);
        render(<IndexValuationPage />);

        expect(await screen.findByText(/Data through 2026-09-17/)).toBeInTheDocument();
        expect(screen.getByText(/split history has not been verified/)).toBeInTheDocument();
        expect(screen.getByText(/25 loss-making companies/)).toBeInTheDocument();
    });

    it("shows provider errors with a retry path", async () => {
        apiMocks.fetchIndexValuation
            .mockRejectedValueOnce(new Error("Index valuation has not been published yet."))
            .mockResolvedValueOnce(makeIndexValuationFixture());
        render(<IndexValuationPage />);

        expect(await screen.findByRole("alert")).toHaveTextContent("not been published");
        fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
        expect(await screen.findByTestId("index-pe-chart")).toBeInTheDocument();
    });
});
