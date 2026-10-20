import { describe, expect, it } from "vitest";

import nextConfig, { securityHeaders } from "../../next.config";

describe("security headers", () => {
    it("applies the baseline protections to every page", async () => {
        const rules = await nextConfig.headers?.();
        expect(rules).toEqual([{ source: "/:path*", headers: securityHeaders }]);
        const byName = Object.fromEntries(securityHeaders.map((header) => [header.key, header.value]));
        expect(byName["X-Frame-Options"]).toBe("DENY");
        expect(byName["Content-Security-Policy"]).toContain("frame-ancestors 'none'");
        // Next.js boots with inline scripts; a script-src here would break the app.
        expect(byName["Content-Security-Policy"]).not.toContain("script-src");
    });
});
