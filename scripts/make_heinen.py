#!/usr/bin/env python3
"""都道府県ごとの代表の観測点（県庁所在地の気象台など）の「日別平年値」を集めて
data/heinen.json に書き出す。

毎日の自動実行では使わない。平年値は10年ごとにしか変わらない（いまは1991〜2020年の値で、
次は2031年ごろに入れ替わる）ので、手元で一度動かして結果をリポジトリに置いておく。
build.py はこのファイルを読むだけで、気象庁の平年値のページには毎日取りに行かない。

使い方: python3 scripts/make_heinen.py
所要時間は10分あまり（47地点×12か月。続けて叩かないよう1件ごとに間を空ける）。
"""

import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

URL = ("https://www.data.jma.go.jp/stats/etrn/view/nml_sfc_d.php"
       "?prec_no={prec}&block_no={wmo}&year=&month={month}&day=&view=")
OUT = "data/heinen.json"
INTERVAL_SEC = 1.5

# (都道府県コード, 都道府県, 地点名, アメダスの観測所番号, 国際地点番号)
# 県庁所在地の観測点。県庁所在地に気象台などが無い埼玉県は熊谷、滋賀県は彦根。
STATIONS = [
    ("01", "北海道", "札幌", "14163", "47412"),
    ("02", "青森県", "青森", "31312", "47575"),
    ("03", "岩手県", "盛岡", "33431", "47584"),
    ("04", "宮城県", "仙台", "34392", "47590"),
    ("05", "秋田県", "秋田", "32402", "47582"),
    ("06", "山形県", "山形", "35426", "47588"),
    ("07", "福島県", "福島", "36127", "47595"),
    ("08", "茨城県", "水戸", "40201", "47629"),
    ("09", "栃木県", "宇都宮", "41277", "47615"),
    ("10", "群馬県", "前橋", "42251", "47624"),
    ("11", "埼玉県", "熊谷", "43056", "47626"),
    ("12", "千葉県", "千葉", "45212", "47682"),
    ("13", "東京都", "東京", "44132", "47662"),
    ("14", "神奈川県", "横浜", "46106", "47670"),
    ("15", "新潟県", "新潟", "54232", "47604"),
    ("16", "富山県", "富山", "55102", "47607"),
    ("17", "石川県", "金沢", "56227", "47605"),
    ("18", "福井県", "福井", "57066", "47616"),
    ("19", "山梨県", "甲府", "49142", "47638"),
    ("20", "長野県", "長野", "48156", "47610"),
    ("21", "岐阜県", "岐阜", "52586", "47632"),
    ("22", "静岡県", "静岡", "50331", "47656"),
    ("23", "愛知県", "名古屋", "51106", "47636"),
    ("24", "三重県", "津", "53133", "47651"),
    ("25", "滋賀県", "彦根", "60131", "47761"),
    ("26", "京都府", "京都", "61286", "47759"),
    ("27", "大阪府", "大阪", "62078", "47772"),
    ("28", "兵庫県", "神戸", "63518", "47770"),
    ("29", "奈良県", "奈良", "64036", "47780"),
    ("30", "和歌山県", "和歌山", "65042", "47777"),
    ("31", "鳥取県", "鳥取", "69122", "47746"),
    ("32", "島根県", "松江", "68132", "47741"),
    ("33", "岡山県", "岡山", "66408", "47768"),
    ("34", "広島県", "広島", "67437", "47765"),
    ("35", "山口県", "山口", "81286", "47784"),
    ("36", "徳島県", "徳島", "71106", "47895"),
    ("37", "香川県", "高松", "72086", "47891"),
    ("38", "愛媛県", "松山", "73166", "47887"),
    ("39", "高知県", "高知", "74182", "47893"),
    ("40", "福岡県", "福岡", "82182", "47807"),
    ("41", "佐賀県", "佐賀", "85142", "47813"),
    ("42", "長崎県", "長崎", "84496", "47817"),
    ("43", "熊本県", "熊本", "86141", "47819"),
    ("44", "大分県", "大分", "83216", "47815"),
    ("45", "宮崎県", "宮崎", "87376", "47830"),
    ("46", "鹿児島県", "鹿児島", "88317", "47827"),
    ("47", "沖縄県", "那覇", "91197", "47936"),
]

DAYS_IN_MONTH = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # うるう年の暦


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "kisetsukago/0.1"})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as err:  # 一時的な不調なら間を空けて数回試す
            wait = 5 * (i + 1)
            print(f"  取得に失敗（{err}）。{wait}秒待って再試行します")
            time.sleep(wait)
    raise RuntimeError(f"取得できませんでした: {url}")


def parse_month(page):
    """1か月分の表から、日ごとの平均・最高・最低気温を取り出す。"""
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S)]
        rows.append(cells)
    header = next((r for r in rows if r and r[0] == "要素"), None)
    if header is None:
        raise RuntimeError("表の見出しが見つかりません")
    idx = {name: header.index(name) for name in ("平均気温(℃)", "最高気温(℃)", "最低気温(℃)")}
    out = {}
    for r in rows:
        m = re.match(r"^(\d+)日$", r[0]) if r else None
        if not m:
            continue
        out[int(m.group(1))] = {
            "tavg": float(r[idx["平均気温(℃)"]]),
            "tmax": float(r[idx["最高気温(℃)"]]),
            "tmin": float(r[idx["最低気温(℃)"]]),
        }
    return out


def main():
    stations = {}
    for code, pref, name, amedas, wmo in STATIONS:
        series = {"tavg": [], "tmax": [], "tmin": []}
        for month in range(1, 13):
            page = fetch(URL.format(prec=amedas[:2], wmo=wmo, month=month))
            days = parse_month(page)
            want = DAYS_IN_MONTH[month - 1]
            if month == 2 and 29 not in days and 28 in days:
                # 2月29日の行が無いときは、前後の日の平均で埋める（3月1日は次の月の表なので後で直す）
                days[29] = None
            for d in range(1, want + 1):
                if d not in days:
                    raise RuntimeError(f"{name} {month}月{d}日の値がありません")
                for k in series:
                    series[k].append(days[d][k] if days[d] else None)
            time.sleep(INTERVAL_SEC)
        for k, vals in series.items():
            for i, v in enumerate(vals):
                if v is None:  # 2月29日の穴埋め
                    vals[i] = round((vals[i - 1] + vals[i + 1]) / 2, 1)
        stations[code] = {"pref": pref, "name": name, "amedas": amedas, "wmo": wmo, **series}
        print(f"{pref} {name}: 最高 {min(series['tmax'])}〜{max(series['tmax'])}℃ / "
              f"最低 {min(series['tmin'])}〜{max(series['tmin'])}℃")

    if len(stations) != 47:
        sys.exit(f"47地点そろいませんでした（{len(stations)}）")
    for s in stations.values():
        for k in ("tavg", "tmax", "tmin"):
            if len(s[k]) != 366 or not all(-30.0 <= v <= 40.0 for v in s[k]):
                sys.exit(f"値がおかしい: {s['name']} {k}")

    doc = {
        "source": "気象庁「過去の気象データ検索」の日別平年値（統計期間1991〜2020年）",
        "source_url": "https://www.data.jma.go.jp/stats/etrn/index.php",
        "fetched": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d"),
        "days": "1月1日から12月31日まで（2月29日を含む366日）",
        "stations": stations,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"書き出し: {OUT}（47地点）")


if __name__ == "__main__":
    main()
