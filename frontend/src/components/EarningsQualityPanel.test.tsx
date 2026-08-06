import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import EarningsQualityPanel from "@/components/EarningsQualityPanel";
import type { EarningsQualityPeriod, EarningsQualityResponse } from "@/lib/api";

const period = (verified = false): EarningsQualityPeriod => ({
    period_end: "2025-12-31",
    period_type: "quarterly",
    reported: {
        revenue: 1_000,
        net_income: 100,
        income_before_tax: 125,
        net_income_from_continuing_operations: 100,
        income_tax_expense: 25,
        non_recurring: -25,
        extraordinary_items: 0,
        discontinued_operations: 0,
        non_operating_income_net_other: 0,
    },
    materiality_base: 125,
    thresholds: { warning: 0.1, high: 0.25 },
    flags: [{
        category: "non_recurring",
        label: "Vendor-labelled non-recurring item",
        amount: -25,
        materiality_ratio: 0.2,
        severity: "warning",
        source: "structured_financial_data",
        source_field: "nonRecurring",
        detail: "Candidate only.",
        treatment: "flag_only",
        recurring_adjustment: false,
    }],
    data_quality_warnings: [],
    assessment: "material_candidates",
    statement_fingerprint: "abc",
    analysis: verified ? {
        id: 1,
        ticker: "AAA.US",
        period_end: "2025-12-31",
        period_type: "quarterly",
        status: "completed",
        stage: "completed",
        model: "deepseek",
        prompt_version: "v1",
        source_accession: "0001",
        source_snapshots: [{
            source_id: "0001:primary",
            accession: "0001",
            form: "10-Q",
            document_name: "report.htm",
            source_url: "https://www.sec.gov/report.htm",
            html_snapshot_id: 1,
            text_snapshot_id: 2,
            html_checksum: "a",
            text_checksum: "b",
        }],
        result: {
            verification_status: "verified",
            reported_net_income: 100,
            normalized_net_income: 120,
            adjusted_eps: 1.2,
            company_adjusted: null,
            adjustments: [],
            notes: [],
        },
        validation_report: {
            verified: true,
            checks: [],
            failures: [],
            sign_convention: "normalized",
            gains_and_charges_treated_symmetrically: true,
        },
        error_message: null,
        retryable: false,
        created_at: "2026-01-01T00:00:00",
        started_at: "2026-01-01T00:00:01",
        finished_at: "2026-01-01T00:00:02",
    } : null,
    verified_normalized: verified ? { net_income: 120, adjusted_eps: 1.2 } : null,
});

const response = (verified = false): EarningsQualityResponse => ({
    ticker: "AAA.US",
    currency: "USD",
    methodology: {
        materiality_base: "max(abs(income before tax), 1% of abs(revenue))",
        warning_threshold: 0.1,
        high_threshold: 0.25,
        reported_remains_primary: true,
        structured_flags_are_adjustments: false,
    },
    summary: {
        verdict: "flags_present",
        evaluated_periods: 1,
        flagged_periods: 1,
        data_quality_periods: 0,
        financial_industry_exemption: false,
        message: "Screening signal only.",
    },
    annual: [],
    quarterly: [period(verified)],
    sec_analysis: {
        supported: true,
        cik: "1",
        reason: null,
        supported_forms: ["10-Q"],
        unsupported_forms: ["20-F", "6-K"],
    },
});

describe("EarningsQualityPanel", () => {
    it("does not request AI until the user clicks and keeps locked users gated", async () => {
        const user = userEvent.setup();
        const onAnalyze = vi.fn();
        const onUnlock = vi.fn();
        const { rerender } = render(<EarningsQualityPanel data={response()} onAnalyze={onAnalyze} onUnlock={onUnlock} />);

        expect(onAnalyze).not.toHaveBeenCalled();
        await user.click(screen.getByRole("button", { name: /2025-12-31/i }));
        await user.click(screen.getByRole("button", { name: /unlock to analyze/i }));
        expect(onUnlock).toHaveBeenCalledOnce();
        expect(onAnalyze).not.toHaveBeenCalled();

        rerender(<EarningsQualityPanel data={response()} adminKey="secret" onAnalyze={onAnalyze} onUnlock={onUnlock} />);
        await user.click(screen.getByRole("button", { name: /analyze filing/i }));
        expect(onAnalyze).toHaveBeenCalledOnce();
        expect(onAnalyze.mock.calls[0][0].period_end).toBe("2025-12-31");
    });

    it("shows adjusted NI and EPS only for a verified cached result", async () => {
        const user = userEvent.setup();
        render(<EarningsQualityPanel data={response(true)} adminKey="secret" />);

        await user.click(screen.getByRole("button", { name: /2025-12-31/i }));
        expect(screen.getByText("Verified adjusted")).toBeInTheDocument();
        expect(screen.getAllByText("120").length).toBeGreaterThan(0);
        expect(screen.getByText("1.20")).toBeInTheDocument();
        expect(screen.getByRole("link", { name: /10-Q source/i })).toHaveAttribute("href", "https://www.sec.gov/report.htm");
    });

    it("disables repeat submission while the selected period is being queued", async () => {
        const user = userEvent.setup();
        const onAnalyze = vi.fn();
        render(<EarningsQualityPanel data={response()} adminKey="secret" busyPeriod="quarterly:2025-12-31" onAnalyze={onAnalyze} />);

        await user.click(screen.getByRole("button", { name: /2025-12-31/i }));
        const analyzeButton = screen.getByRole("button", { name: /analyze filing/i });
        expect(analyzeButton).toBeDisabled();
        await user.click(analyzeButton);
        expect(onAnalyze).not.toHaveBeenCalled();
    });

    it("keeps verified net income visible when only adjusted EPS fails validation", async () => {
        const user = userEvent.setup();
        const data = response(true);
        const analysis = data.quarterly[0].analysis;
        if (!analysis?.result || !analysis.validation_report || !data.quarterly[0].verified_normalized) throw new Error("invalid fixture");
        analysis.result.adjusted_eps = null;
        analysis.validation_report.eps_verified = false;
        analysis.validation_report.eps_failures = [{ code: "adjusted_eps_unverified", message: "Adjusted diluted EPS is not source-reconciled." }];
        data.quarterly[0].verified_normalized.adjusted_eps = null;

        render(<EarningsQualityPanel data={data} adminKey="secret" />);
        await user.click(screen.getByRole("button", { name: /2025-12-31/i }));

        expect(screen.getAllByText("120").length).toBeGreaterThan(0);
        expect(screen.getByText("Why adjusted EPS is withheld")).toBeInTheDocument();
        expect(screen.getByText("Adjusted diluted EPS is not source-reconciled.")).toBeInTheDocument();
    });

    it("shows rejected filing candidates as amount-free red flags", async () => {
        const user = userEvent.setup();
        const data = response(true);
        const analysis = data.quarterly[0].analysis;
        if (!analysis?.result || !analysis.validation_report) throw new Error("invalid fixture");
        analysis.result.verification_status = "flag_only";
        analysis.result.normalized_net_income = null;
        analysis.result.adjustments = [];
        analysis.validation_report.verified = false;
        analysis.validation_report.failures = [{
            code: "citation_period_mismatch",
            message: "The cited amount is not bound to the selected quarter or year.",
            adjustment_index: 0,
        }];
        analysis.validation_report.rejected_adjustments = [{
            adjustment_index: 0,
            label: "Net gain on equity securities, net",
            model_category: "other",
            policy_category: "investment_fair_value",
            failure_codes: ["citation_period_mismatch", "cited_amount_mismatch"],
        }];
        data.quarterly[0].verified_normalized = null;

        render(<EarningsQualityPanel data={data} adminKey="secret" />);

        expect(screen.getByText("Filing candidates flagged")).toBeInTheDocument();
        expect(screen.getByText("1 filing flag")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: /2025-12-31/i }));

        expect(screen.getByText("Unverified filing candidates")).toBeInTheDocument();
        expect(screen.getByText("Net gain on equity securities, net")).toBeInTheDocument();
        expect(screen.getByText(/amounts are hidden/i)).toBeInTheDocument();
        expect(screen.getByText(/investment fair value · flag only/i)).toBeInTheDocument();
        expect(screen.getByText(/citation_period_mismatch · cited_amount_mismatch/i)).toBeInTheDocument();
        expect(screen.getByText("No source-validated adjustment amount is available.")).toBeInTheDocument();
    });

    it("counts and displays unquantified filing candidates", async () => {
        const user = userEvent.setup();
        const data = response(true);
        const analysis = data.quarterly[0].analysis;
        if (!analysis?.result || !analysis.validation_report) throw new Error("invalid fixture");
        analysis.result.verification_status = "flag_only";
        analysis.result.normalized_net_income = null;
        analysis.result.adjustments = [];
        analysis.result.unquantified_candidates = [{
            label: "Pending divestiture with no recognized impairment",
            model_category: "asset_or_business_disposal",
            policy_category: "asset_or_business_disposal",
            failure_codes: ["unquantified_candidate"],
        }];
        analysis.validation_report.verified = false;
        data.quarterly[0].verified_normalized = null;

        render(<EarningsQualityPanel data={data} adminKey="secret" />);

        expect(screen.getByText("Filing candidates flagged")).toBeInTheDocument();
        expect(screen.getByText("1 filing flag")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: /2025-12-31/i }));
        expect(screen.getByText("Unquantified filing candidates")).toBeInTheDocument();
        expect(screen.getByText("Pending divestiture with no recognized impairment")).toBeInTheDocument();
        expect(screen.getByText(/amount unavailable/i)).toBeInTheDocument();
    });

    it("shows a retryable waiting state when the matching SEC filing is not filed", async () => {
        const user = userEvent.setup();
        const onAnalyze = vi.fn();
        const data = response(true);
        const analysis = data.quarterly[0].analysis;
        if (!analysis) throw new Error("invalid fixture");
        analysis.status = "waiting_for_filing";
        analysis.stage = "waiting_for_filing";
        analysis.result = null;
        analysis.validation_report = null;
        analysis.error_message = "The matching 10-Q has not been filed for this reporting period yet.";
        analysis.retryable = true;
        data.quarterly[0].verified_normalized = null;

        render(<EarningsQualityPanel data={data} adminKey="secret" onAnalyze={onAnalyze} />);
        await user.click(screen.getByRole("button", { name: /2025-12-31/i }));

        expect(screen.getByText("SEC filing not available yet")).toBeInTheDocument();
        expect(screen.queryByText("Analysis failed")).not.toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: /check for filing/i }));
        expect(onAnalyze).toHaveBeenCalledOnce();
    });
});
