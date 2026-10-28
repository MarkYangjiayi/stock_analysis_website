import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeThemeMember } from "@/test/themesFixture";
import ThemeMembersTable from "./ThemeMembersTable";

const members = [
    makeThemeMember({ ticker: "NVDA.US", market_cap: 4.7e12, excess_5d: 0.01, excess_21d: 0.10 }),
    makeThemeMember({ ticker: "SMCI.US", market_cap: null, excess_5d: null, excess_21d: -0.05, is_leader: false, leader_rank: null }),
    makeThemeMember({ ticker: "AMD.US", market_cap: 3.9e11, excess_5d: 0.08, excess_21d: 0.02, leader_rank: 2 }),
];

const tickerOrder = () => within(screen.getByRole("table", { name: "Theme members" }))
    .getAllByRole("link")
    .map((link) => link.textContent);

describe("ThemeMembersTable", () => {
    it("formats market cap and keeps the leader order until a column is sorted", () => {
        render(<ThemeMembersTable members={members} />);

        expect(tickerOrder()).toEqual(["NVDA", "SMCI", "AMD"]);
        expect(screen.getByText("$4.7T")).toBeInTheDocument();
        expect(screen.getByText("$390B")).toBeInTheDocument();
    });

    it("cycles market cap, 5D and 21D sorts through desc, asc and off with missing values last", () => {
        render(<ThemeMembersTable members={members} />);
        const marketCap = screen.getByRole("button", { name: /Mkt cap/ });

        fireEvent.click(marketCap);
        expect(tickerOrder()).toEqual(["NVDA", "AMD", "SMCI"]);
        expect(marketCap.closest("th")).toHaveAttribute("aria-sort", "descending");

        fireEvent.click(marketCap);
        expect(tickerOrder()).toEqual(["AMD", "NVDA", "SMCI"]);
        expect(marketCap.closest("th")).toHaveAttribute("aria-sort", "ascending");

        fireEvent.click(marketCap);
        expect(tickerOrder()).toEqual(["NVDA", "SMCI", "AMD"]);
        expect(marketCap.closest("th")).toHaveAttribute("aria-sort", "none");

        fireEvent.click(screen.getByRole("button", { name: /^5D/ }));
        expect(tickerOrder()).toEqual(["AMD", "NVDA", "SMCI"]);

        fireEvent.click(screen.getByRole("button", { name: /^21D/ }));
        expect(tickerOrder()).toEqual(["NVDA", "AMD", "SMCI"]);
        fireEvent.click(screen.getByRole("button", { name: /^21D/ }));
        expect(tickerOrder()).toEqual(["SMCI", "AMD", "NVDA"]);
    });
});
