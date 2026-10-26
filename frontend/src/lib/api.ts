export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
    status: number;
    detail?: string;

    constructor(message: string, status: number, detail?: string) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.detail = detail;
    }
}

const parseError = async (response: Response) => {
    const payload = await response.json().catch(() => ({})) as { detail?: unknown; message?: unknown };
    const fallback = `Request failed with status ${response.status}`;
    // FastAPI validation errors (422) carry a list of {loc, msg, type} objects.
    if (Array.isArray(payload.detail)) {
        const messages = payload.detail
            .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : ""))
            .filter(Boolean);
        return messages.length ? messages.join("; ") : fallback;
    }
    if (typeof payload.detail === "string" && payload.detail) return payload.detail;
    if (typeof payload.message === "string" && payload.message) return payload.message;
    return fallback;
};

export async function apiRequest<T>(
    path: string,
    init: RequestInit = {},
    timeoutMs = 30_000,
): Promise<T> {
    const controller = new AbortController();
    if (init.signal?.aborted) controller.abort();
    let timedOut = false;
    const timeout = globalThis.setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);
    const onAbort = () => controller.abort();
    init.signal?.addEventListener("abort", onAbort, { once: true });

    try {
        const headers = new Headers(init.headers);
        if (!headers.has("Accept")) headers.set("Accept", "application/json");
        const response = await fetch(`${API_BASE_URL}${path}`, {
            ...init,
            headers,
            signal: controller.signal,
        });
        if (!response.ok) {
            const message = await parseError(response);
            throw new ApiError(message, response.status, message);
        }
        return response.json() as Promise<T>;
    } catch (error) {
        if (timedOut) throw new ApiError(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds`, 408);
        throw error;
    } finally {
        globalThis.clearTimeout(timeout);
        init.signal?.removeEventListener("abort", onAbort);
    }
}

export interface StockProfile {
    ticker: string;
    name: string | null;
    exchange: string | null;
    sector: string | null;
    industry: string | null;
    description: string | null;
    currency: string | null;
    last_updated: string | null;
}

export interface HistoricalDataPoint {
    date: string;
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
    volume: number | null;
    MA20?: number | null;
    MA50?: number | null;
    RSI?: number | null;
    MACD?: number | null;
    MACD_Signal?: number | null;
    MACD_Hist?: number | null;
}

export interface ValuationMetrics {
    ttm: {
        revenue: number;
        gross_profit: number;
        net_income: number;
        free_cash_flow: number;
        roe: number;
    };
    balance_sheet_latest: {
        total_assets: number;
        total_liabilities: number;
        total_stockholder_equity: number;
        shares_outstanding: number;
    };
    valuation: {
        dcf_intrinsic_value_per_share: number | null;
        model_version: string;
        available: boolean;
        unavailable_reasons: string[];
        upside_downside: number | null;
        current_price: number | null;
        margin_of_safety: number | null;
        assumptions: {
            fcf_growth_rate_5yr: number;
            wacc: number;
            perpetual_growth: number;
        };
    };
    data_quality_warnings?: Array<{
        code: string;
        message: string;
        [key: string]: unknown;
    }>;
}

export interface HistoricalFinancialPoint {
    date: string;
    revenue: number;
    net_income: number;
    gross_margin: number;
    free_cash_flow?: number | null;
    operating_margin?: number | null;
    cash_and_short_term_investments?: number | null;
    total_debt?: number | null;
    stockholder_equity?: number | null;
    debt_to_equity?: number | null;
    shares_outstanding?: number | null;
    shares_reported?: number | null;
    share_adjustment_factor?: number | null;
    price?: number | null;
}

export interface StockDataResponse {
    profile: StockProfile;
    historical_data: HistoricalDataPoint[];
    historical_financials: HistoricalFinancialPoint[];
    valuation_metrics?: ValuationMetrics | null;
    valuation_history?: ValuationHistoryResponse | null;
}

export type MultipleKey = "pe" | "ps" | "pb" | "pfcf" | "ev_revenue" | "ev_ebitda";

export interface ValuationHistoryMetric {
    key: MultipleKey;
    label: string;
    description: string;
    formula: string;
    valid_points: number;
    total_points: number;
    latest_value: number | null;
    latest_date: string | null;
    latest_reason: string | null;
    median: number | null;
}

export interface ValuationHistoryPoint {
    date: string;
    price_date: string;
    basis_id: number | null;
    values: Record<MultipleKey, number | null>;
    reason: string | null;
    ev_reason: string | null;
}

export interface ValuationHistoryResponse {
    ticker: string;
    interval: "1d" | "1wk" | "1mo";
    currency: string | null;
    price_basis: "split_only";
    history_basis: "reconstructed_estimates";
    split_reference_date: string | null;
    methodology: string[];
    warnings: string[];
    metrics: ValuationHistoryMetric[];
    bases: Array<{
        id: number;
        period_end: string;
        available_from: string;
        periods: string[];
        source: string;
        raw_snapshot_ids: number[];
        share_reference_dates: string[];
        inputs: Record<string, number | null>;
        reasons: Partial<Record<MultipleKey, string>>;
    }>;
    points: ValuationHistoryPoint[];
}

export interface FinancialFlowSummaryCard {
    key: string;
    label: string;
    value: number | null;
    yoy_change: number | null;
    margin: number | null;
    note: string | null;
}

export interface FinancialFlowNode {
    id: string;
    label: string;
    value: number;
    kind: string;
    source_id: string;
    evidence_type: string;
    confidence: string;
    original_label: string | null;
}

export interface FinancialFlowLink {
    source: string;
    target: string;
    value: number;
    kind: string;
}

export interface FinancialFlowResponse {
    ticker: string;
    currency: string | null;
    period_type: "annual" | "quarterly";
    period_end: string | null;
    available_periods: string[];
    status: "ready" | "partial" | "unsupported" | "unavailable";
    coverage_level: "full" | "consolidated" | "none";
    unsupported_reason: string | null;
    chart_available: boolean;
    summary_cards: FinancialFlowSummaryCard[];
    nodes: FinancialFlowNode[];
    links: FinancialFlowLink[];
    insights: Array<{ code?: string; severity?: string; message?: string }>;
    validation: {
        reconciled?: boolean;
        missing_fields?: string[];
        warnings?: Array<{ code?: string; message?: string }>;
        [key: string]: unknown;
    };
    sources: Array<{
        source_id: string;
        document_type: string;
        filing_date: string | null;
        url: string | null;
    }>;
    enrichment: {
        status: string;
        run_id: number | null;
        last_error: string | null;
        updated_at: string | null;
    };
}

export type MarketSnapshotUnit =
    | "currency"
    | "integer"
    | "multiple"
    | "number"
    | "percent"
    | "ratio"
    | "date"
    | "text";

export interface MarketSnapshotMetric {
    value: number | string | string[] | null;
    unit: MarketSnapshotUnit;
    source_date: string | null;
    unavailable_reason: string | null;
    secondary_value: number | null;
    secondary_unit: "currency" | "percent" | "number" | null;
    percentile: number | null;
    percentile_scope: "industry" | "sector" | null;
}

export interface MarketSnapshotResponse {
    ticker: string;
    currency: string | null;
    source_dates: {
        price: string | null;
        screener: string | null;
        financials: string | null;
        provider: string | null;
    };
    coverage: {
        available: number;
        total: number;
        ratio: number;
    };
    metrics: Record<string, MarketSnapshotMetric>;
}

export interface StockEvent {
    id: string;
    kind: "earnings" | "dividend";
    status: "upcoming" | "reported";
    title: string;
    event_date: string;
    period_end?: string | null;
    payment_date?: string | null;
    timing?: string | null;
    eps_actual?: number | null;
    eps_estimate?: number | null;
    eps_difference?: number | null;
    eps_surprise_percent?: number | null;
}

export interface EarningsExpectation {
    period: string;
    label: string;
    period_end: string;
    eps_average?: number | null;
    eps_low?: number | null;
    eps_high?: number | null;
    eps_growth?: number | null;
    revenue_average?: number | null;
    revenue_low?: number | null;
    revenue_high?: number | null;
    revenue_growth?: number | null;
    eps_analyst_count?: number | null;
    revenue_analyst_count?: number | null;
    eps_revisions_up_7d?: number | null;
    eps_revisions_down_7d?: number | null;
    eps_revisions_up_30d?: number | null;
    eps_revisions_down_30d?: number | null;
    eps_trend_current?: number | null;
    eps_trend_7d?: number | null;
    eps_trend_30d?: number | null;
}

export interface EventsExpectationsResponse {
    ticker: string;
    source: string;
    as_of?: string | null;
    available: boolean;
    next_event?: StockEvent | null;
    upcoming_events: StockEvent[];
    recent_earnings: StockEvent[];
    expectations: EarningsExpectation[];
    wall_street_target_price?: number | null;
    dividend_yield?: number | null;
    annual_dividend_per_share?: number | null;
    /** Same block as decision-support, so the panel and Key risks agree. */
    expectation_signals?: ExpectationSignalsContext | null;
    data_quality_notes: string[];
}

export type ValuationScenarioName = "bear" | "base" | "bull";

export interface OperatingForecastYear {
    year: number;
    revenue: number;
    operating_margin: number;
    tax_rate: number;
    capex: number;
    depreciation: number;
    change_in_working_capital: number;
    source: string;
}

export interface DecisionValuationScenarioInput {
    operating_forecast?: OperatingForecastYear[];
    terminal_roic?: number;
    forecast_as_of?: string;
    scenario: ValuationScenarioName;
    fcf_growth_rate: number;
    wacc: number;
    perpetual_growth: number;
}

export interface DecisionValuationScenarioResult {
    scenario: ValuationScenarioName;
    assumptions: DecisionValuationScenarioInput;
    available: boolean;
    reasons?: string[];
    intrinsic_value_per_share?: number;
    enterprise_value?: number;
    equity_value?: number;
    projected_fcf?: number[];
    present_value_explicit_fcf?: number;
    present_value_terminal?: number;
    terminal_share_of_enterprise_value?: number | null;
    terminal_fcff?: number;
    projected_growth_rates?: Array<number | null>;
    upside_downside?: number | null;
}

export interface DecisionImpliedGrowth {
    available: boolean;
    implied_fcf_growth_rate: number | null;
    base_fcf_growth_rate: number;
    growth_gap_to_base: number | null;
    wacc: number;
    perpetual_growth: number;
    forecast_years: number;
    target_price: number | null;
    target_enterprise_value: number | null;
    modeled_price: number | null;
    status: "above_base" | "at_base" | "below_base" | "unavailable";
    reasons: string[];
}

export interface DecisionValuation {
    model_version?: string;
    available: boolean;
    unavailable_reasons: string[];
    inputs: {
        fcf: number | null;
        reported_fcf?: number | null;
        after_tax_interest_adjustment?: number | null;
        cash_flow_type?: "FCFF" | "unspecified";
        cash: number | null;
        debt: number | null;
        shares: number | null;
        financial_statement_date: string | null;
        model_input_reasons?: string[];
        reported_period_end?: string | null;
        filing_date?: string | null;
        period_date_basis?: string;
        shares_basis?: { value: number | null; source: string; basis: string; as_of: string | null } | null;
        input_lineage?: { notes?: string[]; debt?: { source: string | null; scope: string }; fcf_source?: string | null };
        equity_bridge?: { cash_basis: string; cash_added: number | null; equity_adjustment: number; complete: boolean };
    };
    current_price: number | null;
    scenario_source: "default" | "saved" | "request";
    default_scenarios?: DecisionValuationScenarioInput[];
    assumption_basis?: {
        wacc: {
            available: boolean;
            wacc: number | null;
            method: string;
            quality: "calculated" | "estimated_with_fallbacks";
            assumptions_as_of: string;
            risk_free_rate: number;
            equity_risk_premium: number;
            beta: number;
            raw_beta?: number | null;
            beta_policy?: string;
            beta_source: string;
            cost_of_equity: number;
            average_debt: number | null;
            cost_of_debt: number | null;
            debt_cost_source: string;
            /**
             * "annualized" when some TTM quarters lack interest and the reported ones are annualized;
             * "estimated" when no usable quarters exist and interest is book debt × the fallback cost of debt.
             */
            interest_expense_source?: "reported" | "annualized" | "estimated" | "debt_free" | "unavailable";
            tax_rate: number;
            equity_weight: number | null;
            debt_weight: number | null;
            notes: string[];
            unavailable_reasons: string[];
        };
        growth: {
            method: string;
            signals: Array<{ source: string; raw_value: number; winsorized_value: number }>;
            base_growth: number;
            scenario_spread: number;
            notes?: string[];
            forecast_source?: string;
        };
        terminal_growth: { rate: number; configured_rate: number; method: string };
        cash_flow: {
            available: boolean;
            fcff: number | null;
            reported_fcf: number | null;
            after_tax_interest_adjustment: number | null;
            reason: string | null;
        };
    } | null;
    scenarios: DecisionValuationScenarioResult[];
    implied_growth: DecisionImpliedGrowth;
    position: { status: string; text: string };
    sensitivity: {
        wacc_values: number[];
        terminal_growth_values: number[];
        fcf_growth_rate: number;
        values: Array<Array<number | null>>;
        cell_reasons: Array<Array<string | null>>;
    };
    formula: {
        forecast_years: number;
        initial_growth_years?: number;
        forecast_mode?: string;
        cash_flow_type: string;
        cash_treatment: string;
        debt_treatment: string;
        terminal_value: string;
    };
}

export interface PeerScopeResult {
    scope: "industry" | "sector";
    minimum_observations: number;
    observation_count: number;
    available: boolean;
    raw_percentile: number | null;
    desirability_percentile: number | null;
    reason: string | null;
}

export interface PeerMetric {
    key: string;
    label: string;
    direction: "higher_better" | "lower_better";
    format: "percent" | "multiple" | "ratio";
    evidence_id: string;
    value: number | null;
    industry: PeerScopeResult;
    sector: PeerScopeResult;
    summary_scope: "industry" | "sector" | null;
    summary_percentile: number | null;
}

export type PeerMultipleKey =
    | "pe_ratio"
    | "forward_pe"
    | "ps_ratio"
    | "pb_ratio"
    | "price_fcf"
    | "ev_sales"
    | "ev_ebitda";

export type PeerMultipleScope = "auto" | "industry" | "sector";

export interface PeerMultiplesResponse {
    available: boolean;
    reason: "target_not_in_snapshot"
        | "target_metric_unavailable"
        | "insufficient_industry_coverage"
        | "insufficient_sector_coverage"
        | null;
    metric: { key: PeerMultipleKey; label: string; format: "multiple" };
    as_of_date: string | null;
    target: {
        ticker: string;
        name: string | null;
        value: number | null;
        market_cap: number | null;
        sales_growth_ttm: number | null;
        raw_percentile: number | null;
        premium_to_median: number | null;
    };
    cohort: {
        scope: "industry" | "sector";
        name: string | null;
        member_count: number;
        valid_count: number;
        excluded_count: number;
        minimum_observations: number;
    } | null;
    distribution: {
        mean: number;
        median: number;
        p10: number;
        p25: number;
        p75: number;
        p90: number;
    } | null;
    peers: Array<{
        ticker: string;
        name: string | null;
        value: number;
        market_cap: number | null;
        sales_growth_ttm: number | null;
    }>;
}

export interface DecisionWarning {
    id: string;
    severity: "warning" | "high";
    title: string;
    message: string;
    metric: string;
    current: number | null;
    previous: number | null;
    evidence_metric: "revenue" | "gross_margin" | "operating_margin" | "fcf" | "cash" | "debt" | "debt_to_equity" | "shares" | "expectations" | "short_interest";
    /** Null for consensus and short-interest flags, which stay outside the brief's evidence registry. */
    evidence_id: string | null;
    category?: "fundamental" | "expectations" | "positioning";
    source_date?: string | null;
}

export interface ExpectationSignalValues {
    as_of: string | null;
    eps_period_end: string | null;
    eps_estimate: number | null;
    eps_estimate_30d_ago: number | null;
    eps_estimate_90d_ago: number | null;
    eps_revision_30d: number | null;
    eps_revision_90d: number | null;
    eps_revisions_up_30d: number | null;
    eps_revisions_down_30d: number | null;
    eps_estimate_analysts: number | null;
    eps_revision_breadth_30d: number | null;
    eps_next_year_revision_30d: number | null;
    eps_next_year_revision_90d: number | null;
    shares_short: number | null;
    shares_short_prior_month: number | null;
    short_interest_change: number | null;
    short_float: number | null;
    short_ratio: number | null;
}

export interface ExpectationSignalsContext {
    available: boolean;
    source: string;
    as_of: string | null;
    max_age_days: number;
    signals: ExpectationSignalValues | null;
    /** False when the data is stale or fewer than three analysts cover the current fiscal year. */
    estimate_assessed?: boolean;
    estimate_signal: { direction: "cut" | "raise"; basis: "30d" | "90d" | "breadth"; severity: "warning" | "high" | null } | null;
    short_signal: { kind: "rising" | "crowded"; severity: "warning" | "high" } | null;
    notes: string[];
}

export interface DecisionSummaryMetric {
    key: string;
    label: string;
    value: number | null;
    format: PeerMetric["format"];
    direction: PeerMetric["direction"];
    scope: "industry" | "sector";
    desirability_percentile: number;
    evidence_id: string;
}

export interface DecisionEvidenceItem {
    id: string;
    kind: string;
    label: string;
    value: unknown;
    source_date: string | null;
    available: boolean;
}

export type EarningsQualityPeriodType = "annual" | "quarterly";

export interface EarningsQualityFlag {
    category: string;
    label: string;
    amount: number;
    materiality_ratio: number;
    severity: "warning" | "high";
    source: string;
    source_field: string;
    detail: string;
    treatment: "flag_only" | "recurring_flag_only";
    recurring_adjustment: boolean;
    reported_amount?: number;
    comparison_amount?: number;
}

export interface EarningsQualitySourceSnapshot {
    source_id: string;
    accession: string;
    form: string;
    document_name: string;
    source_url: string;
    html_snapshot_id: number;
    text_snapshot_id: number;
    html_checksum: string;
    text_checksum: string;
}

export interface EarningsQualityAnalysis {
    id: number;
    ticker: string;
    period_end: string;
    period_type: EarningsQualityPeriodType;
    status: "queued" | "running" | "completed" | "failed" | "waiting_for_filing";
    stage: string;
    model: string;
    prompt_version: string;
    source_accession: string | null;
    source_snapshots: EarningsQualitySourceSnapshot[];
    result: {
        verification_status: "verified" | "flag_only";
        reported_net_income: number;
        normalized_net_income: number | null;
        adjusted_eps: number | null;
        company_adjusted: {
            label: string;
            adjusted_net_income: number | null;
            adjusted_diluted_eps: number | null;
        } | null;
        adjustments: Array<{
            category: string;
            label: string;
            pretax_earnings_effect: number | null;
            tax_effect: number | null;
            earnings_effect_after_tax: number;
            include_in_normalized: boolean;
            recurring: boolean;
            cash_effect: "cash" | "non_cash" | "mixed" | "unknown";
            citation: {
                source_id: string;
                accession: string;
                document_name: string;
                section: string;
                excerpt: string;
                source_amount: number;
                source_unit_scale: number;
                period_end: string;
                period_scope: "quarter" | "annual";
            };
        }>;
        unquantified_candidates?: Array<{
            label: string;
            model_category: string;
            policy_category: string;
            failure_codes: string[];
        }>;
        notes: string[];
    } | null;
    validation_report: {
        verified: boolean;
        eps_verified?: boolean;
        checks: Array<Record<string, unknown>>;
        failures: Array<{ code: string; message: string; adjustment_index?: number }>;
        eps_failures?: Array<{ code: string; message: string }>;
        rejected_adjustments?: Array<{
            adjustment_index: number;
            label: string;
            model_category: string;
            policy_category: string;
            failure_codes?: string[];
        }>;
        sign_convention: string;
        gains_and_charges_treated_symmetrically: boolean;
    } | null;
    error_message: string | null;
    retryable: boolean;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
}

export interface EarningsQualityPeriod {
    period_end: string;
    period_type: EarningsQualityPeriodType;
    reported: {
        revenue: number | null;
        net_income: number | null;
        income_before_tax: number | null;
        net_income_from_continuing_operations: number | null;
        income_tax_expense: number | null;
        non_recurring: number | null;
        extraordinary_items: number | null;
        discontinued_operations: number | null;
        non_operating_income_net_other: number | null;
    };
    materiality_base: number | null;
    thresholds: { warning: number; high: number };
    flags: EarningsQualityFlag[];
    data_quality_warnings: Array<{ code: string; message: string }>;
    assessment: "unavailable" | "material_candidates" | "data_quality_warning" | "no_material_candidates";
    statement_fingerprint: string;
    analysis: EarningsQualityAnalysis | null;
    verified_normalized: {
        net_income: number | null;
        adjusted_eps: number | null;
    } | null;
}

export interface EarningsQualityResponse {
    ticker: string;
    currency: string | null;
    methodology: {
        materiality_base: string;
        warning_threshold: number;
        high_threshold: number;
        reported_remains_primary: boolean;
        structured_flags_are_adjustments: boolean;
    };
    summary: {
        verdict: "unavailable" | "flags_present" | "data_quality_warning" | "no_material_candidates_on_available_data";
        evaluated_periods: number;
        flagged_periods: number;
        data_quality_periods: number;
        financial_industry_exemption: boolean;
        message: string;
    };
    annual: EarningsQualityPeriod[];
    quarterly: EarningsQualityPeriod[];
    sec_analysis: {
        supported: boolean;
        cik: string | null;
        reason: string | null;
        supported_forms: string[];
        unsupported_forms: string[];
    };
}

export interface DecisionSupportResponse {
    metadata: {
        ticker: string;
        company_name: string | null;
        currency: string | null;
        industry: string | null;
        sector: string | null;
        price_date: string | null;
        screener_date: string | null;
        screener_published_at: string | null;
        financial_statement_date: string | null;
        factor_date: string | null;
        factor_published_at: string | null;
    };
    summary: {
        valuation_position: { status: string; text: string };
        strongest_peer_metrics: DecisionSummaryMetric[];
        weakest_peer_metrics: DecisionSummaryMetric[];
        fundamental_warnings: DecisionWarning[];
        coverage: {
            quarterly_statements: number;
            peer_metrics_available: number;
            peer_metrics_total: number;
            published_factor_count: number;
            missing_data_reasons: string[];
            data_quality_notes: Array<{ code: string; message: string }>;
        };
    };
    valuation: DecisionValuation;
    peer_comparison: {
        ticker_in_screener: boolean;
        industry: string | null;
        sector: string | null;
        industry_member_count: number;
        sector_member_count: number;
        metrics: PeerMetric[];
        strongest: DecisionSummaryMetric[];
        weakest: DecisionSummaryMetric[];
        available_metric_count: number;
        total_metric_count: number;
    };
    risks: {
        warnings: DecisionWarning[];
        data_quality_notes: Array<{ code: string; message: string }>;
        high_count: number;
        warning_count: number;
    };
    expectation_signals?: ExpectationSignalsContext;
    earnings_quality?: EarningsQualityResponse;
    evidence: DecisionEvidenceItem[];
}

export interface PublishedFactorValue {
    raw_value: number | null;
    normalized_value: number | null;
    details?: Record<string, unknown> | null;
}

export interface PublishedFactorSnapshot {
    ticker: string;
    as_of_date: string;
    published_at: string;
    version: string;
    factors: Record<string, PublishedFactorValue>;
}

export type MarketUniverse = "SP500" | "RUSSELL2000" | "SP500_RUSSELL2000";
export type MarketPeriod = "3m" | "6m" | "1y";
export type YieldCurvePeriod = "1y" | "3y" | "5y";

export interface TreasuryYieldObservation {
    date: string;
    yields: Record<string, number | null>;
}

export interface TreasuryYieldCurveResponse {
    meta: {
        period: YieldCurvePeriod;
        as_of_date: string;
        fetched_at: string;
        source_name: string;
        provider_name: string;
        source_url: string;
        stale: boolean;
        warnings: string[];
    };
    maturities: Array<{
        key: string;
        label: string;
        years: number;
    }>;
    latest: TreasuryYieldObservation;
    snapshots: Array<TreasuryYieldObservation & {
        key: string;
        label: string;
    }>;
    observations: TreasuryYieldObservation[];
}

export interface IndexValuationPoint {
    date: string;
    index_pe: number | null;
    index_pe_earners: number | null;
    median_pe: number | null;
    member_count: number;
    covered_count: number;
    loss_maker_count: number;
    coverage_pct: number | null;
    reason: string | null;
}

export interface IndexValuationResponse {
    meta: {
        universe: "SP500";
        as_of_date: string;
        expected_as_of_date: string;
        published_at: string | null;
        stale: boolean;
        history_start: string;
        history_end: string;
        membership_mode: "point_in_time";
        history_basis: "reconstructed_estimates";
        price_basis: "split_only";
        warnings: string[];
    };
    methodology: string[];
    points: IndexValuationPoint[];
    stats: {
        months_total: number;
        months_valid: number;
        latest_date: string | null;
        latest_index_pe: number | null;
        latest_index_pe_earners: number | null;
        latest_median_pe: number | null;
        median_index_pe: number | null;
        min_index_pe: number | null;
        max_index_pe: number | null;
        average_coverage_pct: number | null;
    };
}

export interface MarketOverviewResponse {
    meta: {
        universe: MarketUniverse;
        period: MarketPeriod;
        as_of_date: string;
        expected_as_of_date: string;
        published_at: string;
        stale: boolean;
        membership_mode: "point_in_time";
        data_complete: boolean;
        warnings: string[];
    };
    dates: string[];
    benchmark: {
        ticker: string;
        absolute_index: number[];
    };
    sector_trends: Array<{
        ticker: string;
        label: string;
        absolute_index: number[];
        relative_to_spy_index: number[];
    }>;
    rsp_spy_index: number[];
    breadth: {
        pct_above_ma20: Array<number | null>;
        pct_above_ma50: Array<number | null>;
        pct_above_ma200: Array<number | null>;
        net_advances_pct: Array<number | null>;
        new_high_low_pct: Array<number | null>;
        new_high_pct: Array<number | null>;
        new_low_pct: Array<number | null>;
        mcclellan: Array<number | null>;
        dispersion_1d: Array<number | null>;
        dispersion_20d: Array<number | null>;
        member_count: number[];
        price_coverage_pct: Array<number | null>;
    };
}

export interface NewsItem {
    title: string;
    link: string;
    pub_date: string;
    summary: string;
    publisher: string;
}

export interface AnomalyReport {
    ticker: string;
    company_name: string;
    company_description?: string | null;
    market_cap?: number | null;
    date: string;
    quote_timestamp: string;
    price_change: number;
    ai_analysis: string;
    attribution_status: "completed" | "no_news" | "timed_out" | "news_unavailable" | "attribution_unavailable";
    news: NewsItem[];
    top_news_links: string[];
}

export interface AnomalyScan {
    id: number;
    trigger: string;
    status: "queued" | "running" | "completed" | "failed";
    requested_limit: number;
    threshold_pct: number;
    universe_as_of: string | null;
    quote_as_of: string | null;
    results: AnomalyReport[];
    error_message: string | null;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
}

export interface ScreenerPayload {
    limit: number;
    offset: number;
    sort_by: string;
    sort_desc: boolean;
    as_of_date?: string;
    sector?: string;
    market_cap_min?: number;
    market_cap_max?: number;
    pe_min?: number;
    pe_max?: number;
    rsi_14_min?: number;
    rsi_14_max?: number;
    price_above_ma50?: boolean;
    price_below_ma50?: boolean;
    roe_min?: number;
    debt_to_equity_max?: number;
    fcf_min?: number;
    gross_margin_min?: number;
    sales_growth_5yr_min?: number;
}

export interface ScreenerResult {
    ticker: string;
    name?: string | null;
    sector?: string | null;
    industry?: string | null;
    market_cap?: number | null;
    close?: number | null;
    pe_ratio?: number | null;
    roe?: number | null;
    debt_to_equity?: number | null;
    gross_margin?: number | null;
    sales_growth_5yr?: number | null;
    rsi_14?: number | null;
    date?: string;
    [key: string]: unknown;
}

export interface ScreenerResponse {
    total: number;
    items: ScreenerResult[];
    limit: number;
    offset: number;
    as_of_date: string | null;
}

export const fetchStockData = (
    ticker: string,
    interval = "1d",
    financialPeriod = "Yearly",
    signal?: AbortSignal,
) => apiRequest<StockDataResponse>(
    `/api/stocks/${encodeURIComponent(ticker)}?interval=${encodeURIComponent(interval)}&financial_period=${encodeURIComponent(financialPeriod)}`,
    { signal },
    90_000,
);

export const fetchMarketSnapshot = (ticker: string, signal?: AbortSignal) =>
    apiRequest<MarketSnapshotResponse>(
        `/api/stocks/${encodeURIComponent(ticker)}/market-snapshot`,
        { signal },
    );

export const fetchFinancialFlow = (
    ticker: string,
    periodType: "annual" | "quarterly",
    periodEnd?: string | null,
    signal?: AbortSignal,
) => {
    const params = new URLSearchParams({ period_type: periodType });
    if (periodEnd) params.set("period_end", periodEnd);
    return apiRequest<FinancialFlowResponse>(
        `/api/stocks/${encodeURIComponent(ticker)}/financial-flow?${params.toString()}`,
        { signal },
        90_000,
    );
};

export const fetchEventsExpectations = (ticker: string, signal?: AbortSignal) =>
    apiRequest<EventsExpectationsResponse>(
        `/api/stocks/${encodeURIComponent(ticker)}/events-expectations`,
        { signal },
    );

export interface QualityMetric {
    value: number | null;
    /** 0-100 between the weak and strong thresholds. */
    score: number | null;
    label: string;
    unit: "percent" | "ratio";
    weak: number;
    strong: number;
}

export interface QualityPillar {
    key: "profitability" | "growth" | "cash_conversion" | "balance_sheet" | "capital_allocation";
    label: string;
    score: number | null;
    metrics: string[];
    applicable: boolean;
}

export interface QualityScorecardResponse {
    ticker: string;
    available: boolean;
    template: "standard" | "financial" | "reit" | null;
    industry?: string | null;
    score: number | null;
    grade: string | null;
    pillars: QualityPillar[];
    metrics: Record<string, QualityMetric>;
    piotroski: {
        score: number | null;
        available: number;
        checks: Array<{ key: string; label: string; passed: boolean | null }>;
        fiscal_years?: string[];
    } | null;
    altman: {
        z: number;
        zone: "safe" | "grey" | "distress";
        zone_label: string;
        fiscal_year: string;
    } | null;
    fiscal_years: string[];
    source: string;
    fetched_at: string | null;
    notes: string[];
}

export const fetchQualityScorecard = (ticker: string, signal?: AbortSignal) =>
    apiRequest<QualityScorecardResponse>(`/api/stocks/${encodeURIComponent(ticker)}/quality`, { signal });

export const fetchLatestTickerFactors = (ticker: string, signal?: AbortSignal) =>
    apiRequest<PublishedFactorSnapshot>(`/api/quant/factors/${encodeURIComponent(ticker)}/latest`, { signal });

const personalHeaders = (adminKey?: string, json = false) => {
    const headers: Record<string, string> = {};
    if (adminKey) headers["X-API-Key"] = adminKey;
    if (json) headers["Content-Type"] = "application/json";
    return headers;
};

export const fetchDecisionSupport = (
    ticker: string,
    adminKey?: string,
    signal?: AbortSignal,
) => apiRequest<DecisionSupportResponse>(
    `/api/stocks/${encodeURIComponent(ticker)}/decision-support`,
    { headers: personalHeaders(adminKey), signal },
);

export const fetchPeerMultiples = (
    ticker: string,
    metric: PeerMultipleKey = "ps_ratio",
    scope: PeerMultipleScope = "auto",
    signal?: AbortSignal,
) => apiRequest<PeerMultiplesResponse>(
    `/api/stocks/${encodeURIComponent(ticker)}/peer-multiples?metric=${encodeURIComponent(metric)}&scope=${encodeURIComponent(scope)}&limit=10`,
    { signal },
);

export const fetchEarningsQuality = (
    ticker: string,
    signal?: AbortSignal,
) => apiRequest<EarningsQualityResponse>(
    `/api/stocks/${encodeURIComponent(ticker)}/earnings-quality`,
    { signal },
);

export const startEarningsQualityAnalysis = (
    ticker: string,
    periodEnd: string,
    periodType: EarningsQualityPeriodType,
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<EarningsQualityAnalysis>(
    `/api/personal/stocks/${encodeURIComponent(ticker)}/earnings-quality/analyses`,
    {
        method: "POST",
        headers: personalHeaders(adminKey, true),
        body: JSON.stringify({ period_end: periodEnd, period_type: periodType }),
        signal,
    },
);

export const fetchEarningsQualityAnalysis = (
    ticker: string,
    analysisId: number,
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<EarningsQualityAnalysis>(
    `/api/stocks/${encodeURIComponent(ticker)}/earnings-quality/analyses/${analysisId}`,
    { headers: personalHeaders(adminKey), signal },
);

export const calculateDecisionValuation = (
    ticker: string,
    scenarios: DecisionValuationScenarioInput[],
    signal?: AbortSignal,
) => apiRequest<DecisionValuation>(
    `/api/stocks/${encodeURIComponent(ticker)}/valuation/calculate`,
    {
        method: "POST",
        headers: personalHeaders(undefined, true),
        body: JSON.stringify({ scenarios }),
        signal,
    },
);

export const fetchPersonalWatchlist = (adminKey: string, signal?: AbortSignal) =>
    apiRequest<{ tickers: string[] }>("/api/personal/watchlist", {
        headers: personalHeaders(adminKey),
        signal,
    });

/** Feishu-style colour of a report tag; the colour marks the signal type, not advice. */
export type ReportTagColor = "blue" | "orange" | "green" | "red" | "carmine" | "turquoise" | "purple" | string;

export interface TodayRegimeItem {
    key: string;
    label: string;
    state: string;
    tone: "green" | "red" | "orange" | "grey" | string;
    detail: string;
    as_of: string | null;
}

export interface TodayCluster {
    kind: "theme" | "industry";
    name: string;
    slug: string | null;
    direction: "up" | "down";
    tickers: string[];
    /** Percent units, e.g. -10.77 for -10.77%. */
    average_move: number;
}

export interface TodayMover {
    ticker: string;
    company_name: string | null;
    /** Percent units, e.g. 11.92 for +11.92%. */
    price_change: number | null;
    quote_timestamp: string | null;
    news_title: string | null;
    news_link: string | null;
}

export interface TodayEvent {
    kind: "economic" | "earnings";
    name: string;
    date: string;
    session?: string | null;
    period?: string | null;
    actual?: number | null;
    estimate?: number | null;
}

export interface TodayResponse {
    generated_at: string;
    report: {
        run_id: number;
        report_type: "morning_briefing" | "post_market_summary";
        report_date: string | null;
        captured_at: string | null;
        status: string;
        delivered: boolean;
        /** ``overview_stale`` marks trend, breadth and participation read from an older market overview. */
        regime: { items: TodayRegimeItem[]; unavailable: string[]; overview_stale?: boolean } | null;
        regime_captured_at: string | null;
        anomaly_clusters: { as_of: string; movers: number; clusters: TodayCluster[] } | null;
        movers: TodayMover[];
    } | null;
    watchlist: {
        target: string | null;
        items: Array<{ ticker: string; tags: Array<{ text: string; color: ReportTagColor }>; as_of: string }>;
    } | null;
    events: {
        from: string;
        to: string;
        window_days: number;
        economic_available: boolean;
        earnings_available: boolean;
        items: TodayEvent[];
        timezone_note?: string;
    } | null;
    unavailable: string[];
}

export const fetchToday = (adminKey: string, signal?: AbortSignal) =>
    apiRequest<TodayResponse>("/api/personal/today", {
        headers: personalHeaders(adminKey),
        signal,
    }, 60_000);

export const replacePersonalWatchlist = (
    tickers: string[],
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<{ tickers: string[] }>("/api/personal/watchlist", {
    method: "PUT",
    headers: personalHeaders(adminKey, true),
    body: JSON.stringify({ tickers }),
    signal,
});

export const importPersonalWatchlist = (
    tickers: string[],
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<{ tickers: string[]; imported: boolean }>("/api/personal/watchlist/import", {
    method: "POST",
    headers: personalHeaders(adminKey, true),
    body: JSON.stringify({ tickers }),
    signal,
});

export const savePersonalValuationScenarios = (
    ticker: string,
    scenarios: DecisionValuationScenarioInput[],
    adminKey: string,
) => apiRequest<{ ticker: string; is_saved: boolean; scenarios: DecisionValuationScenarioInput[] }>(
    `/api/personal/stocks/${encodeURIComponent(ticker)}/valuation-scenarios`,
    {
        method: "PUT",
        headers: personalHeaders(adminKey, true),
        body: JSON.stringify({ scenarios }),
    },
);

export const resetPersonalValuationScenarios = (
    ticker: string,
    adminKey: string,
) => apiRequest<{ ticker: string; is_saved: boolean; scenarios: DecisionValuationScenarioInput[] }>(
    `/api/personal/stocks/${encodeURIComponent(ticker)}/valuation-scenarios`,
    { method: "DELETE", headers: personalHeaders(adminKey) },
);

export const fetchMarketOverview = (
    universe: MarketUniverse,
    period: MarketPeriod,
    signal?: AbortSignal,
) => apiRequest<MarketOverviewResponse>(
    `/api/v1/market-overview?universe=${encodeURIComponent(universe)}&period=${encodeURIComponent(period)}`,
    { signal },
    90_000,
);

export const fetchTreasuryYieldCurve = (
    period: YieldCurvePeriod,
    signal?: AbortSignal,
) => apiRequest<TreasuryYieldCurveResponse>(
    `/api/v1/yield-curve?period=${encodeURIComponent(period)}`,
    { signal },
    60_000,
);

export const fetchIndexValuation = (signal?: AbortSignal) =>
    apiRequest<IndexValuationResponse>("/api/v1/index-valuation", { signal }, 90_000);

export const fetchStockNews = (ticker: string, signal?: AbortSignal) =>
    apiRequest<NewsItem[]>(`/api/stocks/${encodeURIComponent(ticker)}/news`, { signal });

export const fetchLatestAnomalyScan = (signal?: AbortSignal) =>
    apiRequest<AnomalyScan | null>("/api/market/anomalies", { signal });

export const startAnomalyScan = (signal?: AbortSignal) =>
    apiRequest<AnomalyScan>("/api/market/anomalies/scans", {
        method: "POST",
        signal,
    });

export const fetchAnomalyScan = (scanId: number, signal?: AbortSignal) =>
    apiRequest<AnomalyScan>(`/api/market/anomalies/scans/${scanId}`, { signal });

export const fetchScreener = (payload: ScreenerPayload, signal?: AbortSignal) =>
    apiRequest<ScreenerResponse>("/api/stocks/screener", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal,
    });

export interface SimilarStockMatch {
    ticker: string;
    name: string | null;
    industry: string | null;
    correlation: number;
    returns: number[];
}
export interface SimilarStocksResponse {
    ticker: string;
    window_days: number;
    as_of: string | null;
    status: "ok" | "insufficient_history" | "unsupported_market" | "unsupported_security";
    dates: string[];
    target_returns: number[];
    matches: SimilarStockMatch[];
    eligible_count: number;
}
export const fetchSimilarStocks = (ticker: string, signal?: AbortSignal) =>
    apiRequest<SimilarStocksResponse>(`/api/stocks/${encodeURIComponent(ticker)}/similar`, { signal });

export type MarketPhase = "pre" | "open" | "post" | "closed";

export interface MarketStatusResponse {
    phase: MarketPhase;
    session_date: string | null;
    opens_at: string | null;
    closes_at: string | null;
    next_open: string;
    as_of: string;
}

export interface WatchlistQuote {
    price: number;
    previous_close: number | null;
    change: number | null;
    change_pct: number | null;
    as_of: string;
    session_date: string;
}

export interface WatchlistQuoteItem {
    ticker: string;
    quote: WatchlistQuote | null;
    sparkline: Array<{ date: string; close: number }>;
}

export interface WatchlistQuotesResponse {
    market: MarketStatusResponse;
    delay_minutes: number;
    items: WatchlistQuoteItem[];
}

export const fetchMarketStatus = (signal?: AbortSignal) =>
    apiRequest<MarketStatusResponse>("/api/v1/market/status", { signal }, 10_000);

export const fetchWatchlistQuotes = (tickers: string[], signal?: AbortSignal) =>
    apiRequest<WatchlistQuotesResponse>(
        `/api/v1/watchlist/quotes?tickers=${encodeURIComponent(tickers.join(","))}`,
        { signal },
        20_000,
    );

/** realtime: streamed trade in the session; delayed: 15-minute feed; close: the session's closing price. */
export type LiveQuoteSource = "realtime" | "delayed" | "close";

export interface LiveQuote extends WatchlistQuote {
    source: LiveQuoteSource;
}

export interface LiveQuoteResponse {
    ticker: string;
    market: MarketStatusResponse;
    quote: LiveQuote | null;
}

export const fetchLiveQuote = (ticker: string, signal?: AbortSignal) =>
    apiRequest<LiveQuoteResponse>(`/api/v1/quotes/live?ticker=${encodeURIComponent(ticker)}`, { signal }, 10_000);

// Trending themes. Mirrors the Theme* models in api/schemas.py. Returns are simple
// fractions (0.05 == +5%); missing values stay null and render as "—".
export type ThemeStage = "emerging" | "accelerating" | "leading" | "crowded" | "fading" | "neutral" | "insufficient";
export type ThemeView = "now" | "trend";
export type ThemeProposalStatus = "pending" | "accepted" | "rejected" | "expired";
export type ThemeProposalDecision = "accepted" | "rejected";

export interface ThemeTrendsMeta {
    as_of_date: string;
    expected_as_of_date: string;
    published_at: string | null;
    stale: boolean;
    scoring_version: string;
    taxonomy_version: string | null;
    point_in_time: boolean;
    attention_degraded: boolean;
    coverage_pct: number | null;
    warnings: string[];
}

export interface ThemeLeader {
    ticker: string;
    name: string | null;
}

export interface ThemeListItem {
    slug: string;
    name_en: string;
    name_zh: string;
    megatrend: string | null;
    stage: ThemeStage;
    candidate_stage: ThemeStage;
    stage_changed: boolean;
    heat: number | null;
    heat_now: number | null;
    heat_trend: number | null;
    rank_now: number | null;
    rank_trend: number | null;
    rank_change: number | null;
    streak_top5: number;
    score_price: number | null;
    score_breadth: number | null;
    score_volume: number | null;
    score_attention: number | null;
    excess_5d: number | null;
    excess_21d: number | null;
    excess_63d: number | null;
    pct_above_ma50: number | null;
    near_high_share: number | null;
    pct_above_ma50_change_10d: number | null;
    ignitions_today: number;
    ignitions_5d: number;
    n_members: number;
    n_priced: number;
    leaders: ThemeLeader[];
    proxy_etf: string | null;
    proxy_excess_21d: number | null;
    heat_sparkline: Array<number | null>;
    emerging: boolean;
    attention_only: boolean;
    attention_degraded: boolean;
    point_in_time: boolean;
}

export interface ThemeListSummary {
    ranked: number;
    emerging: number;
    accelerating: number;
    leading: number;
    crowded: number;
    fading: number;
    top_riser: string | null;
}

export interface ThemeListResponse {
    meta: ThemeTrendsMeta;
    view: ThemeView;
    summary: ThemeListSummary;
    themes: ThemeListItem[];
}

export interface ThemeMemberRow {
    ticker: string;
    name: string | null;
    tier: "core" | "peripheral";
    weight: number;
    exposure: number;
    role: string | null;
    review_status: string;
    sources: string[];
    evidence_quote: string | null;
    evidence_source: string | null;
    evidence_as_of: string | null;
    close: number | null;
    return_1d: number | null;
    excess_5d: number | null;
    excess_21d: number | null;
    near_high_ratio: number | null;
    relative_volume_5d: number | null;
    attention_z: number | null;
    ignited: boolean;
    first_ignition_date: string | null;
    leader_score: number | null;
    leader_rank: number | null;
    is_leader: boolean;
    sparkline: Array<number | null>;
}

export interface ThemeHistory {
    dates: string[];
    heat: Array<number | null>;
    heat_now: Array<number | null>;
    heat_trend: Array<number | null>;
    score_price: Array<number | null>;
    score_breadth: Array<number | null>;
    score_volume: Array<number | null>;
    score_attention: Array<number | null>;
    basket_index: Array<number | null>;
    benchmark_index: Array<number | null>;
    proxy_index: Array<number | null>;
    pct_above_ma50: Array<number | null>;
    near_high_share: Array<number | null>;
    ignitions: number[];
    stage: ThemeStage[];
    rank_now: Array<number | null>;
    point_in_time: boolean[];
}

export interface ThemeBriefEvidence {
    id: string;
    kind: string;
    label: string;
    source_date: string | null;
    link: string | null;
}

export interface ThemeBriefResponse {
    date: string;
    model: string;
    prompt_version: string;
    created_at: string | null;
    stale: boolean;
    content: string;
    evidence: ThemeBriefEvidence[];
}

export interface ThemeDetailResponse {
    meta: ThemeTrendsMeta;
    theme: ThemeListItem;
    definition: string;
    keywords: string[];
    proxy_etfs: string[];
    history: ThemeHistory;
    members: ThemeMemberRow[];
    brief: ThemeBriefResponse | null;
}

export interface ThemeHistoryResponse {
    meta: ThemeTrendsMeta;
    slug: string;
    history: ThemeHistory;
}

export interface TrendingCompany {
    rank: number;
    ticker: string;
    name: string | null;
    sector: string | null;
    industry: string | null;
    score: number;
    close: number | null;
    market_cap: number | null;
    return_1d: number | null;
    excess_5d: number | null;
    excess_5d_vs_sector: number | null;
    relative_volume_5d: number | null;
    attention_z: number | null;
    near_high_ratio: number | null;
    themes: string[];
    flags: string[];
}

export interface TrendingCompaniesResponse {
    meta: ThemeTrendsMeta;
    companies: TrendingCompany[];
}

export interface ThemeProposalItem {
    id: number;
    label_en: string | null;
    label_zh: string | null;
    slug_suggestion: string | null;
    rationale: string | null;
    tickers: string[];
    evidence: Array<Record<string, unknown>>;
    first_seen: string;
    last_seen: string;
    hit_count: number;
    status: ThemeProposalStatus;
}

export interface ThemeProposalsResponse {
    proposals: ThemeProposalItem[];
}

export interface ThemeProposalDecisionResponse {
    proposal: ThemeProposalItem;
    yaml_snippet: string | null;
}

export const fetchThemes = (
    { asOfDate, view, stage }: { asOfDate?: string | null; view: ThemeView; stage?: ThemeStage | null },
    adminKey: string,
    signal?: AbortSignal,
) => {
    const params = new URLSearchParams({ view });
    if (asOfDate) params.set("as_of_date", asOfDate);
    if (stage) params.set("stage", stage);
    return apiRequest<ThemeListResponse>(
        `/api/v1/themes?${params.toString()}`,
        { headers: personalHeaders(adminKey), signal },
        60_000,
    );
};

export const fetchThemeDetail = (
    slug: string,
    adminKey: string,
    asOfDate?: string | null,
    signal?: AbortSignal,
) => {
    const query = asOfDate ? `?${new URLSearchParams({ as_of_date: asOfDate }).toString()}` : "";
    return apiRequest<ThemeDetailResponse>(
        `/api/v1/themes/${encodeURIComponent(slug)}${query}`,
        { headers: personalHeaders(adminKey), signal },
        60_000,
    );
};

export const fetchThemeHistory = (
    slug: string,
    days: number,
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<ThemeHistoryResponse>(
    `/api/v1/themes/${encodeURIComponent(slug)}/history?days=${encodeURIComponent(days)}`,
    { headers: personalHeaders(adminKey), signal },
    60_000,
);

export const fetchTrendingCompanies = (
    { asOfDate, limit }: { asOfDate?: string | null; limit?: number },
    adminKey: string,
    signal?: AbortSignal,
) => {
    const params = new URLSearchParams();
    if (asOfDate) params.set("as_of_date", asOfDate);
    if (limit != null) params.set("limit", String(limit));
    const query = params.toString();
    return apiRequest<TrendingCompaniesResponse>(
        `/api/v1/trending-companies${query ? `?${query}` : ""}`,
        { headers: personalHeaders(adminKey), signal },
        60_000,
    );
};

export const fetchThemeProposals = (
    adminKey: string,
    status?: ThemeProposalStatus | null,
    signal?: AbortSignal,
) => apiRequest<ThemeProposalsResponse>(
    `/api/personal/themes/proposals${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    { headers: personalHeaders(adminKey), signal },
);

export const decideThemeProposal = (
    id: number,
    decision: ThemeProposalDecision,
    adminKey: string,
) => apiRequest<ThemeProposalDecisionResponse>(
    `/api/personal/themes/proposals/${encodeURIComponent(id)}/decision`,
    {
        method: "POST",
        headers: personalHeaders(adminKey, true),
        body: JSON.stringify({ decision }),
    },
);
