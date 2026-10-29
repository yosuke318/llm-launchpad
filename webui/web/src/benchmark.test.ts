import { describe, expect, it } from "vitest";

import { isValidRuns, nextMessages, signed } from "./benchmark";

describe("ベンチマーク", () => {
  it("何周流すかは 1〜10 の整数", () => {
    expect([0, 1, 3, 10, 11, 2.5].map(isValidRuns)).toEqual([false, true, true, true, false, false]);
  });

  it("見積もりとの差に符号を付ける", () => {
    expect(signed(1.234)).toBe("+1.23");
    expect(signed(-0.5)).toBe("−0.50");
    expect(signed(0)).toBe("0.00");
  });

  it("空の入力は送らず、送るときは会話の後ろに足す", () => {
    expect(nextMessages([], "  ")).toBeNull();
    const history = [{ role: "user" as const, content: "Hi" }, { role: "assistant" as const, content: "Hello" }];
    expect(nextMessages(history, " Next ")).toEqual([...history, { role: "user", content: "Next" }]);
  });
});
