import type { AppNotification } from "./api";

// 知らせの文の翻訳キーと値。自動停止は、表示するときまでの残り分数を出す
export function notificationText(n: AppNotification, now: Date): { key: string; params: Record<string, string | number> } {
  if (n.kind === "auto_stop_soon") {
    const minutes = Math.max(1, Math.round((new Date(n.stop_at ?? now).getTime() - now.getTime()) / 60000));
    return { key: `notify.autoStopSoon.${n.reason}`, params: { workspace: n.workspace, minutes } };
  }
  return { key: `notify.${n.kind === "ready" ? "ready" : "spotInterrupted"}`, params: { workspace: n.workspace } };
}

// 最後に見た id より後の知らせ。初めて読んだとき(lastId が null)は、前からあったものとして知らせない
export function newNotifications(list: AppNotification[], lastId: number | null) {
  return lastId === null ? [] : list.filter((n) => n.id > lastId);
}
