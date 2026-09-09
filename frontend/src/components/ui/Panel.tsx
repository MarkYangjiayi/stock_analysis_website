import type { HTMLAttributes, ReactNode } from "react";

type PanelProps = HTMLAttributes<HTMLElement> & {
    as?: "section" | "article" | "div";
    children: ReactNode;
};

export function Panel({ as: Component = "section", className = "", children, ...props }: PanelProps) {
    return <Component className={`surface-panel ${className}`} {...props}>{children}</Component>;
}

export function SectionHeader({
    eyebrow,
    title,
    description,
    action,
    className = "",
}: {
    eyebrow?: string;
    title: string;
    description?: string;
    action?: ReactNode;
    className?: string;
}) {
    return (
        <header className={`section-header ${className}`}>
            <div className="min-w-0">
                {eyebrow && <p className="eyebrow">{eyebrow}</p>}
                <h2 className="section-title">{title}</h2>
                {description && <p className="section-description">{description}</p>}
            </div>
            {action && <div className="shrink-0">{action}</div>}
        </header>
    );
}
