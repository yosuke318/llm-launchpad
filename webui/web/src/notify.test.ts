import { describe, expect, it } from "vitest";

import type { AppNotification } from "./api";
import { newNotifications, notificationText } from "./notify";

const base = { id: 1, key: "k", created_at: "2026-11-03T03:00:00+00:00", workspace: "qwen3-8b" };

describe("notificationText", () => {
  it("自動停止は、残りの分数を出す", () => {
    const n: AppNotification = { ...base, kind: "auto_stop_soon", reason: "idle", stop_at: "2026-11-03T03:08:00+00:00" };
    expect(notificationText(n, new Date("2026-11-03T03:00:20Z"))).toEqual({ key: "notify.autoStopSoon.idle", params: { workspace: "qwen3-8b", minutes: 8 } });
  });

  it("止まる時刻を過ぎていても、1分と出す", () => {
    const n: AppNotification = { ...base, kind: "auto_stop_soon", reason: "max_lifetime", stop_at: "2026-11-03T03:00:00+00:00" };
    expect(notificationText(n, new Date("2026-11-03T03:05:00Z")).params.minutes).toBe(1);
  });

  it("準備完了とスポットの中断", () => {
    expect(notificationText({ ...base, kind: "ready" }, new Date()).key).toBe("notify.ready");
    expect(notificationText({ ...base, kind: "spot_interrupted" }, new Date()).key).toBe("notify.spotInterrupted");
  });
});

describe("newNotifications", () => {
  const list: AppNotification[] = [1, 2, 3].map((id) => ({ ...base, id, kind: "ready" }));

  it("初めて読んだときは知らせない", () => {
    expect(newNotifications(list, null)).toEqual([]);
  });

  it("最後に見た id より後だけ", () => {
    expect(newNotifications(list, 1).map((n) => n.id)).toEqual([2, 3]);
  });
});
