"""インスタンス型を、リージョンごとの料金とスポットの起動しやすさで比べ、おすすめのリージョンを選ぶ。"""

# Spot placement score がこれ以上なら、中断や起動できないリスクが低いとみなす(web/src/launch.ts の placementRisk と同じ)
LOW_RISK_SCORE = 7


def compare(regions, on_demand, spot_min, scores, current=None):
    """regions: [{"code", "name"}]、on_demand: {リージョン: 時間単価}、spot_min: {リージョン: {"availability_zone", "hourly_usd"}}、
    scores: {リージョン: 1〜10}。インスタンス型を提供しているリージョン(オンデマンドかスポットの料金があるもの)だけを、
    スポットの安い順(スポットがなければオンデマンドの安い順)に並べる。

    おすすめ:
      spot: score が LOW_RISK_SCORE 以上のリージョンのうち、スポットが一番安いところ。なければ None
      on_demand: オンデマンドが一番安いところ
    """
    names = {r["code"]: r["name"] for r in regions}
    rows = []
    for code in names:
        od = on_demand.get(code)
        spot = spot_min.get(code)
        if od is None and spot is None:
            continue
        rows.append({
            "code": code,
            "name": names[code],
            "on_demand_hourly_usd": od,
            "spot_min": spot,
            "placement_score": scores.get(code),
            "current": code == current,
        })

    def sort_key(r):
        spot = r["spot_min"]["hourly_usd"] if r["spot_min"] else None
        return (spot is None, spot if spot is not None else r["on_demand_hourly_usd"] or 0, r["code"])

    rows.sort(key=sort_key)

    spot_candidates = [r for r in rows if r["spot_min"] and (r["placement_score"] or 0) >= LOW_RISK_SCORE]
    od_candidates = [r for r in rows if r["on_demand_hourly_usd"] is not None]
    return {
        "regions": rows,
        "recommended": {
            "spot": spot_candidates[0]["code"] if spot_candidates else None,
            "on_demand": min(od_candidates, key=lambda r: (r["on_demand_hourly_usd"], r["code"]))["code"] if od_candidates else None,
        },
        "low_risk_score": LOW_RISK_SCORE,
    }
