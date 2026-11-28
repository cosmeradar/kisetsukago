#!/usr/bin/env python3
"""電気代（総務省「小売物価統計調査（動向編）」の品目3511）の過去の分をまとめて取り、data/denkidai.json を作る。

毎日の自動実行（build.py）は、新しい月が公表されたら1か月ずつ足していく。
このスクリプトは最初の一度だけ（または作り直すとき）手元で動かす。
古い年の表は .xls なので、読むのに xlrd が要る（pip install xlrd）。毎日の自動実行では使わない。
表は灯油と同じ「主要品目の都市別小売価格」。

使い方: python3 scripts/make_denkidai.py 2020-01
"""

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build as b  # noqa: E402
from make_touyu import rows_of, INTERVAL_SEC  # noqa: E402


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2020-01"
    today = datetime.now(b.JST).date()
    doc = {
        "source": "総務省統計局「小売物価統計調査（動向編）」主要品目の都市別小売価格（政府統計の総合窓口 e-Stat）",
        "item": "3511 電気代（従量電灯，アンペア制（契約電流50アンペア）又は最低料金制）1か月・円",
        "months": {}, "kwh": {}, "released": {}, "statinf": {},
    }
    key = start
    misses = 0
    while True:
        y, m = map(int, key.split("-"))
        if (y, m) >= (today.year, today.month):
            break
        table, _, released = b.find_touyu_files(y, m)
        time.sleep(INTERVAL_SEC)
        if not table:
            print(f"{b.month_text(key)}分: 見つかりません")
            misses += 1
        else:
            prices, kwh = b.denki_prices(rows_of(b.get_bytes(b.ESTAT_FILE.format(sid=table, kind=0))))
            time.sleep(INTERVAL_SEC)
            odd = [c for c, v in prices.items() if not 3000 <= v <= 40000]
            missing = [b.CAPITALS[c][0] for c in b.PREF if c not in prices]
            print(f"{b.month_text(key)}分: {kwh}kWh / {len(prices)}市 / 札幌 {prices.get('01')} / 東京 {prices.get('13')} / "
                  f"那覇 {prices.get('47')} / 無い {missing} / 範囲外 {odd}")
            if len(prices) >= 40 and not odd:
                doc["months"][key] = prices
                doc["kwh"][key] = kwh
                doc["released"][key] = released
                doc["statinf"][key] = table
        key = b.month_shift(key, 1)
    if not doc["months"]:
        sys.exit("1か月分も取れませんでした")
    # 表に使用量の記載が無い月（2020年12月〜2022年ごろ）は、前後の記載のある月が同じ使用量のときだけ、それで埋める。
    # 前後で違う（基本銘柄の改正をまたぐ）ときは埋めずに止める
    keys = sorted(doc["months"])
    inferred = []
    for i, k in enumerate(keys):
        if doc["kwh"].get(k):
            continue
        before = next((doc["kwh"][x] for x in reversed(keys[:i]) if doc["kwh"].get(x)), None)
        after = next((doc["kwh"][x] for x in keys[i + 1:] if doc["kwh"].get(x)), None)
        if before and before == after:
            doc["kwh"][k] = before
            inferred.append(k)
        else:
            sys.exit(f"{k} の使用量を決められません（前 {before}・後 {after}）")
    doc["kwh_inferred"] = inferred
    os.makedirs(os.path.dirname(b.DENKI_FILE), exist_ok=True)
    with open(b.DENKI_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    print(f"書き出し: {b.DENKI_FILE}（{min(doc['months'])}〜{max(doc['months'])}・{len(doc['months'])}か月、"
          f"見つからない月 {misses}・使用量 {sorted(set(doc['kwh'].values()))}）")


if __name__ == "__main__":
    main()
