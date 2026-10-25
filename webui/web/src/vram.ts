import type { VramEstimate } from "./api";

// VRAM の内訳を、インスタンスの VRAM に対する割合(%)にする。帯のグラフに使う
export interface VramSegments {
  weights: number;
  kvCache: number;
  overhead: number;
  free: number;
  // 見積もりがインスタンスの VRAM を超える(収まる型がない)
  overflow: boolean;
}

export function vramSegments(e: VramEstimate): VramSegments {
  const total = e.instance_vram_gib > 0 ? e.instance_vram_gib : e.required_gib;
  if (total <= 0) return { weights: 0, kvCache: 0, overhead: 0, free: 100, overflow: false };
  const pct = (v: number) => (v / Math.max(total, e.required_gib)) * 100;
  const weights = pct(e.weights_gib);
  const kvCache = pct(e.kv_cache_gib);
  const overhead = pct(e.overhead_gib);
  return {
    weights,
    kvCache,
    overhead,
    free: Math.max(100 - weights - kvCache - overhead, 0),
    overflow: e.instance_vram_gib < e.required_gib,
  };
}

// 残りの VRAM が少ないと、実機では収まらないことがある(見積もりは目安のため)
export const TIGHT_HEADROOM_GIB = 1;

export function isTight(e: VramEstimate) {
  return e.instance_vram_gib > 0 && e.headroom_gib < TIGHT_HEADROOM_GIB;
}
