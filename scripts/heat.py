#!/usr/bin/env python3
"""気象庁アメダスの最新観測から、都道府県ごとの最高気温を求める。"""

import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

LATEST_TIME = "https://www.jma.go.jp/bosai/amedas/data/latest_time.txt"
MAP_TMPL = "https://www.jma.go.jp/bosai/amedas/data/map/{ts}.json"
STATION_TABLE = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
FORECAST_AREA = "https://www.jma.go.jp/bosai/forecast/const/forecast_area.json"

HOT = 35.0
MAX_AGE_MINUTES = 180

PREF = {
    "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県",
    "05": "秋田県", "06": "山形県", "07": "福島県", "08": "茨城県",
    "09": "栃木県", "10": "群馬県", "11": "埼玉県", "12": "千葉県",
    "13": "東京都", "14": "神奈川県", "15": "新潟県", "16": "富山県",
    "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
    "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県",
    "25": "滋賀県", "26": "京都府", "27": "大阪府", "28": "兵庫県",
    "29": "奈良県", "30": "和歌山県", "31": "鳥取県", "32": "島根県",
    "33": "岡山県", "34": "広島県", "35": "山口県", "36": "徳島県",
    "37": "香川県", "38": "愛媛県", "39": "高知県", "40": "福岡県",
    "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県",
    "45": "宮崎県", "46": "鹿児島県", "47": "沖縄県",
}


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


def stations_by_pref(forecast_area):
    """府県予報区コードの上2桁でまとめ、都道府県ごとの観測所番号にする。"""
    out = {}
    for office, areas in forecast_area.items():
        name = PREF.get(office[:2])
        if name is None:
            continue
        for area in areas:
            for code in area.get("amedas", []):
                out.setdefault(name, set()).add(code)
    return {k: sorted(v) for k, v in out.items()}


def summarize(by_pref, obs, table):
    """都道府県ごとに、代表地点のうち最も気温の高い地点を選ぶ。"""
    rows = []
    for pref, codes in by_pref.items():
        best = None
        for code in codes:
            temp = value_of(obs.get(code, {}), "temp")
            if temp is None:
                continue
            if best is None or temp > best["temp"]:
                best = {
                    "temp": temp,
                    "spot": table.get(code, {}).get("kjName") or code,
                    "code": code,
                }
        rows.append({
            "pref": pref,
            "temp": best["temp"] if best else None,
            "spot": best["spot"] if best else None,
            "hot": bool(best and best["temp"] >= HOT),
        })
    rows.sort(key=lambda r: (r["temp"] is None, -(r["temp"] or 0), r["pref"]))
    return rows


def check(rows, age, obs, table, by_pref):
    problems = []
    if age > MAX_AGE_MINUTES:
        problems.append(f"データが古い（{age:.0f}分前）")
    if len(by_pref) != 47:
        problems.append(f"都道府県が47にならない（{len(by_pref)}）")
    if not table:
        problems.append("地点名の対応表が空")
    with_temp = sum(1 for e in obs.values() if value_of(e, "temp") is not None)
    if with_temp < 500:
        problems.append(f"気温が取れている地点が少なすぎる（{with_temp}）")
    missing = [r["pref"] for r in rows if r["temp"] is None]
    if len(missing) > 5:
        problems.append(f"気温が取れない県が多い（{len(missing)}）: {missing[:5]}")
    for r in rows:
        if r["temp"] is not None and not (-50 <= r["temp"] <= 50):
            problems.append(f"気温が異常値: {r['pref']} {r['temp']}")
    return problems


def main():
    raw_time = get(LATEST_TIME, as_json=False)
    obs_at = datetime.fromisoformat(raw_time)
    age = (datetime.now(JST) - obs_at).total_seconds() / 60

    obs = get(MAP_TMPL.format(ts=obs_at.strftime("%Y%m%d%H%M%S")))
    table = get(STATION_TABLE)
    by_pref = stations_by_pref(get(FORECAST_AREA))
    rows = summarize(by_pref, obs, table)

    print(f"観測時刻(JST): {obs_at:%Y-%m-%d %H:%M}（{age:.0f}分前）")
    print(f"都道府県数: {len(by_pref)}")
    print(f"{HOT:.0f}度以上の都道府県: {sum(1 for r in rows if r['hot'])}")
    print("--- 上位15 ---")
    for r in rows[:15]:
        t = f"{r['temp']}℃（{r['spot']}）" if r["temp"] is not None else "データなし"
        print(f"{r['pref']}  {t}")

    problems = check(rows, age, obs, table, by_pref)
    if problems:
        print("--- 検査で問題を検出 ---")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("検査: 問題なし")


if __name__ == "__main__":
    main()
