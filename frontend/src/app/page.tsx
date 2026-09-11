"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowDownRight, ArrowUpRight, Check, ChevronDown, ChevronUp, Plus, Search, ShieldCheck } from "lucide-react";
import {
    ApiError,
    DecisionSupportResponse,
    DecisionWarning,
    EarningsQualityAnalysis,
    EarningsQualityPeriod,
    EarningsQualityResponse,
    EventsExpectationsResponse,
    fetchDecisionSupport,
    fetchEventsExpectations,
    fetchEarningsQuality,
    fetchEarningsQualityAnalysis,
    fetchFinancialFlow,
    fetchLatestTickerFactors,
    fetchMarketSnapshot,
    fetchStockData,
    FinancialFlowResponse,
    MarketSnapshotResponse,
    PublishedFactorSnapshot,
    startEarningsQualityAnalysis,
    StockDataResponse,
} from "@/lib/api";
import DecisionCockpit, { type CockpitTab } from "@/components/DecisionCockpit";
import NewsFeed from "@/components/NewsFeed";
import PersonalUnlockDialog from "@/components/PersonalUnlockDialog";
import PointInTimeFactorPanel from "@/components/PointInTimeFactorPanel";
import StockSnapshotPanel from "@/components/StockSnapshotPanel";
import WatchlistSidebar from "@/components/WatchlistSidebar";
import { usePersonalWorkspace } from "@/hooks/usePersonalWorkspace";
import type { FinancialEvidenceMetric } from "@/components/FinancialTrendChart";
import AnalysisNavigation, { isAnalysisSection, type AnalysisSection } from "@/components/analysis/AnalysisNavigation";
import FinancialSnapshot from "@/components/analysis/FinancialSnapshot";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { formatSignedPercent } from "@/lib/format";

const StockValuationChart = dynamic(() => import("@/components/StockValuationChart"), {
    ssr: false,
    loading: () => <div className="h-[480px] animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />,
});
const OverviewStockChart = dynamic(() => import("@/components/StockChart"), {
    ssr: false,
    loading: () => <div className="h-[260px] animate-pulse bg-[var(--surface-muted)]" />,
});
const FinancialTrendChart = dynamic(() => import("@/components/FinancialTrendChart"), {
    ssr: false,
    loading: () => <div className="h-[520px] animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800" />,
});
const FinancialFlowPanel = dynamic(() => import("@/components/FinancialFlowPanel"), {
    ssr: false,
    loading: () => <div className="h-[520px] animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800" />,
});

const attachEarningsAnalysis = (
    current: EarningsQualityResponse | null,
    analysis: EarningsQualityAnalysis,
): EarningsQualityResponse | null => {
    if (!current || current.ticker !== analysis.ticker) return current;
    const update = (period: EarningsQualityPeriod) => period.period_end === analysis.period_end
        && period.period_type === analysis.period_type
        ? {
            ...period,
            analysis,
            verified_normalized: analysis.status === "completed"
                && analysis.result?.verification_status === "verified"
                ? {
                    net_income: analysis.result.normalized_net_income,
                    adjusted_eps: analysis.result.adjusted_eps,
                }
                : period.verified_normalized,
        }
        : period;
    return {
        ...current,
        annual: current.annual.map(update),
        quarterly: current.quarterly.map(update),
    };
};

const isFinancialFlowResponse = (value: unknown): value is FinancialFlowResponse => {
    if (!value || typeof value !== "object") return false;
    const candidate = value as Partial<FinancialFlowResponse>;
    return typeof candidate.ticker === "string"
        && Array.isArray(candidate.nodes)
        && Array.isArray(candidate.links)
        && Array.isArray(candidate.summary_cards)
        && Array.isArray(candidate.available_periods)
        && Array.isArray(candidate.insights)
        && Array.isArray(candidate.sources)
        && Boolean(candidate.enrichment && typeof candidate.enrichment.status === "string");
};

const waitForPoll = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
    const finish = () => {
        signal.removeEventListener("abort", abort);
        resolve();
    };
    const timer = globalThis.setTimeout(finish, milliseconds);
    const abort = () => {
        globalThis.clearTimeout(timer);
        signal.removeEventListener("abort", abort);
        reject(new DOMException("Aborted", "AbortError"));
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
});

function AnalysisPage() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const requestedSymbol = searchParams.get("ticker")?.trim().toUpperCase() || "";
    const requestedTicker = requestedSymbol && !requestedSymbol.includes(".") ? `${requestedSymbol}.US` : requestedSymbol;
    const requestedSection = searchParams.get("section");
    const activeSection: AnalysisSection = isAnalysisSection(requestedSection) ? requestedSection : "overview";
    const [ticker, setTicker] = useState("");
    const [stockData, setStockData] = useState<StockDataResponse | null>(null);
    const [factorSnapshot, setFactorSnapshot] = useState<PublishedFactorSnapshot | null>(null);
    const [decision, setDecision] = useState<DecisionSupportResponse | null>(null);
    const [earningsQuality, setEarningsQuality] = useState<EarningsQualityResponse | null>(null);
    const [eventsExpectations, setEventsExpectations] = useState<EventsExpectationsResponse | null>(null);
    const [marketSnapshot, setMarketSnapshot] = useState<MarketSnapshotResponse | null>(null);
    const [financialFlow, setFinancialFlow] = useState<FinancialFlowResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [factorLoading, setFactorLoading] = useState(false);
    const [decisionLoading, setDecisionLoading] = useState(false);
    const [earningsQualityLoading, setEarningsQualityLoading] = useState(false);
    const [earningsQualityBusyPeriod, setEarningsQualityBusyPeriod] = useState<string | null>(null);
    const [decisionRefreshVersion, setDecisionRefreshVersion] = useState(0);
    const [chartLoading, setChartLoading] = useState(false);
    const [error, setError] = useState("");
    const [factorError, setFactorError] = useState("");
    const [decisionError, setDecisionError] = useState("");
    const [earningsQualityError, setEarningsQualityError] = useState("");
    const [eventsExpectationsLoading, setEventsExpectationsLoading] = useState(false);
    const [eventsExpectationsError, setEventsExpectationsError] = useState("");
    const [marketSnapshotLoading, setMarketSnapshotLoading] = useState(false);
    const [marketSnapshotError, setMarketSnapshotError] = useState("");
    const [financialFlowLoading, setFinancialFlowLoading] = useState(false);
    const [financialFlowError, setFinancialFlowError] = useState("");
    const [chartInterval, setChartInterval] = useState("1d");
    const [financialPeriod, setFinancialPeriod] = useState<"annual" | "ttm" | "quarterly">("annual");
    const [financialMetric, setFinancialMetric] = useState<FinancialEvidenceMetric>("overview");
    const [unlockOpen, setUnlockOpen] = useState(false);
    const [descriptionExpanded, setDescriptionExpanded] = useState(false);
    const [cockpitView, setCockpitView] = useState<CockpitTab>("overview");
    const personal = usePersonalWorkspace();
    const watchlist = personal.watchlist;
    const handlePersonalUnauthorized = personal.handleUnauthorized;
    const stockRequestRef = useRef<AbortController | null>(null);
    const factorRequestRef = useRef<AbortController | null>(null);
    const decisionRequestRef = useRef<AbortController | null>(null);
    const earningsQualityRequestRef = useRef<AbortController | null>(null);
    const eventsExpectationsRequestRef = useRef<AbortController | null>(null);
    const marketSnapshotRequestRef = useRef<AbortController | null>(null);
    const financialFlowRequestRef = useRef<AbortController | null>(null);
    const financialFlowRequestedPeriodRef = useRef<string | undefined>(undefined);
    const earningsAnalysisRequestRef = useRef<AbortController | null>(null);
    const financialEvidenceRef = useRef<HTMLDivElement | null>(null);
    const pendingEvidenceFocusRef = useRef(false);

    const loadStock = useCallback(async (
        symbol: string,
        interval = "1d",
        period: "annual" | "ttm" | "quarterly" = "annual",
        initial = false,
    ) => {
        stockRequestRef.current?.abort();
        const controller = new AbortController();
        stockRequestRef.current = controller;
        if (initial) setLoading(true); else setChartLoading(true);
        setError("");
        try {
            const data = await fetchStockData(symbol, interval, period === "quarterly" ? "Quarterly" : "Yearly", controller.signal);
            if (!controller.signal.aborted) {
                setStockData(data);
                return true;
            }
            return false;
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return false;
            if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Unable to load this security.");
            return false;
        } finally {
            if (!controller.signal.aborted) {
                setLoading(false);
                setChartLoading(false);
            }
        }
    }, []);

    const loadFactors = useCallback(async (symbol: string) => {
        factorRequestRef.current?.abort();
        const controller = new AbortController();
        factorRequestRef.current = controller;
        setFactorLoading(true);
        setFactorError("");
        setFactorSnapshot(null);
        try {
            const snapshot = await fetchLatestTickerFactors(symbol, controller.signal);
            if (!controller.signal.aborted) setFactorSnapshot(snapshot);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (!controller.signal.aborted) setFactorError(caught instanceof Error ? caught.message : "Published factors are unavailable.");
        } finally {
            if (!controller.signal.aborted) setFactorLoading(false);
        }
    }, []);

    const loadDecision = useCallback(async (symbol: string, adminKey?: string | null) => {
        decisionRequestRef.current?.abort();
        const controller = new AbortController();
        decisionRequestRef.current = controller;
        setDecisionLoading(true);
        setDecisionError("");
        try {
            const result = await fetchDecisionSupport(symbol, adminKey || undefined, controller.signal);
            if (!controller.signal.aborted) setDecision(result);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401 && adminKey) {
                handlePersonalUnauthorized();
                try {
                    const publicResult = await fetchDecisionSupport(symbol, undefined, controller.signal);
                    if (!controller.signal.aborted) setDecision(publicResult);
                    return;
                } catch (fallbackError) {
                    if (!controller.signal.aborted) setDecisionError(fallbackError instanceof Error ? fallbackError.message : "Decision support is unavailable.");
                    return;
                }
            }
            if (!controller.signal.aborted) setDecisionError(caught instanceof Error ? caught.message : "Decision support is unavailable.");
        } finally {
            if (!controller.signal.aborted) setDecisionLoading(false);
        }
    }, [handlePersonalUnauthorized]);

    const loadEarningsQuality = useCallback(async (symbol: string) => {
        earningsQualityRequestRef.current?.abort();
        const controller = new AbortController();
        earningsQualityRequestRef.current = controller;
        setEarningsQualityLoading(true);
        setEarningsQualityError("");
        try {
            const result = await fetchEarningsQuality(symbol, controller.signal);
            if (!controller.signal.aborted) setEarningsQuality(result);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (!controller.signal.aborted) setEarningsQualityError(caught instanceof Error ? caught.message : "Earnings-quality evidence is unavailable.");
        } finally {
            if (!controller.signal.aborted) setEarningsQualityLoading(false);
        }
    }, []);

    const loadEventsExpectations = useCallback(async (symbol: string) => {
        eventsExpectationsRequestRef.current?.abort();
        const controller = new AbortController();
        eventsExpectationsRequestRef.current = controller;
        setEventsExpectationsLoading(true);
        setEventsExpectationsError("");
        try {
            const result = await fetchEventsExpectations(symbol, controller.signal);
            if (!controller.signal.aborted) setEventsExpectations(result);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (!controller.signal.aborted) setEventsExpectationsError(caught instanceof Error ? caught.message : "Events and expectations are unavailable.");
        } finally {
            if (!controller.signal.aborted) setEventsExpectationsLoading(false);
        }
    }, []);

    const loadMarketSnapshot = useCallback(async (symbol: string) => {
        marketSnapshotRequestRef.current?.abort();
        const controller = new AbortController();
        marketSnapshotRequestRef.current = controller;
        setMarketSnapshotLoading(true);
        setMarketSnapshotError("");
        try {
            const result = await fetchMarketSnapshot(symbol, controller.signal);
            if (!controller.signal.aborted) setMarketSnapshot(result);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (!controller.signal.aborted) setMarketSnapshotError(caught instanceof Error ? caught.message : "Market snapshot is unavailable.");
        } finally {
            if (!controller.signal.aborted) setMarketSnapshotLoading(false);
        }
    }, []);

    const loadFinancialFlow = useCallback(async (
        symbol: string,
        periodType: "annual" | "quarterly",
        periodEnd?: string,
    ) => {
        financialFlowRequestRef.current?.abort();
        const controller = new AbortController();
        financialFlowRequestRef.current = controller;
        financialFlowRequestedPeriodRef.current = periodEnd;
        setFinancialFlow((current) => {
            if (!current || current.ticker !== symbol || current.period_type !== periodType) return null;
            if (periodEnd && current.period_end !== periodEnd) return null;
            return current;
        });
        setFinancialFlowLoading(true);
        setFinancialFlowError("");
        try {
            let result = await fetchFinancialFlow(symbol, periodType, periodEnd, controller.signal);
            if (controller.signal.aborted) return;
            if (!isFinancialFlowResponse(result)) throw new Error("Profit flow returned an incomplete response.");
            setFinancialFlow(result);

            for (let attempt = 0; attempt < 20 && ["queued", "running"].includes(result.enrichment.status); attempt += 1) {
                await waitForPoll(3_000, controller.signal);
                result = await fetchFinancialFlow(symbol, periodType, periodEnd, controller.signal);
                if (!isFinancialFlowResponse(result)) throw new Error("Profit flow returned an incomplete response.");
                if (!controller.signal.aborted) setFinancialFlow(result);
            }
            if (!controller.signal.aborted && ["queued", "running"].includes(result.enrichment.status)) {
                setFinancialFlow({
                    ...result,
                    enrichment: { ...result.enrichment, status: "pending_refresh" },
                });
            }
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (!controller.signal.aborted) setFinancialFlowError(caught instanceof Error ? caught.message : "Profit flow is unavailable.");
        } finally {
            if (!controller.signal.aborted) setFinancialFlowLoading(false);
        }
    }, []);

    const cachedActiveEarningsAnalysisId = [
        ...(earningsQuality?.quarterly ?? []),
        ...(earningsQuality?.annual ?? []),
    ].map((period) => period.analysis).find((analysis) => (
        analysis?.ticker === requestedTicker
        && (analysis.status === "queued" || analysis.status === "running")
    ))?.id ?? null;

    useEffect(() => {
        const adminKey = personal.adminKey;
        if (!requestedTicker || !cachedActiveEarningsAnalysisId || earningsQualityBusyPeriod || !adminKey) return;
        const controller = new AbortController();
        const analysisId = cachedActiveEarningsAnalysisId;
        const poll = async () => {
            try {
                let active = true;
                while (!controller.signal.aborted && active) {
                    await waitForPoll(1_500, controller.signal);
                    const analysis = await fetchEarningsQualityAnalysis(requestedTicker, analysisId, adminKey, controller.signal);
                    active = analysis.status === "queued" || analysis.status === "running";
                    if (!controller.signal.aborted) setEarningsQuality((current) => attachEarningsAnalysis(current, analysis));
                }
                if (!controller.signal.aborted) {
                    await Promise.all([
                        loadEarningsQuality(requestedTicker),
                        loadDecision(requestedTicker, personal.adminKey),
                    ]);
                }
            } catch (caught) {
                if (caught instanceof DOMException && caught.name === "AbortError") return;
                if (!controller.signal.aborted) setEarningsQualityError(caught instanceof Error ? caught.message : "Filing analysis status is unavailable.");
            }
        };
        void poll();
        return () => controller.abort();
    }, [cachedActiveEarningsAnalysisId, earningsQualityBusyPeriod, loadDecision, loadEarningsQuality, personal.adminKey, requestedTicker]);

    useEffect(() => {
        if (!requestedTicker) {
            setTicker("");
            setStockData(null);
            setFactorSnapshot(null);
            setEarningsQuality(null);
            setEventsExpectations(null);
            setMarketSnapshot(null);
            setFinancialFlow(null);
            setMarketSnapshotError("");
            setFinancialFlowError("");
            setError("");
            return;
        }
        setTicker(requestedTicker);
        setStockData(null);
        setDecision(null);
        setEarningsQuality(null);
        setEventsExpectations(null);
        setMarketSnapshot(null);
        setFinancialFlow(null);
        setMarketSnapshotError("");
        setFinancialFlowError("");
        setEarningsQualityBusyPeriod(null);
        setEarningsQualityError("");
        setEventsExpectationsError("");
        setChartInterval("1d");
        setFinancialPeriod("annual");
        setFinancialMetric("overview");
        setDescriptionExpanded(false);
        void loadStock(requestedTicker, "1d", "annual", true).then((loaded) => {
            // The stock endpoint can populate a cold local snapshot. Refresh the
            // cockpit after that read-through completes so it sees the new data.
            if (loaded) {
                setDecisionRefreshVersion((version) => version + 1);
                void loadEarningsQuality(requestedTicker);
                void loadEventsExpectations(requestedTicker);
                void loadMarketSnapshot(requestedTicker);
                void loadFinancialFlow(requestedTicker, "annual");
            }
        });
        void loadFactors(requestedTicker);
        void loadEarningsQuality(requestedTicker);
        void loadMarketSnapshot(requestedTicker);
        return () => {
            stockRequestRef.current?.abort();
            factorRequestRef.current?.abort();
            earningsQualityRequestRef.current?.abort();
            eventsExpectationsRequestRef.current?.abort();
            marketSnapshotRequestRef.current?.abort();
            financialFlowRequestRef.current?.abort();
            earningsAnalysisRequestRef.current?.abort();
        };
    }, [loadEarningsQuality, loadEventsExpectations, loadFactors, loadFinancialFlow, loadMarketSnapshot, loadStock, requestedTicker]);

    useEffect(() => {
        if (!requestedTicker) {
            setDecision(null);
            setDecisionError("");
            return;
        }
        void loadDecision(requestedTicker, personal.adminKey);
        return () => decisionRequestRef.current?.abort();
    }, [decisionRefreshVersion, loadDecision, personal.adminKey, requestedTicker]);

    const selectTicker = (symbol: string) => router.push(`/?ticker=${encodeURIComponent(symbol.toUpperCase())}`);

    const selectSection = useCallback((section: AnalysisSection) => {
        const params = new URLSearchParams(searchParams.toString());
        if (section === "overview") params.delete("section");
        else params.set("section", section);
        const query = params.toString();
        router.push(query ? `/?${query}` : "/", { scroll: false });
    }, [router, searchParams]);

    useEffect(() => {
        setCockpitView((current) => {
            if (activeSection === "overview") return "overview";
            if (activeSection === "valuation") return current === "peers" ? current : "valuation";
            if (activeSection === "financials") return "risks";
            if (activeSection === "events") return "brief";
            return current;
        });
    }, [activeSection]);

    useEffect(() => {
        if (activeSection !== "financials" || !pendingEvidenceFocusRef.current) return;
        pendingEvidenceFocusRef.current = false;
        const frame = window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
            financialEvidenceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
            financialEvidenceRef.current?.focus({ preventScroll: true });
        }));
        return () => window.cancelAnimationFrame(frame);
    }, [activeSection, financialMetric, financialPeriod]);

    const handleIntervalChange = async (interval: string) => {
        if (!ticker || interval === chartInterval) return;
        setChartInterval(interval);
        await loadStock(ticker, interval, financialPeriod);
    };

    const handlePeriodChange = async (period: "annual" | "ttm" | "quarterly") => {
        if (!ticker || period === financialPeriod) return;
        const previous = financialPeriod;
        setFinancialPeriod(period);
        if (period === "quarterly" || previous === "quarterly") await loadStock(ticker, chartInterval, period);
        if (period === "ttm") {
            financialFlowRequestRef.current?.abort();
            setFinancialFlowLoading(false);
        } else {
            await loadFinancialFlow(ticker, period);
        }
    };

    const addToWatchlist = (symbol: string) => {
        if (!personal.isUnlocked) {
            setUnlockOpen(true);
            return;
        }
        if (!watchlist.includes(symbol)) void personal.replaceWatchlist([symbol, ...watchlist]);
    };

    const removeFromWatchlist = (symbol: string) => {
        if (!personal.isUnlocked) {
            setUnlockOpen(true);
            return;
        }
        void personal.replaceWatchlist(watchlist.filter((item) => item !== symbol));
    };

    const refreshDecision = useCallback(async () => {
        if (!ticker) return;
        await loadDecision(ticker, personal.adminKey);
    }, [loadDecision, personal.adminKey, ticker]);

    const analyzeEarningsPeriod = useCallback(async (period: EarningsQualityPeriod) => {
        if (!ticker) return;
        if (!personal.adminKey) {
            setUnlockOpen(true);
            return;
        }
        earningsAnalysisRequestRef.current?.abort();
        const controller = new AbortController();
        earningsAnalysisRequestRef.current = controller;
        const requestTicker = ticker;
        const adminKey = personal.adminKey;
        const key = `${period.period_type}:${period.period_end}`;
        setEarningsQualityBusyPeriod(key);
        setEarningsQualityError("");
        try {
            let analysis = await startEarningsQualityAnalysis(
                requestTicker,
                period.period_end,
                period.period_type,
                adminKey,
                controller.signal,
            );
            if (!controller.signal.aborted) setEarningsQuality((current) => attachEarningsAnalysis(current, analysis));
            while (!controller.signal.aborted && (analysis.status === "queued" || analysis.status === "running")) {
                await waitForPoll(1_500, controller.signal);
                analysis = await fetchEarningsQualityAnalysis(requestTicker, analysis.id, adminKey, controller.signal);
                if (!controller.signal.aborted) setEarningsQuality((current) => attachEarningsAnalysis(current, analysis));
            }
            if (!controller.signal.aborted) {
                await Promise.all([
                    loadEarningsQuality(requestTicker),
                    loadDecision(requestTicker, personal.adminKey),
                ]);
            }
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401) {
                handlePersonalUnauthorized();
                return;
            }
            if (!controller.signal.aborted) setEarningsQualityError(caught instanceof Error ? caught.message : "Filing analysis could not be completed.");
        } finally {
            if (!controller.signal.aborted) setEarningsQualityBusyPeriod(null);
        }
    }, [handlePersonalUnauthorized, loadDecision, loadEarningsQuality, personal.adminKey, ticker]);

    const showFinancialEvidence = async (metric: DecisionWarning["evidence_metric"]) => {
        const metricMap: Record<DecisionWarning["evidence_metric"], FinancialEvidenceMetric> = {
            revenue: "revenue",
            gross_margin: "gross_margin",
            operating_margin: "operating_margin",
            fcf: "free_cash_flow",
            cash: "cash_and_short_term_investments",
            debt: "total_debt",
            debt_to_equity: "debt_to_equity",
            shares: "shares_outstanding",
        };
        pendingEvidenceFocusRef.current = true;
        setCockpitView("risks");
        selectSection("financials");
        setFinancialMetric(metricMap[metric]);
        if (financialPeriod !== "quarterly") await handlePeriodChange("quarterly");
    };

    const handleCockpitViewChange = (view: CockpitTab) => {
        setCockpitView(view);
        if (view === "overview") selectSection("overview");
        else if (view === "valuation" || view === "peers") selectSection("valuation");
        else if (view === "risks") selectSection("financials");
        else selectSection("events");
    };

    const latest = stockData?.historical_data.filter((point) => point.close != null).at(-1);
    const previous = stockData?.historical_data.filter((point) => point.close != null).at(-2);
    const change = latest?.close != null && previous?.close != null ? latest.close - previous.close : null;
    const changePct = change != null && previous?.close ? change / previous.close : null;

    return (
        <div className="flex h-full w-full overflow-hidden bg-[var(--app-bg)]">
            <div className="hidden h-full xl:block">
                <WatchlistSidebar currentTicker={ticker} onSelectTicker={selectTicker} watchlist={watchlist} onAdd={addToWatchlist} onRemove={removeFromWatchlist} readOnly={!personal.isUnlocked} onUnlock={() => setUnlockOpen(true)} />
            </div>

            <div className="app-page analysis-workspace min-w-0 flex-1">
                <div className="page-container">
                    <WatchlistSidebar compact currentTicker={ticker} onSelectTicker={selectTicker} watchlist={watchlist} onAdd={addToWatchlist} onRemove={removeFromWatchlist} readOnly={!personal.isUnlocked} onUnlock={() => setUnlockOpen(true)} />

                    {!ticker && (
                        <section className="surface-panel flex min-h-[65vh] flex-col items-center justify-center px-6 py-16 text-center">
                            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-300"><Search size={26} /></span>
                            <p className="eyebrow mt-6">Single-security workspace</p>
                            <h1 className="mt-2 text-3xl font-black tracking-[-0.04em] sm:text-4xl">Start with a ticker</h1>
                            <p className="mt-3 max-w-xl text-sm leading-6 text-slate-500 sm:text-base">Review prices, fundamentals, quality-gated point-in-time factors, news, and an optional AI synthesis in one traceable workspace.</p>
                            <div className="mt-7 flex flex-wrap justify-center gap-2">
                                {watchlist.slice(0, 6).map((symbol) => <button key={symbol} type="button" className="secondary-button font-mono" onClick={() => selectTicker(symbol)}>{symbol.replace(".US", "")}</button>)}
                            </div>
                        </section>
                    )}

                    {loading && !stockData && (
                        <div className="grid gap-5" aria-label="Loading security analysis">
                            <div className="surface-panel h-64 animate-pulse bg-slate-100 dark:bg-slate-800" />
                            <div className="grid gap-5 xl:grid-cols-2"><div className="surface-panel h-96 animate-pulse bg-slate-100 dark:bg-slate-800" /><div className="surface-panel h-96 animate-pulse bg-slate-100 dark:bg-slate-800" /></div>
                            <p className="text-center text-sm text-slate-500">Loading cached data or synchronizing the latest available snapshot…</p>
                        </div>
                    )}

                    {error && !stockData && (
                        <div className="error-panel flex min-h-[320px] flex-col items-center justify-center text-center" role="alert">
                            <AlertCircle size={34} /><h2 className="mt-4 text-lg font-black">Analysis unavailable</h2><p className="mt-2 max-w-lg">{error}</p>
                            <button type="button" className="secondary-button mt-5" onClick={() => void loadStock(ticker, chartInterval, financialPeriod, true)}>Try again</button>
                        </div>
                    )}

                    {stockData && (
                        <>
                            {error && <div className="error-panel" role="alert">{error} The previous snapshot remains visible.</div>}
                            <section className="stock-context-header" aria-label="Stock identity">
                                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start sm:gap-5">
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-3">
                                            <span className="hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[var(--brand-soft)] text-lg font-semibold text-[var(--brand-strong)] sm:flex" aria-hidden="true">{stockData.profile.ticker.split(".")[0].slice(0, 1)}</span>
                                            <div className="min-w-0">
                                                <h1 className="break-words text-2xl font-semibold tracking-[-0.035em] sm:text-3xl">{stockData.profile.name || stockData.profile.ticker}</h1>
                                                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-muted)]">
                                                    <span className="font-semibold text-[var(--brand-strong)]">{stockData.profile.ticker}</span>
                                                    {stockData.profile.exchange && <span>{stockData.profile.exchange}</span>}
                                                    {stockData.profile.sector && <span>{stockData.profile.sector}</span>}
                                                    {stockData.profile.description && <button type="button" className="inline-flex min-h-7 items-center gap-1 hover:text-[var(--text)]" aria-expanded={descriptionExpanded} aria-controls="company-description" onClick={() => setDescriptionExpanded((expanded) => !expanded)}>
                                                        About {descriptionExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                                                    </button>}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex shrink-0 items-center justify-between gap-4 sm:items-start sm:gap-5">
                                        <div className="sm:text-right">
                                            <div className="flex items-baseline gap-1.5 sm:justify-end"><span className="text-xs text-[var(--text-muted)]">{stockData.profile.currency || "USD"}</span><span className="text-2xl font-semibold tracking-tight sm:text-[28px]">{latest?.close?.toFixed(2) ?? "—"}</span></div>
                                            {change != null && <p className={`mt-0.5 flex items-center gap-1 text-sm font-medium sm:justify-end ${change > 0 ? "text-[var(--positive)]" : change < 0 ? "text-[var(--negative)]" : "text-[var(--neutral)]"}`}>{change > 0 ? <ArrowUpRight size={14} /> : change < 0 ? <ArrowDownRight size={14} /> : null}{change > 0 ? "+" : change < 0 ? "−" : ""}{Math.abs(change).toFixed(2)} {changePct == null ? "" : `(${formatSignedPercent(changePct)})`}</p>}
                                            <p className="mt-1 text-xs text-[var(--text-muted)]">Adjusted close · {latest?.date || "Date unavailable"}</p>
                                        </div>
                                        {watchlist.includes(stockData.profile.ticker) ? <span className="research-link"><Check size={15} /> In watchlist</span> : <button type="button" onClick={() => addToWatchlist(stockData.profile.ticker)} className="secondary-button min-h-9 shrink-0 px-3 py-1.5 text-xs"><Plus size={14} /> {personal.isUnlocked ? "Watchlist" : "Unlock to add"}</button>}
                                    </div>
                                </div>
                                {descriptionExpanded && <div id="company-description" className="mt-3 border-t pt-3 text-sm leading-6 text-[var(--text-muted)]">{stockData.profile.description}{stockData.profile.industry && <p className="mt-2 text-xs">Industry · {stockData.profile.industry}</p>}</div>}
                            </section>

                            <AnalysisNavigation active={activeSection} onChange={selectSection} />

                            <div id={`analysis-panel-${activeSection}`} role="tabpanel" aria-labelledby={`analysis-tab-${activeSection}`} className="space-y-4">
                                {activeSection === "valuation" && <div className="flex flex-wrap items-center justify-between gap-3">
                                    <button type="button" className="secondary-button text-xs" onClick={() => selectSection("technical")}>Historical multiples →</button>
                                    <SegmentedControl<CockpitTab>
                                        label="Valuation section"
                                        value={cockpitView === "peers" ? "peers" : "valuation"}
                                        options={[{ value: "valuation", label: "Scenarios & sensitivity" }, { value: "peers", label: "Peer benchmarks" }]}
                                        onChange={handleCockpitViewChange}
                                    />
                                </div>}

                                <div className={["overview", "valuation", "financials", "events"].includes(activeSection) ? "" : "hidden"}>
                                    <DecisionCockpit
                                        ticker={stockData.profile.ticker}
                                        decision={decision}
                                        loading={decisionLoading}
                                        error={decisionError}
                                        adminKey={personal.adminKey}
                                        onUnlock={() => setUnlockOpen(true)}
                                        onUnauthorized={personal.handleUnauthorized}
                                        onRetry={() => void refreshDecision()}
                                        onRefresh={refreshDecision}
                                        onShowEvidence={(metric) => void showFinancialEvidence(metric)}
                                        earningsQuality={earningsQuality}
                                        earningsQualityLoading={earningsQualityLoading}
                                        earningsQualityError={earningsQualityError}
                                        earningsQualityBusyPeriod={earningsQualityBusyPeriod}
                                        onAnalyzeEarningsPeriod={analyzeEarningsPeriod}
                                        eventsExpectations={eventsExpectations}
                                        eventsExpectationsLoading={eventsExpectationsLoading}
                                        eventsExpectationsError={eventsExpectationsError}
                                        activeView={cockpitView}
                                        onViewChange={handleCockpitViewChange}
                                        hideNavigation
                                        overviewContent={<>
                                            <div className="overview-grid">
                                                <section className="research-panel overflow-hidden" aria-labelledby="overview-price-title" data-testid="overview-price-panel">
                                                    <header className="flex flex-wrap items-start justify-between gap-2 px-4 pb-2 pt-4 sm:px-5">
                                                        <div><h2 id="overview-price-title" className="text-lg font-semibold tracking-tight">Price &amp; volume</h2><p className="mt-1 text-xs text-[var(--text-muted)]">Adjusted · logarithmic · {stockData.profile.currency || "USD"}</p></div>
                                                        <SegmentedControl label="Price interval" value={chartInterval} options={[{ value: "1d", label: "Daily", disabled: chartLoading }, { value: "1wk", label: "Weekly", disabled: chartLoading }, { value: "1mo", label: "Monthly", disabled: chartLoading }]} onChange={(interval) => void handleIntervalChange(interval)} />
                                                    </header>
                                                    <div className="px-2 pb-2 sm:px-3"><OverviewStockChart data={stockData.historical_data} interval={chartInterval} isLoading={chartLoading} height={260} embedded /></div>
                                                </section>
                                                <FinancialSnapshot stock={stockData} snapshot={marketSnapshot} statementDate={decision?.metadata.financial_statement_date || marketSnapshot?.source_dates.financials} onDetails={() => selectSection("financials")} />
                                            </div>
                                            <div className="flex flex-wrap gap-x-5 gap-y-2 rounded-lg border px-4 py-3 text-xs text-[var(--text-muted)]" aria-label="Data sources">
                                                <span className="font-medium text-[var(--text)]">Data sources</span>
                                                <span>Price · {latest?.date || "Unavailable"}</span>
                                                <span>Financials · {decision?.metadata.financial_statement_date || marketSnapshot?.source_dates.financials || "Unavailable"}</span>
                                                <span>Factors · {factorLoading ? "Loading" : factorError ? "Unavailable" : factorSnapshot?.as_of_date || "Unavailable"}</span>
                                            </div>
                                        </>}
                                    />
                                </div>

                                {activeSection === "overview" && <details className="research-details">
                                    <summary>All company metrics · {marketSnapshot ? `${marketSnapshot.coverage.available}/${marketSnapshot.coverage.total} available` : marketSnapshotLoading ? "Loading" : "Unavailable"}</summary>
                                    <StockSnapshotPanel data={marketSnapshot} loading={marketSnapshotLoading} error={marketSnapshotError} onRetry={() => void loadMarketSnapshot(stockData.profile.ticker)} />
                                </details>}

                                {activeSection === "financials" && <>
                                    <FinancialFlowPanel
                                        data={financialFlow}
                                        loading={financialFlowLoading}
                                        error={financialFlowError}
                                        timePeriod={financialPeriod}
                                        onTimePeriodChange={(period) => void handlePeriodChange(period)}
                                        onPeriodEndChange={(periodEnd) => {
                                            if (financialPeriod !== "ttm") void loadFinancialFlow(stockData.profile.ticker, financialPeriod, periodEnd);
                                        }}
                                        onRetry={() => {
                                            if (financialPeriod !== "ttm") void loadFinancialFlow(stockData.profile.ticker, financialPeriod, financialFlowRequestedPeriodRef.current);
                                        }}
                                    />
                                    <div ref={financialEvidenceRef} tabIndex={-1} className="scroll-mt-20 rounded-2xl focus:outline-none">
                                        <FinancialTrendChart data={stockData.historical_financials} ttmData={stockData.valuation_metrics?.ttm} currentPrice={stockData.valuation_metrics?.valuation.current_price ?? undefined} timePeriod={financialPeriod} onTimePeriodChange={handlePeriodChange} selectedMetric={financialMetric} onMetricChange={setFinancialMetric} earningsQuality={earningsQuality} dataQualityWarnings={stockData.valuation_metrics?.data_quality_warnings} />
                                    </div>
                                </>}

                                {activeSection === "technical" && <>
                                    <StockValuationChart key={stockData.profile.ticker} data={stockData.historical_data} history={stockData.valuation_history} interval={chartInterval} onIntervalChange={handleIntervalChange} isLoading={chartLoading} />
                                    <PointInTimeFactorPanel snapshot={factorSnapshot} loading={factorLoading} error={factorError} />
                                </>}

                                {activeSection === "events" && <div className="min-h-[420px]"><NewsFeed ticker={stockData.profile.ticker} /></div>}
                            </div>

                            <footer className="flex flex-wrap items-center gap-2 rounded-xl border px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
                                <ShieldCheck size={14} className="text-emerald-500" /> Published factor values are versioned and quality-gated. Cockpit calculations are transparent decision support, not investment advice.
                            </footer>
                        </>
                    )}
                </div>
            </div>
            <PersonalUnlockDialog
                open={unlockOpen}
                loading={personal.unlocking || personal.restoring}
                error={personal.error}
                onClose={() => setUnlockOpen(false)}
                onUnlock={(key) => personal.unlock(key)}
            />
        </div>
    );
}

export default function Home() {
    return <Suspense fallback={<div className="h-full animate-pulse bg-[var(--app-bg)]" />}><AnalysisPage /></Suspense>;
}
