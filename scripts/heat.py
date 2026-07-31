#!/usr/bin/env python3
"""気象庁アメダスの最新観測から、気温がしきい値以上の地点を抽出する。"""

import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

LATEST_TIME = "https://www.jma.go.jp/bosai/amedas/data/latest_time.txt"
MAP_TMPL = "https://www.jma.go.jp/bosai/amedas/data/map/{ts}.json"
TABLE = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"

THRESHOLD = 35.0
MAX_AGE_MINUTES = 180


def get(url, as_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": "kisetsukago/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if as_json else raw.strip()


def value_of(entry, key):
    """アメダスの値は [値, 品質コード] の形。値だけ取り出す。"""
    v = entry.get(key)
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return float(v[0])
    return None


def pick(obs, table, threshold):
    rows = []
    for code, entry in obs.items():
        temp = value_of(entry, "temp")
        if temp is None or temp < threshold:
            continue
        info = table.get(code, {})
        rows.append({
            "code": code,
            "name": info.get("kjName") or f"不明({code})",
            "kana": info.get("knName", ""),
            "temp": temp,
        })
    rows.sort(key=lambda r: (-r["temp"], r["name"]))
    return rows


def main():
    raw_time = get(LATEST_TIME, as_json=False)
    obs_at = datetime.fromisoformat(raw_time)
    ts = obs_at.strftime("%Y%m%d%H%M%S")

    age = (datetime.now(JST) - obs_at).total_seconds() / 60
    print(f"観測時刻(JST): {obs_at:%Y-%m-%d %H:%M}")
    print(f"データの古さ: {age:.0f}分")

    obs = get(MAP_TMPL.format(ts=ts))
    table = get(TABLE)

    with_temp = sum(1 for e in obs.values() if value_of(e, "temp") is not None)
    print(f"観測地点数: {len(obs)}")
    print(f"気温が取れている地点数: {with_temp}")
    print(f"地点名の対応表: {len(table)}件")

    rows = pick(obs, table, THRESHOLD)
    print(f"{THRESHOLD:.0f}度以上の地点数: {len(rows)}")
    print("--- 上位10地点 ---")
    for r in rows[:10]:
        print(f"{r['name']}  {r['temp']}℃")

    problems = []
    if age > MAX_AGE_MINUTES:
        problems.append(f"データが古い（{age:.0f}分前）")
    if with_temp < 500:
        problems.append(f"気温が取れている地点が少なすぎる（{with_temp}）")
    if not table:
        problems.append("地点名の対応表が空")
    for r in rows:
        if not (-50 <= r["temp"] <= 50):
            problems.append(f"気温が異常値: {r['name']} {r['temp']}")
        if r["name"].startswith("不明("):
            problems.append(f"地点名が引けない: {r['code']}")

    if problems:
        print("--- 検査で問題を検出 ---")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("検査: 問題なし")


if __name__ == "__main__":
    main()
