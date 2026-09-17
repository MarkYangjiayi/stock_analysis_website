import Link from "next/link";

type MarketTab = "overview" | "rotation" | "rates" | "valuation";

const TABS: Array<{ id: MarketTab; href: string; label: string }> = [
    { id: "overview", href: "/market", label: "Overview" },
    { id: "rotation", href: "/rrg", label: "Rotation" },
    { id: "rates", href: "/market/yield-curve", label: "Yield Curve" },
    { id: "valuation", href: "/market/index-valuation", label: "Valuation" },
];

export default function MarketTabs({ active }: { active: MarketTab }) {
    return (
        <nav className="flex w-fit rounded-xl border bg-white p-1 dark:bg-[#121920]" aria-label="Market views">
            {TABS.map((tab) => (
                <Link
                    key={tab.id}
                    href={tab.href}
                    aria-current={active === tab.id ? "page" : undefined}
                    className={`rounded-lg px-4 py-2 text-sm font-bold transition-colors ${
                        active === tab.id
                            ? "bg-emerald-600 text-white shadow-sm"
                            : "text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
                    }`}
                >
                    {tab.label}
                </Link>
            ))}
        </nav>
    );
}
