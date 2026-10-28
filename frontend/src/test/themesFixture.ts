import type {
    ThemeBriefResponse,
    ThemeDetailResponse,
    ThemeHistory,
    ThemeListItem,
    ThemeListResponse,
    ThemeMemberRow,
    ThemeProposalItem,
    ThemeProposalsResponse,
    ThemeTrendsMeta,
    TrendingCompaniesResponse,
    TrendingCompany,
} from "@/lib/api";

// Shapes mirror the Theme* models in api/schemas.py; returns are fractions (0.05 == +5%).

export const THEME_AS_OF = "2026-10-09";

export function makeThemeMeta(overrides: Partial<ThemeTrendsMeta> = {}): ThemeTrendsMeta {
    return {
        as_of_date: THEME_AS_OF,
        expected_as_of_date: THEME_AS_OF,
        published_at: "2026-10-10T08:55:00Z",
        stale: false,
        scoring_version: "theme-heat-v1",
        taxonomy_version: "themes-v1",
        point_in_time: true,
        attention_degraded: false,
        coverage_pct: 98.4,
        warnings: [],
        ...overrides,
    };
}

export function makeThemeListItem(overrides: Partial<ThemeListItem> = {}): ThemeListItem {
    return {
        slug: "ai-compute",
        name_en: "AI compute",
        name_zh: "AI算力",
        megatrend: "artificial-intelligence",
        stage: "leading",
        candidate_stage: "leading",
        stage_changed: false,
        heat: 81.2,
        heat_now: 78.4,
        heat_trend: 71.9,
        rank_now: 1,
        rank_trend: 2,
        rank_change: 2,
        streak_top5: 14,
        score_price: 1.84,
        score_breadth: 0.92,
        score_volume: 0.41,
        score_attention: -0.35,
        excess_5d: 0.0312,
        excess_21d: 0.0845,
        excess_63d: 0.1621,
        pct_above_ma50: 0.82,
        near_high_share: 0.47,
        pct_above_ma50_change_10d: 0.12,
        ignitions_today: 1,
        ignitions_5d: 4,
        n_members: 24,
        n_priced: 23,
        leaders: [
            { ticker: "NVDA.US", name: "NVIDIA Corporation" },
            { ticker: "AVGO.US", name: "Broadcom Inc." },
            { ticker: "MU.US", name: "Micron Technology, Inc." },
        ],
        proxy_etf: "SMH.US",
        proxy_excess_21d: 0.0611,
        heat_sparkline: [62.1, 65.4, null, 70.2, 74.8, 78.4],
        emerging: false,
        attention_only: false,
        attention_degraded: false,
        point_in_time: true,
        ...overrides,
    };
}

/** A theme without enough priced members: every nullable metric is missing and must render "—". */
export function makeInsufficientTheme(overrides: Partial<ThemeListItem> = {}): ThemeListItem {
    return makeThemeListItem({
        slug: "space-economy",
        name_en: "Space economy",
        name_zh: "太空经济",
        megatrend: null,
        stage: "insufficient",
        candidate_stage: "insufficient",
        heat: null,
        heat_now: null,
        heat_trend: null,
        rank_now: null,
        rank_trend: null,
        rank_change: null,
        streak_top5: 0,
        score_price: null,
        score_breadth: null,
        score_volume: null,
        score_attention: null,
        excess_5d: null,
        excess_21d: null,
        excess_63d: null,
        pct_above_ma50: null,
        near_high_share: null,
        pct_above_ma50_change_10d: null,
        ignitions_today: 0,
        ignitions_5d: 0,
        n_members: 3,
        n_priced: 2,
        leaders: [],
        proxy_etf: null,
        proxy_excess_21d: null,
        heat_sparkline: [null, null],
        ...overrides,
    });
}

export function makeThemeList(): ThemeListItem[] {
    return [
        makeThemeListItem(),
        makeThemeListItem({
            slug: "nuclear-power",
            name_en: "Nuclear power",
            name_zh: "核电",
            megatrend: "energy-transition",
            stage: "crowded",
            candidate_stage: "crowded",
            heat_now: 72.6,
            heat_trend: 74.3,
            rank_now: 2,
            rank_trend: 1,
            rank_change: -1,
            streak_top5: 31,
            excess_5d: -0.0124,
            excess_21d: 0.0518,
            leaders: [{ ticker: "CEG.US", name: "Constellation Energy Corporation" }],
            proxy_etf: "NLR.US",
            proxy_excess_21d: 0.0402,
        }),
        makeThemeListItem({
            slug: "quantum-computing",
            name_en: "Quantum computing",
            name_zh: "量子计算",
            megatrend: "frontier-computing",
            stage: "emerging",
            candidate_stage: "emerging",
            stage_changed: true,
            emerging: true,
            attention_only: true,
            heat_now: 66.1,
            heat_trend: 54.0,
            rank_now: 3,
            rank_trend: 6,
            rank_change: 4,
            streak_top5: 2,
            leaders: [{ ticker: "IONQ.US", name: "IonQ, Inc." }],
            proxy_etf: "QTUM.US",
            proxy_excess_21d: 0.0215,
        }),
        makeInsufficientTheme(),
    ];
}

export function makeThemesListFixture({
    meta = {},
    themes = makeThemeList(),
    view = "now",
    summary = {},
}: {
    meta?: Partial<ThemeTrendsMeta>;
    themes?: ThemeListItem[];
    view?: ThemeListResponse["view"];
    summary?: Partial<ThemeListResponse["summary"]>;
} = {}): ThemeListResponse {
    return {
        meta: makeThemeMeta(meta),
        view,
        summary: {
            ranked: 3,
            emerging: 1,
            accelerating: 0,
            leading: 1,
            crowded: 1,
            fading: 0,
            top_riser: "quantum-computing",
            ...summary,
        },
        themes,
    };
}

const sessionDates = (length: number) => {
    const dates: string[] = [];
    const cursor = new Date(Date.UTC(2026, 9, 9));
    while (dates.length < length) {
        const day = cursor.getUTCDay();
        if (day !== 0 && day !== 6) dates.unshift(cursor.toISOString().slice(0, 10));
        cursor.setUTCDate(cursor.getUTCDate() - 1);
    }
    return dates;
};

const STAGE_RUNS: ThemeHistory["stage"] = ["neutral", "accelerating", "leading", "leading", "crowded", "fading"];

/** Bounded, deterministic daily series; short histories keep a neutral → accelerating → leading run. */
export function makeThemeHistory(length = 6, overrides: Partial<ThemeHistory> = {}): ThemeHistory {
    const range = Array.from({ length }, (_, index) => index);
    const progress = (index: number) => index / Math.max(length - 1, 1);
    const wave = (index: number, amplitude: number, period: number) => amplitude * Math.sin(index / period);
    const run = Math.max(2, Math.floor(length / STAGE_RUNS.length));
    return {
        dates: sessionDates(length),
        heat: range.map((index) => 56 + wave(index, 22, 9) + 10 * progress(index) + wave(index, 3, 2)),
        heat_now: range.map((index) => 55 + wave(index, 22, 9) + 10 * progress(index)),
        heat_trend: range.map((index) => 54 + wave(index - 6, 12, 14) + 8 * progress(index)),
        score_price: range.map((index) => (index === 1 ? null : 0.5 + wave(index, 1.2, 10))),
        score_breadth: range.map((index) => 0.6 + wave(index, 0.4, 12)),
        score_volume: range.map((index) => -0.2 + wave(index, 0.3, 5)),
        score_attention: range.map((index) => (index === length - 1 ? null : 0.3 + wave(index, 0.5, 7))),
        basket_index: range.map((index) => 1 + 0.12 * progress(index) + wave(index, 0.02, 6)),
        benchmark_index: range.map((index) => 1 + 0.05 * progress(index) + wave(index, 0.01, 8)),
        proxy_index: range.map((index) => (index === 2 ? null : 1 + 0.09 * progress(index) + wave(index, 0.015, 5))),
        pct_above_ma50: range.map((index) => 0.6 + wave(index, 0.25, 11)),
        near_high_share: range.map((index) => 0.4 + wave(index, 0.2, 9)),
        ignitions: range.map((index) => (index % 7 === 3 ? 2 : index % 5 === 0 ? 1 : 0)),
        stage: range.map((index) => STAGE_RUNS[Math.floor(index / run) % STAGE_RUNS.length]),
        rank_now: range.map((index) => Math.max(1, Math.round(6 - 5 * progress(index)))),
        point_in_time: range.map((index) => index > 0),
        ...overrides,
    };
}

export function makeThemeMember(overrides: Partial<ThemeMemberRow> = {}): ThemeMemberRow {
    return {
        ticker: "NVDA.US",
        name: "NVIDIA Corporation",
        market_cap: 4_700_000_000_000,
        tier: "core",
        weight: 0.15,
        exposure: 3,
        role: "pure_play",
        review_status: "verified",
        sources: ["seed", "etf:SMH.US"],
        evidence_quote: "accelerated computing platforms for data centers",
        evidence_source: "EODHD profile",
        evidence_as_of: "2026-10-04",
        close: 192.31,
        return_1d: 0.021,
        excess_5d: 0.0412,
        excess_21d: 0.1033,
        near_high_ratio: 0.97,
        relative_volume_5d: 1.34,
        attention_z: 2.4,
        ignited: true,
        first_ignition_date: "2026-09-22",
        leader_score: 0.91,
        leader_rank: 1,
        is_leader: true,
        sparkline: [1, 1.02, null, 1.05, 1.08],
        ...overrides,
    };
}

export function makeThemeBrief(overrides: Partial<ThemeBriefResponse> = {}): ThemeBriefResponse {
    return {
        date: THEME_AS_OF,
        model: "deepseek-chat",
        prompt_version: "theme-brief-v1",
        created_at: "2026-10-10T09:05:00Z",
        stale: false,
        content: [
            "**盘面事实**",
            "",
            "- 主题篮子X21为+8.5%，跑赢SPY。[E1]",
            "",
            "**同期新闻**",
            "",
            "- 同期新闻包括数据中心资本开支上调。[E2]",
        ].join("\n"),
        evidence: [
            { id: "E1", kind: "metric", label: "Basket X21 +8.5%", source_date: THEME_AS_OF, link: null },
            { id: "E2", kind: "news", label: "Hyperscalers lift data center capex", source_date: "2026-10-08", link: "https://example.com/capex" },
            { id: "E3", kind: "news", label: "Unsafe link is dropped", source_date: "2026-10-08", link: "javascript:alert(1)" },
        ],
        ...overrides,
    };
}

export function makeThemeDetailFixture(overrides: Partial<ThemeDetailResponse> = {}): ThemeDetailResponse {
    return {
        meta: makeThemeMeta(),
        theme: makeThemeListItem(),
        definition: "Companies that design, manufacture or supply accelerated computing for AI training and inference.",
        keywords: ["GPU", "accelerator", "HBM"],
        proxy_etfs: ["SMH.US", "SOXX.US"],
        history: makeThemeHistory(),
        members: [
            makeThemeMember(),
            makeThemeMember({
                ticker: "SMCI.US",
                name: null,
                market_cap: null,
                tier: "peripheral",
                weight: 0.04,
                exposure: 1,
                role: null,
                review_status: "etf_only",
                sources: ["etf:SMH.US"],
                evidence_quote: null,
                evidence_source: null,
                evidence_as_of: null,
                close: null,
                return_1d: null,
                excess_5d: null,
                excess_21d: null,
                near_high_ratio: null,
                relative_volume_5d: null,
                attention_z: null,
                ignited: false,
                first_ignition_date: null,
                leader_score: null,
                leader_rank: null,
                is_leader: false,
                sparkline: [],
            }),
        ],
        brief: makeThemeBrief(),
        ...overrides,
    };
}

export function makeTrendingCompany(overrides: Partial<TrendingCompany> = {}): TrendingCompany {
    return {
        rank: 1,
        ticker: "IONQ.US",
        name: "IonQ, Inc.",
        sector: "Technology",
        industry: "Computer Hardware",
        score: 2.31,
        close: 48.2,
        market_cap: 12_400_000_000,
        return_1d: 0.083,
        excess_5d: 0.214,
        excess_5d_vs_sector: 0.188,
        relative_volume_5d: 2.6,
        attention_z: 3.1,
        near_high_ratio: 0.94,
        themes: ["quantum-computing"],
        flags: ["ignited"],
        ...overrides,
    };
}

export function makeTrendingCompaniesFixture(
    meta: Partial<ThemeTrendsMeta> = {},
    companies: TrendingCompany[] = [
        makeTrendingCompany(),
        makeTrendingCompany({
            rank: 2,
            ticker: "XYZ.US",
            name: null,
            sector: null,
            industry: null,
            score: 1.12,
            close: null,
            market_cap: null,
            return_1d: null,
            excess_5d: -0.021,
            excess_5d_vs_sector: null,
            relative_volume_5d: null,
            attention_z: null,
            near_high_ratio: null,
            themes: [],
            flags: ["attention_only"],
        }),
    ],
): TrendingCompaniesResponse {
    return { meta: makeThemeMeta(meta), companies };
}

export function makeThemeProposal(overrides: Partial<ThemeProposalItem> = {}): ThemeProposalItem {
    return {
        id: 7,
        label_en: "Humanoid robots",
        label_zh: "人形机器人",
        slug_suggestion: "humanoid-robots",
        rationale: "Actuator and vision suppliers moved together on humanoid robot launch headlines [E4].",
        tickers: ["TSLA.US", "HSAI.US", "RR.US"],
        // Same order as the worker's cluster_evidence: company profiles (no link) first, then headlines.
        evidence: [
            { id: "E1", kind: "company", label: "TSLA.US Tesla, Inc.", source_date: "2026-10-09", value: { ticker: "TSLA.US", name: "Tesla, Inc." } },
            { id: "E2", kind: "company", label: "HSAI.US Hesai Group", source_date: "2026-10-09", value: { ticker: "HSAI.US", name: "Hesai Group" } },
            { id: "E3", kind: "company", label: "RR.US Richtech Robotics Inc.", source_date: "2026-10-09", value: { ticker: "RR.US", name: "Richtech Robotics Inc." } },
            { id: "E4", kind: "news", label: "Humanoid robot demo day", source_date: "2026-10-08", link: "https://example.com/robots", value: { headline: "Humanoid robot demo day", publisher: "example.com" } },
        ],
        first_seen: "2026-09-26",
        last_seen: "2026-10-09",
        hit_count: 2,
        status: "pending",
        ...overrides,
    };
}

export function makeThemeProposalsFixture(proposals: ThemeProposalItem[] = [makeThemeProposal()]): ThemeProposalsResponse {
    return { proposals };
}
