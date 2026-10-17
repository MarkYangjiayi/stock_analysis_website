import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { Clock3, ExternalLink, FileText } from "lucide-react";

import { SectionHeader } from "@/components/ui/Panel";
import type { ThemeBriefResponse } from "@/lib/api";

const CITATION = /\[(E\d+)\](?!\()/g;

const evidenceAnchor = (id: string) => `theme-evidence-${id}`;

/** Turn bare [E#] citations into in-page links to the evidence list. */
const linkCitations = (content: string) => content.replace(CITATION, (_, id: string) => `[${id}](#${evidenceAnchor(id)})`);

const safeExternal = (href: string | null | undefined) => {
    if (!href) return null;
    try {
        const url = new URL(href);
        return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
    } catch {
        return null;
    }
};

const EVIDENCE_HREF = /^#theme-evidence-E\d+$/;

/**
 * Brief text is LLM output, so only the citation anchors we inserted become links;
 * any other link in the content renders as plain text. Sources are linked from the
 * validated evidence list instead.
 */
function BriefLink({ href, children }: { href?: string; children?: ReactNode }) {
    if (href && EVIDENCE_HREF.test(href)) {
        return <a href={href} className="rounded bg-accent-soft px-1 font-mono text-xs font-semibold text-accent-strong no-underline hover:underline">{children}</a>;
    }
    return <span>{children}</span>;
}

function EvidenceList({ items }: { items: ThemeBriefResponse["evidence"] }) {
    return (
        <ol className="mt-2 space-y-1.5 text-sm">
            {items.map((item) => {
                const link = safeExternal(item.link);
                return (
                    <li key={item.id} id={evidenceAnchor(item.id)} className="flex min-w-0 scroll-mt-24 gap-2 rounded-md px-2 py-1.5 target:bg-accent-soft">
                        <span className="w-8 shrink-0 font-mono text-xs font-semibold text-accent-strong">{item.id}</span>
                        <span className="min-w-0 flex-1">
                            <span className="block break-words text-fg">{item.label}</span>
                            <span className="font-mono text-xs text-fg-muted">
                                {item.kind}{item.source_date ? ` · ${item.source_date}` : ""}
                            </span>
                        </span>
                        {link && (
                            <a href={link} target="_blank" rel="noopener noreferrer" className="shrink-0 pt-0.5 text-accent-strong" aria-label={`Open source for ${item.id}`}>
                                <ExternalLink size={14} />
                            </a>
                        )}
                    </li>
                );
            })}
        </ol>
    );
}

/** IDs the brief text actually cites, in first-citation order. */
const citedIds = (content: string) => [...new Set([...content.matchAll(CITATION)].map((match) => match[1]))];

/** Model name the worker stores when the AI brief failed validation and a template stands in. */
export const TEMPLATE_BRIEF_MODEL = "template";

export default function ThemeBriefCard({ brief }: { brief: ThemeBriefResponse | null }) {
    const template = brief?.model === TEMPLATE_BRIEF_MODEL;
    const citedOrder = brief ? citedIds(brief.content) : [];
    const byId = new Map((brief?.evidence ?? []).map((item) => [item.id, item]));
    const cited = citedOrder.flatMap((id) => (byId.has(id) ? [byId.get(id)!] : []));
    const uncited = (brief?.evidence ?? []).filter((item) => !citedOrder.includes(item.id));
    return (
        <section className="surface-panel min-w-0 overflow-hidden" aria-label="Why it's moving">
            <SectionHeader
                eyebrow={template ? "Template brief · Chinese" : "Validated brief · Chinese"}
                title="Why it's moving"
                description={template
                    ? "The AI brief did not pass validation, so this summary restates the cited evidence verbatim; the next run retries the AI brief."
                    : "Generated from published metrics and same-period headlines; every sentence cites one evidence item below."}
                action={brief && (
                    <span className={brief.stale
                        ? "inline-flex items-center gap-1.5 rounded-md border border-caution/30 bg-caution/6 px-2 py-0.5 font-mono text-xs font-medium text-caution"
                        : "status-pill"}
                    >
                        <Clock3 size={13} aria-hidden="true" /> {brief.stale ? `Stale · brief from ${brief.date}` : `Brief for ${brief.date}`}
                    </span>
                )}
            />
            {brief ? (
                <div className="grid min-w-0 gap-5 p-4 sm:p-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(280px,1fr)]">
                    <div>
                        {brief.stale && (
                            <p className="mb-3 text-sm text-caution" role="status">This brief was written for an earlier session and may not reflect the latest data.</p>
                        )}
                        <div lang="zh-CN" className="prose prose-sm max-w-none text-fg dark:prose-invert prose-headings:font-semibold prose-strong:text-fg prose-a:text-accent-strong">
                            <ReactMarkdown components={{ a: BriefLink, img: () => null }}>{linkCitations(brief.content)}</ReactMarkdown>
                        </div>
                        <p className="mt-4 font-mono text-xs text-fg-muted">{brief.model} · {brief.prompt_version}</p>
                    </div>
                    <div className="min-w-0">
                        <h3 className="eyebrow">Cited evidence</h3>
                        {cited.length ? <EvidenceList items={cited} /> : <p className="mt-2 text-sm text-fg-muted">—</p>}
                        {uncited.length > 0 && (
                            <details className="mt-3 text-sm">
                                <summary className="cursor-pointer font-mono text-xs text-fg-muted hover:text-fg">
                                    Other evidence the brief could cite ({uncited.length})
                                </summary>
                                <EvidenceList items={uncited} />
                            </details>
                        )}
                    </div>
                </div>
            ) : (
                <div className="flex min-h-[180px] flex-col items-center justify-center p-6 text-center">
                    <FileText className="text-fg-muted" size={26} aria-hidden="true" />
                    <p className="mt-3 font-semibold text-fg">No validated brief for this theme yet</p>
                    <p className="mt-1 max-w-md text-sm text-fg-muted">Briefs are written after each publication for the hottest themes and for any that change stage.</p>
                </div>
            )}
        </section>
    );
}
