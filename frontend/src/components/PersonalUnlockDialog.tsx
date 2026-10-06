"use client";

import { FormEvent, useState } from "react";
import { KeyRound, LockKeyhole, X } from "lucide-react";

interface PersonalUnlockDialogProps {
    open: boolean;
    loading: boolean;
    error?: string;
    onClose: () => void;
    onUnlock: (key: string) => Promise<boolean>;
}

export default function PersonalUnlockDialog({ open, loading, error, onClose, onUnlock }: PersonalUnlockDialogProps) {
    const [key, setKey] = useState("");

    if (!open) return null;

    const close = () => {
        setKey("");
        onClose();
    };

    const submit = async (event: FormEvent) => {
        event.preventDefault();
        if (await onUnlock(key)) close();
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && close()}>
            <section className="surface-panel w-full max-w-md bg-surface-raised p-6 shadow-xl" role="dialog" aria-modal="true" aria-labelledby="unlock-title">
                <div className="flex items-start justify-between gap-4">
                    <span className="rounded-lg bg-accent-soft p-2.5 text-accent-strong"><LockKeyhole size={21} /></span>
                    <button type="button" className="rounded-md p-2 text-fg-muted hover:bg-surface-muted hover:text-fg" onClick={close} aria-label="Close unlock dialog"><X size={18} /></button>
                </div>
                <h2 id="unlock-title" className="mt-4 text-xl font-semibold">Unlock personal workspace</h2>
                <p className="mt-2 text-sm leading-6 text-fg-muted">The key unlocks your server watchlist and saved valuation scenarios for this browser session. It is never written to LocalStorage.</p>
                <form className="mt-5 space-y-4" onSubmit={submit}>
                    <label className="block text-xs font-semibold uppercase tracking-wide text-fg-muted" htmlFor="personal-admin-key">Admin Key</label>
                    <div className="relative">
                        <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" size={16} />
                        <input id="personal-admin-key" autoFocus type="password" autoComplete="current-password" className="control-field pl-10" value={key} onChange={(event) => setKey(event.target.value)} placeholder="Enter Admin Key" />
                    </div>
                    {error && <div className="error-panel" role="alert">{error}</div>}
                    <button type="submit" className="primary-button w-full" disabled={loading || !key.trim()}>{loading ? "Unlocking…" : "Unlock workspace"}</button>
                </form>
            </section>
        </div>
    );
}
