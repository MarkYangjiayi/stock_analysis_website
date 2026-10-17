"use client";

import { useState, type ReactNode } from "react";
import { Loader2, LockKeyhole } from "lucide-react";

import PersonalUnlockDialog from "@/components/PersonalUnlockDialog";
import { usePersonalWorkspace } from "@/hooks/usePersonalWorkspace";

export interface ThemePageHeaderProps {
    eyebrow: string;
    title: string;
    description?: ReactNode;
    aside?: ReactNode;
    children?: ReactNode;
}

export function ThemePageHeader({ eyebrow, title, description, aside, children }: ThemePageHeaderProps) {
    return (
        <header className="flex flex-col justify-between gap-4 border-b pb-5 xl:flex-row xl:items-end">
            <div className="min-w-0">
                <p className="eyebrow">{eyebrow}</p>
                <h1 className="page-title mt-1">{title}</h1>
                {description && <p className="page-description">{description}</p>}
                {children}
            </div>
            {aside && <div className="flex flex-wrap gap-2 xl:justify-end">{aside}</div>}
        </header>
    );
}

export interface ThemeAccess {
    adminKey: string;
    onUnauthorized: () => void;
}

/**
 * Theme data is private to the personal workspace: nothing is fetched until the Admin Key is
 * restored or entered, and a rejected key (401) drops back to this locked state.
 */
export default function ThemeAccessGate({
    header,
    children,
}: {
    header: Omit<ThemePageHeaderProps, "aside" | "children">;
    children: (access: ThemeAccess) => ReactNode;
}) {
    const personal = usePersonalWorkspace();
    const [unlockOpen, setUnlockOpen] = useState(false);

    return (
        <>
            {personal.restoring ? (
                <>
                    <ThemePageHeader {...header} />
                    <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center" aria-label="Restoring workspace">
                        <Loader2 className="animate-spin text-accent" size={34} />
                        <p className="mt-4 text-sm text-fg-muted">Restoring your personal workspace…</p>
                    </section>
                </>
            ) : personal.adminKey ? (
                children({ adminKey: personal.adminKey, onUnauthorized: personal.handleUnauthorized })
            ) : (
                <>
                    <ThemePageHeader {...header} />
                    <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center" aria-labelledby="themes-locked-title">
                        <span className="rounded-lg bg-accent-soft p-3 text-accent-strong"><LockKeyhole size={24} aria-hidden="true" /></span>
                        <h2 id="themes-locked-title" className="mt-4 text-lg font-semibold">Themes are private to your workspace</h2>
                        <p className="mt-2 max-w-md text-sm leading-6 text-fg-muted">
                            Unlock with your Admin Key to load theme heat, members and briefs. Nothing is requested until the workspace is unlocked.
                        </p>
                        {personal.error && <p className="mt-3 max-w-md text-sm text-caution" role="status">{personal.error}</p>}
                        <button type="button" className="primary-button mt-5" onClick={() => setUnlockOpen(true)}>
                            <LockKeyhole size={15} aria-hidden="true" /> Unlock personal workspace
                        </button>
                    </section>
                </>
            )}
            <PersonalUnlockDialog
                open={unlockOpen}
                loading={personal.unlocking || personal.restoring}
                error={personal.error}
                onClose={() => setUnlockOpen(false)}
                onUnlock={(key) => personal.unlock(key)}
            />
        </>
    );
}
