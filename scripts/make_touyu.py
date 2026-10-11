#!/usr/bin/env python3
"""灯油の値段（総務省「小売物価統計調査（動向編）」の品目3701）の過去の分をまとめて取り、data/touyu.json を作る。

毎日の自動実行（build.py）は、新しい月が公表されたら1か月ずつ足していく。
このスクリプトは最初の一度だけ（または作り直すとき）手元で動かす。
古い年の表は .xls なので、読むのに xlrd が要る（pip install xlrd）。毎日の自動実行では使わない。

使い方: python3 scripts/make_touyu.py 2020-01
（引数はさかのぼる最初の月。1か月に2〜3回取りに行き、間を空けるので、6年分で10分ほど）
"""

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build as b  # noqa: E402

INTERVAL_SEC = 2


def rows_of(data):
    """表の中身を行の並びにする。新しい年は .xlsx、古い年は .xls。"""
    if data[:2] == b"PK":
        return b.first_sheet_rows(data)
    import xlrd  # 古い年の .xls を読むときだけ使う
    sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    return [sheet.row_values(i) for i in range(sheet.nrows)]


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2020-01"
    today = datetime.now(b.JST).date()
    doc = {
        "source": "総務省統計局「小売物価統計調査（動向編）」主要品目の都市別小売価格（政府統計の総合窓口 e-Stat）",
        "item": "3701 灯油（白灯油，詰め替え売り，店頭売り）18L・円",
        "months": {}, "released": {}, "statinf": {}, "local_brand": {},
    }
    key = start
    misses = 0
    last_brand = None
    while True:
        y, m = map(int, key.split("-"))
        if (y, m) >= (today.year, today.month):
            break
        table, brand, released = b.find_touyu_files(y, m)
        time.sleep(INTERVAL_SEC)
        if not table:
            print(f"{b.month_text(key)}分: 見つかりません")
            misses += 1
        else:
            prices = b.touyu_prices(rows_of(b.get_bytes(b.ESTAT_FILE.format(sid=table, kind=0))))
            time.sleep(INTERVAL_SEC)
            odd = [c for c, v in prices.items() if not 500 <= v <= 6000]
            missing = [b.CAPITALS[c][0] for c in b.PREF if c not in prices]
            print(f"{b.month_text(key)}分: {len(prices)}市 / 札幌 {prices.get('01')} / 東京 {prices.get('13')} / "
                  f"那覇 {prices.get('47')} / 無い {missing} / 範囲外 {odd}")
            if len(prices) >= 40 and not odd:
                doc["months"][key] = prices
                doc["released"][key] = released
                doc["statinf"][key] = table
                last_brand = (key, brand)
        key = b.month_shift(key, 1)
    if last_brand and last_brand[1]:
        doc["local_brand"][last_brand[0]] = b.local_brand_capitals(
            b.get_bytes(b.ESTAT_FILE.format(sid=last_brand[1], kind=1)))
        time.sleep(INTERVAL_SEC)
    doc["tokyo"] = b.tokyo_series(b.read_xlsx(b.get_bytes(b.TOKYO_KEROSENE)))
    doc["next_release"] = b.next_release(b.get_bytes(b.KOURI_SCHEDULE), today)
    if not doc["months"]:
        sys.exit("1か月分も取れませんでした")
    os.makedirs(os.path.dirname(b.TOUYU_FILE), exist_ok=True)
    with open(b.TOUYU_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    print(f"書き出し: {b.TOUYU_FILE}（{min(doc['months'])}〜{max(doc['months'])}・{len(doc['months'])}か月、"
          f"見つからない月 {misses}・東京都区部 {min(doc['tokyo'])}〜{max(doc['tokyo'])}・次の公表 {doc['next_release']}）")


if __name__ == "__main__":
    main()
