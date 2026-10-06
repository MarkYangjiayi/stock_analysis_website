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
        <nav className="flex w-fit rounded-lg border bg-surface p-1" aria-label="Market views">
            {TABS.map((tab) => (
                <Link
                    key={tab.id}
                    href={tab.href}
                    aria-current={active === tab.id ? "page" : undefined}
                    className={`rounded-md px-4 py-2 text-sm font-semibold transition-colors ${
                        active === tab.id
                            ? "bg-accent-soft text-accent-strong"
                            : "text-fg-muted hover:bg-surface-muted hover:text-fg"
                    }`}
                >
                    {tab.label}
                </Link>
            ))}
        </nav>
    );
}
