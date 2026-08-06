import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AIReport from "@/components/AIReport";

describe("AIReport", () => {
    afterEach(() => vi.unstubAllGlobals());

    it("clears a generated brief when the decision evidence changes", async () => {
        const encoded = new TextEncoder().encode("Generated evidence view [E1].");
        const read = vi.fn()
            .mockResolvedValueOnce({ value: encoded, done: false })
            .mockResolvedValueOnce({ value: undefined, done: true });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
            ok: true,
            body: { getReader: () => ({ read }) },
        }));
        const user = userEvent.setup();
        const { rerender } = render(<AIReport ticker="AAA.US" evidenceKey="evidence-v1" />);

        await user.click(screen.getByRole("button", { name: "Generate brief" }));
        await screen.findByText("Generated evidence view [E1].");

        rerender(<AIReport ticker="AAA.US" evidenceKey="evidence-v2" />);
        await waitFor(() => expect(screen.queryByText("Generated evidence view [E1].")).not.toBeInTheDocument());
        expect(screen.getByRole("button", { name: "Generate brief" })).toBeInTheDocument();
    });

    it("clears and disables a brief when its valuation evidence is out of sync", async () => {
        const encoded = new TextEncoder().encode("Generated evidence view [E1].");
        const read = vi.fn()
            .mockResolvedValueOnce({ value: encoded, done: false })
            .mockResolvedValueOnce({ value: undefined, done: true });
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            body: { getReader: () => ({ read }) },
        });
        vi.stubGlobal("fetch", fetchMock);
        const user = userEvent.setup();
        const { rerender } = render(
            <AIReport ticker="AAA.US" evidenceKey="server-evidence" />,
        );

        await user.click(screen.getByRole("button", { name: "Generate brief" }));
        await screen.findByText("Generated evidence view [E1].");
        rerender(
            <AIReport
                ticker="AAA.US"
                evidenceKey="working-evidence"
                disabledReason="Save or reset the working scenarios first."
            />,
        );

        await waitFor(() => {
            expect(screen.queryByText("Generated evidence view [E1].")).not.toBeInTheDocument();
        });
        expect(screen.getByRole("status")).toHaveTextContent("Brief paused");
        expect(screen.queryByRole("button", { name: "Generate brief" })).not.toBeInTheDocument();
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });
});
