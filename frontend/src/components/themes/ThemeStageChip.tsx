import type { ThemeStage } from "@/lib/api";
import { STAGE_CLASSES, STAGE_DESCRIPTIONS, STAGE_LABELS } from "./themeFormat";

export default function ThemeStageChip({ stage, changed = false }: { stage: ThemeStage; changed?: boolean }) {
    return (
        <span
            className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-medium ${STAGE_CLASSES[stage]}`}
            title={STAGE_DESCRIPTIONS[stage]}
        >
            {STAGE_LABELS[stage]}
            {changed && <span className="font-mono opacity-80">· new</span>}
        </span>
    );
}
