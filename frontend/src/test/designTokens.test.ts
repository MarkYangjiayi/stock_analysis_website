import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

// Colors must go through the semantic tokens in globals.css (bg-surface, text-fg-muted, text-up, ...)
// so both themes stay consistent. Literal hex values belong only in chartTheme.ts.
const SRC = join(__dirname, "..");
const RAW_PALETTE = /\b(?:bg|text|border|from|via|to|ring|fill|stroke|accent|divide|outline|shadow|decoration|placeholder)-(?:slate|gray|zinc|neutral|stone|emerald|green|rose|red|amber|yellow|orange|blue|sky|indigo|violet|purple|teal|cyan|lime|pink|fuchsia)-\d{2,3}\b/;
const HEX_COLOR = /#[0-9a-fA-F]{6}\b/;
const ALLOWED_HEX = new Set(["lib/chartTheme.ts"]);

const sourceFiles = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "test" ? [] : sourceFiles(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
});

describe("design tokens", () => {
    it("keeps raw palette classes and literal colors out of components", () => {
        const offenders = sourceFiles(SRC).flatMap((file) => {
            const path = relative(SRC, file);
            return readFileSync(file, "utf8").split("\n").flatMap((line, index) => {
                const raw = line.match(RAW_PALETTE)?.[0];
                const hex = ALLOWED_HEX.has(path) ? undefined : line.match(HEX_COLOR)?.[0];
                return raw || hex ? [`${path}:${index + 1} ${raw ?? hex}`] : [];
            });
        });
        expect(offenders).toEqual([]);
    });
});
