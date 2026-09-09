import type { KeyboardEvent } from "react";

export interface SegmentOption<T extends string> {
    value: T;
    label: string;
    disabled?: boolean;
}

export function SegmentedControl<T extends string>({
    label,
    value,
    options,
    onChange,
    className = "",
}: {
    label: string;
    value: T;
    options: SegmentOption<T>[];
    onChange: (value: T) => void;
    className?: string;
}) {
    const enabled = options.filter((option) => !option.disabled);
    const move = (event: KeyboardEvent<HTMLButtonElement>, direction: -1 | 1) => {
        event.preventDefault();
        const index = enabled.findIndex((option) => option.value === value);
        const next = enabled[(index + direction + enabled.length) % enabled.length];
        if (next) onChange(next.value);
    };

    return (
        <div className={`segmented-control ${className}`} role="group" aria-label={label}>
            {options.map((option) => (
                <button
                    key={option.value}
                    type="button"
                    disabled={option.disabled}
                    aria-pressed={value === option.value}
                    onClick={() => onChange(option.value)}
                    onKeyDown={(event) => {
                        if (event.key === "ArrowLeft") move(event, -1);
                        if (event.key === "ArrowRight") move(event, 1);
                    }}
                    className="segmented-control-item"
                >
                    {option.label}
                </button>
            ))}
        </div>
    );
}
