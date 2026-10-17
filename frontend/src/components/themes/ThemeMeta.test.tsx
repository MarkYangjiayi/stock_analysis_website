import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThemeMetaNotices } from "./ThemeMeta";
import { makeThemeMeta } from "@/test/themesFixture";

describe("ThemeMetaNotices", () => {
    it("does not repeat the pipeline's attention warning under the degraded-attention notice", () => {
        render(<ThemeMetaNotices meta={makeThemeMeta({
            attention_degraded: true,
            warnings: [
                "Attention degraded on 2026-10-09; heat for 2 themes uses price, breadth and volume only",
                "No screener universe for 2026-10-09; trending companies are empty",
            ],
        })} />);

        expect(screen.getByText(/News attention is incomplete/)).toBeInTheDocument();
        expect(screen.queryByText(/Attention degraded on 2026-10-09/)).toBeNull();
        expect(screen.getByText(/No screener universe/)).toBeInTheDocument();
    });

    it("keeps every warning when attention is not degraded", () => {
        render(<ThemeMetaNotices meta={makeThemeMeta({
            attention_degraded: false,
            warnings: ["Attention degraded on 2026-09-30 for one backfilled date"],
        })} />);

        expect(screen.queryByText(/News attention is incomplete/)).toBeNull();
        expect(screen.getByText(/Attention degraded on 2026-09-30/)).toBeInTheDocument();
    });
});
