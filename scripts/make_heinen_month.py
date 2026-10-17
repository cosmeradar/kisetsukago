#!/usr/bin/env python3
"""都道府県ごとの代表の観測点（make_heinen.py と同じ47地点）の「月ごとの平年値」を集めて
data/heinen_month.json に書き出す。読み物「寒い県ランキング」が使う。

毎日の自動実行では使わない。平年値は10年ごとにしか変わらない（いまは1991〜2020年の値）ので、
手元で一度動かして結果をリポジトリに置いておく（次は2031年ごろ、make_heinen.py と一緒に作り直す）。

気象庁「過去の気象データ検索」の平年値（年・月ごとの値）の「詳細（気温・蒸気圧・湿度）」の表から、
月ごとと年の、平均・日最高・日最低気温と、階級別の日数（冬日＝日最低0℃未満・真冬日＝日最高0℃未満など）を取る。

使い方: python3 scripts/make_heinen_month.py
所要時間は2分ほど（47地点。続けて叩かないよう1件ごとに間を空ける）。
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_heinen import STATIONS, fetch  # noqa: E402  地点の一覧と取得は日別平年値と同じものを使う

URL = ("https://www.data.jma.go.jp/stats/etrn/view/nml_sfc_ym.php"
       "?prec_no={prec}&block_no={wmo}&year=&month=&day=&view=a2")
OUT = "data/heinen_month.json"
INTERVAL_SEC = 2.0
# 表の列の並び（要素の次から）。気象庁の表の見出しの順
COLS = ["tavg", "tmax", "tmin", "d_avg_lt0", "d_avg_ge25", "d_min_lt0", "d_min_ge25",
        "d_max_lt0", "d_max_ge25", "d_max_ge30", "d_max_ge35", "vapor", "humidity"]
NEED = ["tavg", "tmax", "tmin", "d_min_lt0", "d_max_lt0"]


def number(cell):
    """「-5.2」「7.2 @」「---」などから数字を取る。数字が無ければ None。印（@ ) ] など）は別に返す。"""
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(\S*)", cell)
    if not m:
        return None, cell.strip()
    return float(m.group(1)), m.group(2)


def parse(page):
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    header_ok = "＜0.0℃" in html.unescape(page) or "&lt;0.0" in page
    if not header_ok:
        raise RuntimeError("表の見出し（＜0.0℃）が見つかりません")
    out, marks = {}, set()
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S)]
        if not cells:
            continue
        m = re.match(r"^(\d+)月$", cells[0])
        key = int(m.group(1)) if m else ("year" if cells[0] == "年" else None)
        if key is None or len(cells) < 1 + len(COLS):
            continue
        row = {}
        for name, cell in zip(COLS, cells[1:1 + len(COLS)]):
            v, mark = number(cell)
            row[name] = v
            if mark and name in NEED:
                marks.add(f"{name}:{mark}")
        out[key] = row
    return out, marks


def main():
    stations = {}
    for code, pref, name, amedas, wmo in STATIONS:
        rows, marks = parse(fetch(URL.format(prec=amedas[:2], wmo=wmo)))
        months = {str(m): rows[m] for m in range(1, 13) if m in rows}
        if len(months) != 12 or "year" not in rows:
            sys.exit(f"{pref} {name}: 12か月と年がそろいませんでした")
        for k in NEED:
            if any(r[k] is None for r in months.values()) or rows["year"][k] is None:
                sys.exit(f"{pref} {name}: {k} に値の無い月があります")
        stations[code] = {"pref": pref, "name": name, "amedas": amedas, "wmo": wmo,
                          "months": months, "year": rows["year"], "marks": sorted(marks)}
        y = rows["year"]
        print(f"{pref} {name}: 1月の最低 {months['1']['tmin']}℃ / 最高 {months['1']['tmax']}℃ / "
              f"冬日 {y['d_min_lt0']}日 / 真冬日 {y['d_max_lt0']}日{' / 印 ' + ','.join(marks) if marks else ''}")
        time.sleep(INTERVAL_SEC)

    if len(stations) != 47:
        sys.exit(f"47地点そろいませんでした（{len(stations)}）")
    doc = {
        "source": "気象庁「過去の気象データ検索」の平年値（年・月ごとの値）詳細（気温・蒸気圧・湿度）（統計期間1991〜2020年）",
        "source_url": "https://www.data.jma.go.jp/stats/etrn/index.php",
        "fetched": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d"),
        "columns": {"tavg": "平均気温", "tmax": "日最高気温の平均", "tmin": "日最低気温の平均",
                    "d_avg_lt0": "日平均気温0℃未満の日数", "d_avg_ge25": "日平均気温25℃以上の日数",
                    "d_min_lt0": "日最低気温0℃未満の日数（冬日）", "d_min_ge25": "日最低気温25℃以上の日数",
                    "d_max_lt0": "日最高気温0℃未満の日数（真冬日）", "d_max_ge25": "日最高気温25℃以上の日数（夏日）",
                    "d_max_ge30": "日最高気温30℃以上の日数（真夏日）", "d_max_ge35": "日最高気温35℃以上の日数（猛暑日）",
                    "vapor": "蒸気圧", "humidity": "相対湿度"},
        "stations": stations,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"書き出し: {OUT}（47地点）")


if __name__ == "__main__":
    main()
