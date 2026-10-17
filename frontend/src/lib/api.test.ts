import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "./api";

const respondWith = (status: number, body: unknown) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
    })));
};

const rejection = async () => {
    try {
        await apiRequest("/api/test");
    } catch (caught) {
        return caught as ApiError;
    }
    throw new Error("request should fail");
};

afterEach(() => {
    vi.unstubAllGlobals();
});

describe("apiRequest errors", () => {
    it("joins FastAPI validation messages instead of printing objects", async () => {
        respondWith(422, { detail: [{ loc: ["query", "days"], msg: "Input should be less than or equal to 504", type: "less_than_equal" }] });
        const error = await rejection();
        expect(error).toBeInstanceOf(ApiError);
        expect(error.status).toBe(422);
        expect(error.message).toBe("Input should be less than or equal to 504");
        expect(error.message).not.toContain("[object Object]");
    });

    it("keeps string details and falls back for unknown shapes", async () => {
        respondWith(503, { detail: "Theme trends have not been published yet." });
        expect((await rejection()).message).toBe("Theme trends have not been published yet.");

        respondWith(500, { detail: { code: "unexpected" } });
        expect((await rejection()).message).toBe("Request failed with status 500");
    });
});
