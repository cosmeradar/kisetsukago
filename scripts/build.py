#!/usr/bin/env python3
"""観測項目のページ、平年値の目安のページ、トップページ、sitemap.xml を組み立てる。

いま作るのは次の6ページとトップページ。
- 観測項目のページ（METRICS）: 気温・雨量・風速・湿度
  気温は気象庁「最新の気象データ」（その日の最高・最低気温と平年差）を使う。
  雨量・風速・湿度は、アメダスのその時点の観測値を使う。
- 平年値の目安のページ（GUIDES）: 暖房・衣替え
  data/heinen.json（県庁所在地などの日別平年値。scripts/make_heinen.py で作る）から、
  決まった気温を下回る（上回る）日を求め、その日の観測とあわせて載せる。
ページごとの違いは METRICS と GUIDES の表に集めてあるので、ページを増やすときはそこに1つ足す。
並び順と商品は季節で切り替える（夏＝4〜9月、冬＝10〜3月）。
商品は楽天のレビュー件数上位から、日付を種にして日替わりで選ぶ。
公開前の検査に1つでも引っかかったら何も書かずに終了する。
日付が変わった直後（日本時間の朝6時より前）に動いたときは、その日の観測が数時間分しかないので、
何も書かずに終わる（前の日のページがそのまま残る）。
RAKUTEN_MOCK=1 のときは楽天に接続せず架空データで組み立てる（手元確認用）。
"""

import csv
import hashlib
import html
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

LATEST_TIME = "https://www.jma.go.jp/bosai/amedas/data/latest_time.txt"
MAP_TMPL = "https://www.jma.go.jp/bosai/amedas/data/map/{ts}.json"
STATION_TABLE = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
FORECAST_AREA = "https://www.jma.go.jp/bosai/forecast/const/forecast_area.json"
# その日の0時からの最高気温・最低気温（全国の観測点）。毎正時に更新される。
DAILY_MAX_CSV = "https://www.data.jma.go.jp/stats/data/mdrr/tem_rct/alltable/mxtemsadext00_rct.csv"
DAILY_MIN_CSV = "https://www.data.jma.go.jp/stats/data/mdrr/tem_rct/alltable/mntemsadext00_rct.csv"
RAKUTEN = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
SITE = "https://kisetsukago.com"
GA_ID = "G-C46GBVZFLL"

OUT_TOP = "index.html"
OUT_SITEMAP = "sitemap.xml"
HEINEN_FILE = "data/heinen.json"
MAX_AGE_MINUTES = 180
# 日本時間でこれより前に動いたときは、その日の観測が短すぎるので書き換えない
EARLIEST_HOUR = 6

# 楽天から取る候補数と、その中から見せる数
POOL_SIZE = 20
SHOW_PER_KEYWORD = 3
RAKUTEN_INTERVAL_SEC = 3
RETRY_CODES = (429, 500, 502, 503, 504)

# 季節の区切りだけ。並び順・商品・見出しは観測項目ごとに METRICS で決める。
SEASONS = {
    "summer": {"months": (4, 5, 6, 7, 8, 9)},
    "winter": {"months": (10, 11, 12, 1, 2, 3)},
}

# 私が書いた文章にだけ適用する禁止表現。商品名は店舗が付けたものなので対象外。
BANNED = [
    "防げます", "防げる", "予防できます", "防止できます", "防止します",
    "安全です", "危険はありません", "問題ありません",
    "効果があります", "効きます", "改善します", "治ります", "治せます",
    "熱中症を防", "熱中症対策になります", "風邪を防", "凍傷を防",
    "避難してください", "外出は控えてください", "水分を取ってください",
    "暖房を使ってください", "しましょう",
]

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

PREF_CODE = {name: code for code, name in PREF.items()}

# マス目の日本地図。(都道府県, 列, 行)。左上が 0,0。
TILE_MAP = [
    ("北海道", 12, 0),
    ("青森県", 12, 1),
    ("秋田県", 11, 2), ("岩手県", 12, 2),
    ("山形県", 11, 3), ("宮城県", 12, 3),
    ("石川県", 10, 4), ("新潟県", 11, 4), ("福島県", 12, 4),
    ("島根県", 4, 5), ("鳥取県", 5, 5), ("兵庫県", 6, 5), ("京都府", 7, 5),
    ("福井県", 8, 5), ("富山県", 9, 5), ("群馬県", 10, 5), ("栃木県", 11, 5),
    ("茨城県", 12, 5),
    ("長崎県", 1, 6), ("佐賀県", 2, 6), ("福岡県", 3, 6), ("広島県", 4, 6),
    ("岡山県", 5, 6), ("大阪府", 6, 6), ("滋賀県", 7, 6), ("岐阜県", 8, 6),
    ("長野県", 9, 6), ("山梨県", 10, 6), ("埼玉県", 11, 6), ("東京都", 12, 6),
    ("熊本県", 1, 7), ("大分県", 2, 7), ("山口県", 3, 7), ("愛媛県", 4, 7),
    ("香川県", 5, 7), ("和歌山県", 6, 7), ("奈良県", 7, 7), ("三重県", 8, 7),
    ("愛知県", 9, 7), ("静岡県", 10, 7), ("神奈川県", 11, 7), ("千葉県", 12, 7),
    ("鹿児島県", 1, 8), ("宮崎県", 2, 8), ("高知県", 3, 8), ("徳島県", 4, 8),
    ("沖縄県", 0, 9),
]

MAP_COLS = 13
MAP_ROWS = 10

# 色の両端。和紙の色味に合わせた薄めの寒色→暖色。
C_COLD = (188, 211, 224)
C_MID = (242, 237, 227)
C_HOT = (217, 140, 106)
C_NONE = "#EDEBE4"
# 段階で塗り分ける項目の「濃い側」の色。薄い側は C_MID（紙の色）で共通。
C_RAIN = (47, 78, 124)
C_WIND = (107, 127, 91)
C_WET = (60, 110, 143)

# ---------- 観測項目の一覧 ----------
# ページを増やすときは、この表に1つ足す。ページごとの違いはすべてここに集める。
# 季節で変えたい値は {"summer": ..., "winter": ...} と書く。共通ならそのまま書く。
#   source    "daily" は「最新の気象データ」（その日の最高・最低）、"snapshot" はアメダスのその時点の値
#   key       気象庁アメダスの項目名（snapshot のときだけ使う）
#   h1/title  h1 はページの見出し。title は検索結果に出る題名（読者が打つ語を入れる）
#   pick      代表地点の選び方。"max" は最も大きい地点、"min" は最も小さい地点
#             （daily のときは "max" が最高気温の最も高い地点、"min" が最低気温の最も低い地点）
#   scale     地図の色。("heat",) はその日の最小〜最大でなめらかに割り振る（気温用）。
#             ("steps", 区切り, 濃い側の色) は決まった段階で塗り分ける
#   valid     この範囲を外れた値が出たら公開しない
#   min_spots 全国でこの数だけ観測できていなければ公開しない
#   tags      表に付ける印。("ge", 値, 文字, 色の種類)
METRICS = [
    {
        "id": "kion",
        "source": "daily",
        "out": "kion/index.html",
        "path": "/kion/",
        "name": "気温",
        "h1": "都道府県別の気温",
        "title": "都道府県別の気温ランキング（最高気温・最低気温・平年差）｜毎日更新",
        "unit": "℃",
        "digits": 1,
        "desc": "気象庁の観測をもとに、47都道府県の代表地点のその日の最高気温・最低気温と平年差を、"
                "地図と表で毎日まとめています。",
        "pick": {"summer": "max", "winter": "min"},
        "order_label": {"summer": "最高気温の高い順", "winter": "最低気温の低い順"},
        "scale": ("heat",),
        "valid": (-50.0, 50.0),
        "min_spots": 500,
        "tags": [("ge", 35.0, "35℃以上", "hot"), ("le", 0.0, "0℃以下", "cold")],
        "tail": "気象庁が天気予報に使う代表地点のうち、各都道府県で最高気温が最も高い"
                "（冬は最低気温が最も低い）地点の値です。その都道府県で一番高い（低い）値とは限りません。",
        "heading": {"summer": "暑い時期に選ばれているもの",
                    "winter": "寒い時期に選ばれているもの"},
        "keywords": {"summer": ["ハンディファン", "冷感 タオル", "日傘"],
                     "winter": ["電気毛布", "加湿器", "あったかインナー"]},
    },
    {
        "id": "uryo",
        "source": "snapshot",
        "key": "precipitation1h",
        "out": "uryo/index.html",
        "path": "/uryo/",
        "name": "雨量",
        "h1": "都道府県別の雨量",
        "title": "都道府県別の雨量ランキング（1時間雨量）",
        "unit": "mm",
        "digits": 1,
        "column": "1時間雨量",
        "desc": "気象庁の観測をもとに、都道府県ごとの代表地点の1時間雨量を地図と表でまとめています。",
        "pick": "max",
        "order_label": "雨量の多い順",
        "scale": ("steps", [0.0, 1.0, 5.0, 10.0, 20.0], C_RAIN),
        "valid": (0.0, 200.0),
        "min_spots": 800,
        "tags": [("ge", 10.0, "10mm以上", "hot")],
        "tail": "各都道府県で最も雨量の多い代表地点の値です。"
                "同じ都道府県の中でも地点によって差があります。",
        "heading": "雨の日に選ばれているもの",
        "keywords": ["折りたたみ傘", "レインコート", "レインブーツ"],
    },
    {
        "id": "fusoku",
        "source": "snapshot",
        "key": "wind",
        "out": "fusoku/index.html",
        "path": "/fusoku/",
        "name": "風速",
        "h1": "都道府県別の風速",
        "title": "都道府県別の風速ランキング",
        "unit": "m/s",
        "digits": 1,
        "column": "風速",
        "desc": "気象庁の観測をもとに、都道府県ごとの代表地点の風速を地図と表でまとめています。",
        "pick": "max",
        "order_label": "風速の大きい順",
        "scale": ("steps", [2.0, 4.0, 6.0, 10.0, 15.0], C_WIND),
        "valid": (0.0, 100.0),
        "min_spots": 500,
        "tags": [("ge", 10.0, "10m/s以上", "hot")],
        "tail": "各都道府県で最も風速の大きい代表地点の値です。"
                "同じ都道府県の中でも地点によって差があります。",
        "heading": "風のある日に選ばれているもの",
        "keywords": ["ウインドブレーカー", "折りたたみ傘 耐風", "洗濯ばさみ 強力"],
    },
    {
        "id": "shitsudo",
        "source": "snapshot",
        "key": "humidity",
        "out": "shitsudo/index.html",
        "path": "/shitsudo/",
        "name": "湿度",
        "h1": "都道府県別の湿度",
        "title": "都道府県別の湿度ランキング",
        "unit": "%",
        "digits": 0,
        "column": "湿度",
        "desc": "気象庁の観測をもとに、都道府県ごとの代表地点の湿度を地図と表でまとめています。",
        "pick": {"summer": "max", "winter": "min"},
        "order_label": {"summer": "湿度の高い順", "winter": "湿度の低い順"},
        "scale": ("steps", [40.0, 50.0, 60.0, 70.0, 80.0, 90.0], C_WET),
        "valid": (0.0, 100.0),
        "min_spots": 500,
        "tags": [("ge", 80.0, "80%以上", "hot"), ("le", 40.0, "40%以下", "cold")],
        "tail": "気象庁が天気予報に使う代表地点のみを対象としています。"
                "同じ都道府県の中でも地点によって差があります。",
        "heading": {"summer": "湿気の多い時期に選ばれているもの",
                    "winter": "乾燥する時期に選ばれているもの"},
        "keywords": {"summer": ["除湿機", "サーキュレーター", "除湿シート"],
                     "winter": ["加湿器 大容量", "保湿クリーム", "ハンドクリーム"]},
    },
]

# ---------- 平年値の目安のページ ----------
# 県庁所在地などの日別平年値から、決まった気温を下回る（上回る）日を求めて並べる。
# 一年中有効なページなので、題名に季節の語を入れてよい（指示書ルール6の例外。2026-10-11 運営者承認）。
#   element   平年値のどの値を使うか（"tmin" 最低気温 / "tmax" 最高気温）
#   views     月で切り替える見せ方。dir が "down" なら下回る日、"up" なら上回る日を並べる。
#             main は地図に塗る気温、order_from はこの日から数えて早い順に並べる、
#             invert は「早いほど暖かい地域」になる見せ方（地図の色を反対にする）
GUIDES = [
    {
        "id": "danbou",
        "out": "danbou/index.html",
        "path": "/danbou/",
        "name": "暖房",
        "h1": "暖房はいつから？いつまで？",
        "title": "暖房はいつから？いつまで？都道府県別の目安（平年の最低気温）",
        "desc": "気象庁の平年値をもとに、朝の最低気温が15℃・10℃・5℃を下回る時期（春は上回る時期）を"
                "47都道府県で並べています。その日の最低気温と平年差も毎日入れ替わります。",
        "element": "tmin",
        "element_label": "最低気温",
        "element_text": "朝の最低気温",
        "views": [
            {"months": (6, 7, 8, 9, 10, 11, 12), "label": "いつから（秋から冬）", "dir": "down",
             "thresholds": [15.0, 10.0, 5.0], "main": 10.0, "order_from": (7, 1), "invert": False},
            {"months": (1, 2, 3, 4, 5), "label": "いつまで（冬から春）", "dir": "up",
             "thresholds": [5.0, 10.0, 15.0], "main": 10.0, "order_from": (1, 1), "invert": True},
        ],
        "note": "暖房を使い始める時期や使い終える時期は、住まいのつくりや体感によって人それぞれです。"
                "このページは、判断の材料として、決まった気温を下回る（春は上回る）時期を"
                "平年値から機械的に求めて並べたものです。表の日付は平年の値で、その年の天候によって前後します。",
        "heading": "暖房の季節に選ばれているもの",
        "keywords": ["セラミックヒーター", "こたつ", "湯たんぽ"],
    },
    {
        "id": "koromogae",
        "out": "koromogae/index.html",
        "path": "/koromogae/",
        "name": "衣替え",
        "h1": "衣替えの時期はいつ？",
        "title": "衣替えの時期はいつ？都道府県別の目安（平年の最高気温）",
        "desc": "気象庁の平年値をもとに、日中の最高気温が25℃・20℃・15℃を下回る時期（春は上回る時期）を"
                "47都道府県で並べています。その日の最高気温と平年差も毎日入れ替わります。",
        "element": "tmax",
        "element_label": "最高気温",
        "element_text": "日中の最高気温",
        "views": [
            {"months": (8, 9, 10, 11, 12, 1), "label": "秋から冬", "dir": "down",
             "thresholds": [25.0, 20.0, 15.0], "main": 20.0, "order_from": (7, 1), "invert": False},
            {"months": (2, 3, 4, 5, 6, 7), "label": "春から夏", "dir": "up",
             "thresholds": [15.0, 20.0, 25.0], "main": 20.0, "order_from": (1, 1), "invert": True},
        ],
        "note": "衣替えをする時期は、学校や職場の決まりや体感によっても変わります。"
                "このページは、判断の材料として、決まった気温を下回る（春は上回る）時期を"
                "平年値から機械的に求めて並べたものです。表の日付は平年の値で、その年の天候によって前後します。",
        "heading": "衣替えの時期に選ばれているもの",
        "keywords": ["衣装ケース", "布団圧縮袋", "防虫剤 衣類"],
    },
]


# ---------- 取得 ----------

def get(url, as_json=True, headers=None, tries=4, encoding="utf-8"):
    """取得する。混雑や一時的な不調なら間を空けて数回試す。"""
    h = {"User-Agent": "kisetsukago/0.1"}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode(encoding)
            return json.loads(raw) if as_json else raw.strip()
        except urllib.error.HTTPError as err:
            last = err
            if err.code not in RETRY_CODES:
                raise
        except urllib.error.URLError as err:
            last = err
        wait = 3 * (i + 1)
        print(f"  取得に失敗（{last}）。{wait}秒待って再試行します")
        time.sleep(wait)
    raise last


def value_of(entry, key):
    """アメダスの値は [値, 品質コード] の形。値だけ取り出す。"""
    v = entry.get(key)
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return float(v[0])
    return None


def fetch_weather():
    raw_time = get(LATEST_TIME, as_json=False)
    obs_at = datetime.fromisoformat(raw_time)
    obs = get(MAP_TMPL.format(ts=obs_at.strftime("%Y%m%d%H%M%S")))
    table = get(STATION_TABLE)
    area = get(FORECAST_AREA)
    return obs_at, obs, table, area


def to_float(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_daily_csv(text, kind):
    """「最新の気象データ」のCSV（その日の最高気温か最低気温）を読む。

    返すのは {観測所番号: {"name", "val", "diff"}} と、データの時刻（日本時間）。
    品質情報が 0（観測していない）・1（欠測）・2（疑問値）の値は使わない。
    1日の途中は「資料不足値」（品質情報4）になるが、それは0時からその時刻までの値として使う。
    """
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0] if rows else []
    want = "最高気温(℃)" if kind == "max" else "最低気温(℃)"
    if len(head) < 16 or not head[9].endswith(want) or head[14] != "平年差（℃）":
        raise ValueError(f"{want}のCSVの形が想定と違います: {head[:16]}")
    out, times = {}, {}
    for r in rows[1:]:
        if len(r) < 16 or not r[0].strip():
            continue
        try:
            at = datetime(int(r[4]), int(r[5]), int(r[6]), int(r[7]), int(r[8]), tzinfo=JST)
        except ValueError:
            continue
        times[at] = times.get(at, 0) + 1
        v = to_float(r[9])
        if r[10].strip() in ("0", "1", "2"):
            v = None
        out[r[0].strip()] = {"name": r[2].split("（")[0].strip(), "val": v,
                             "diff": to_float(r[14]) if v is not None else None}
    if not times:
        raise ValueError(f"{want}のCSVに観測がありません")
    return out, max(times, key=times.get)


def fetch_daily():
    """その日の最高気温・最低気温（全国の観測点）を、観測所番号ごとにまとめる。"""
    hi, at_hi = parse_daily_csv(get(DAILY_MAX_CSV, as_json=False, encoding="cp932"), "max")
    lo, at_lo = parse_daily_csv(get(DAILY_MIN_CSV, as_json=False, encoding="cp932"), "min")
    daily = {}
    for code in set(hi) | set(lo):
        h, l = hi.get(code, {}), lo.get(code, {})
        daily[code] = {"name": h.get("name") or l.get("name"),
                       "max": h.get("val"), "max_diff": h.get("diff"),
                       "min": l.get("val"), "min_diff": l.get("diff")}
    # 2つのCSVの時刻がずれていたら、早いほうを「ここまでの観測」とする
    return min(at_hi, at_lo), daily


def load_heinen():
    with open(HEINEN_FILE, encoding="utf-8") as f:
        return json.load(f)


def mock_pool(keyword):
    """RAKUTEN_MOCK=1 のときだけ使う架空データ。手元で見た目を確かめるためのもの。

    本番（GitHub Actions）ではこの関数は呼ばれない。
    """
    grey = ("data:image/svg+xml;charset=utf-8,"
            "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'%3E"
            "%3Crect width='1' height='1' fill='%23E2DFD5'/%3E%3C/svg%3E")
    return [{
        "name": f"【見本】{keyword} のサンプル商品 {i + 1}",
        "price": 1200 + i * 130,
        "url": "https://example.com/mock-item",
        "shop": "見本ショップ",
        "reviews": 400 - i * 11,
        "image": grey,
        "keyword": keyword,
    } for i in range(POOL_SIZE)]


def fetch_pool(keyword, app_id, access_key, affiliate_id):
    """レビュー件数の多い順に候補を取る。"""
    if os.environ.get("RAKUTEN_MOCK") == "1":
        return mock_pool(keyword)
    params = {
        "applicationId": app_id,
        "accessKey": access_key,
        "affiliateId": affiliate_id,
        "keyword": keyword,
        "hits": str(POOL_SIZE),
        "sort": "-reviewCount",
        "formatVersion": "2",
        "imageFlag": "1",
        "availability": "1",
        "hasReviewFlag": "1",
        "elements": "itemName,itemPrice,affiliateUrl,mediumImageUrls,shopName,reviewCount",
    }
    url = RAKUTEN + "?" + urllib.parse.urlencode(params)
    data = get(url, headers={"Referer": SITE + "/", "Origin": SITE})
    out = []
    for it in data.get("Items", []):
        images = it.get("mediumImageUrls") or []
        out.append({
            "name": it.get("itemName", ""),
            "price": it.get("itemPrice"),
            "url": it.get("affiliateUrl", ""),
            "shop": it.get("shopName", ""),
            "reviews": it.get("reviewCount", 0),
            "image": images[0] if images else "",
            "keyword": keyword,
        })
    return out


def pick_daily(pool, day, keyword, n=SHOW_PER_KEYWORD):
    """日付とキーワードを種にして選ぶ。同じ日なら何度動かしても同じ結果になる。"""
    if not pool:
        return []
    seed = int(hashlib.sha256(f"{day}:{keyword}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    chosen = rng.sample(pool, min(n, len(pool)))
    chosen.sort(key=lambda it: -it.get("reviews", 0))
    return chosen


# ---------- 集計 ----------

def season_of(month):
    return "summer" if month in SEASONS["summer"]["months"] else "winter"


def by_season(value, season):
    """季節で変える設定は辞書で書く。共通のものはそのまま書く。"""
    if isinstance(value, dict) and set(value) == {"summer", "winter"}:
        return value[season]
    return value


def fmt(metric, v):
    return f"{v:.{metric['digits']}f}"


def fmt_diff(v):
    return "—" if v is None else f"{v:+.1f}"


def stations_by_pref(forecast_area):
    out = {}
    for office, areas in forecast_area.items():
        name = PREF.get(office[:2])
        if name is None:
            continue
        for area in areas:
            for code in area.get("amedas", []):
                out.setdefault(name, set()).add(code)
    return {k: sorted(v) for k, v in out.items()}


def tag_of(metric, v):
    """表に付ける印。当てはまった最初のものを使う。"""
    for how, edge, label, cls in metric.get("tags", []):
        if (how == "ge" and v >= edge) or (how == "le" and v <= edge):
            return (label, cls)
    return None


def sort_rows(rows, want_high):
    if want_high:
        rows.sort(key=lambda r: (r["val"] is None, -(r["val"] or 0), r["pref"]))
    else:
        rows.sort(key=lambda r: (r["val"] is None,
                                 r["val"] if r["val"] is not None else float("inf"),
                                 r["pref"]))
    return rows


def summarize(by_pref, obs, table, metric, season):
    """都道府県ごとに代表地点をまとめる。どの地点を選ぶかは metric の pick で決まる。"""
    key = metric["key"]
    want_high = by_season(metric["pick"], season) == "max"
    rows = []
    for pref, codes in by_pref.items():
        best = None
        for code in codes:
            v = value_of(obs.get(code, {}), key)
            if v is None:
                continue
            if best is None or (v > best["val"] if want_high else v < best["val"]):
                best = {"val": v, "spot": table.get(code, {}).get("kjName") or code}
        rows.append({
            "pref": pref,
            "val": best["val"] if best else None,
            "spot": best["spot"] if best else None,
            "tag": tag_of(metric, best["val"]) if best else None,
        })
    return sort_rows(rows, want_high)


def summarize_daily(by_pref, daily, table, metric, season):
    """その日の最高・最低気温で、都道府県ごとに代表地点を1つ選ぶ。

    夏は最高気温が最も高い地点、冬は最低気温が最も低い地点。その地点の最高・最低を両方載せる。
    """
    want_high = by_season(metric["pick"], season) == "max"
    side = "max" if want_high else "min"
    rows = []
    for pref, codes in by_pref.items():
        best = None
        for code in codes:
            d = daily.get(code)
            if not d or d[side] is None:
                continue
            if best is None or (d[side] > best[side] if want_high else d[side] < best[side]):
                best = dict(d, code=code)
        row = {"pref": pref, "val": None, "spot": None, "tag": None,
               "max": None, "max_diff": None, "min": None, "min_diff": None}
        if best:
            row.update({
                "val": best[side],
                "spot": table.get(best["code"], {}).get("kjName") or best["name"] or best["code"],
                "tag": tag_of(metric, best[side]),
                "max": best["max"], "max_diff": best["max_diff"],
                "min": best["min"], "min_diff": best["min_diff"],
            })
        rows.append(row)
    return sort_rows(rows, want_high)


def value_range(rows):
    got = [r["val"] for r in rows if r["val"] is not None]
    if not got:
        return 0.0, 0.0
    return min(got), max(got)


# ---------- 平年値 ----------

LEAP = 2024  # 平年値は2月29日を含む366日の並び。うるう年の暦で数える


def day_index(month, day):
    """月日を、1月1日を0とした通し番号にする（2月29日を含む366日）。"""
    return (date(LEAP, month, day) - date(LEAP, 1, 1)).days


def md_of(i):
    d = date(LEAP, 1, 1) + timedelta(days=i % 366)
    return d.month, d.day


def md_text(i):
    m, d = md_of(i)
    return f"{m}月{d}日"


def crossing(series, threshold, direction):
    """平年値の並びから、決まった気温を下回る（上回る）最初の日の通し番号を返す。

    下回る（down）は一年で最も高い日から、上回る（up）は一年で最も低い日から数え始める。
    一年を通して下回らない（上回らない）とき、または一年中下回っている（上回っている）ときは None。
    """
    n = len(series)
    if direction == "down":
        start = max(range(n), key=lambda i: series[i])
        if series[start] < threshold:
            return None
        hit = lambda v: v < threshold  # noqa: E731
    else:
        start = min(range(n), key=lambda i: series[i])
        if series[start] > threshold:
            return None
        hit = lambda v: v > threshold  # noqa: E731
    for k in range(1, n):
        i = (start + k) % n
        if hit(series[i]):
            return i
    return None


def view_of(guide, month):
    for v in guide["views"]:
        if month in v["months"]:
            return v
    raise ValueError(f"{guide['name']}: {month}月の見せ方が決まっていません")


def summarize_guide(guide, view, heinen, daily, day):
    """平年値から、決まった気温を下回る（上回る）日を都道府県ごとに求める。その日の観測も添える。"""
    base = day_index(*view["order_from"])
    today = day_index(day.month, day.day)
    side = "min" if guide["element"] == "tmin" else "max"
    rows = []
    for code, pref in PREF.items():
        st = heinen["stations"][code]
        series = st[guide["element"]]
        dates = {t: crossing(series, t, view["dir"]) for t in view["thresholds"]}
        main_i = dates[view["main"]]
        key = (main_i - base) % 366 if main_i is not None else None
        obs = daily.get(st["amedas"], {})
        val = obs.get(side)
        normal = series[today]
        diff = obs.get(side + "_diff")
        if val is not None and diff is None:
            diff = round(val - normal, 1)  # 気象庁が平年差を出す前は、同じ平年値から求める
        label = "—"
        if main_i is not None:
            m, d = md_of(main_i)
            label = f"{m}/{d}"
        rows.append({
            "pref": pref, "code": code, "spot": st["name"], "dates": dates, "key": key,
            "val": None if key is None else (-key if view["invert"] else key),
            "label": label,
            "lowest": min(series), "highest": max(series),
            "today": val, "normal": normal, "diff": diff,
        })
    rows.sort(key=lambda r: (r["key"] is None, r["key"] if r["key"] is not None else 0, r["code"]))
    return rows


def guide_map_metric(guide, view):
    """地図の部品に渡すための設定。色は「その日の最小〜最大」と同じ割り振り方を使う。"""
    verb = "下回る" if view["dir"] == "down" else "上回る"
    return {"name": f"{guide['element_label']}が{view['main']:g}℃を{verb}日",
            "scale": ("heat",), "digits": 0, "unit": ""}


def guide_legend(view, rows):
    got = [r for r in rows if r["key"] is not None]
    if not got:
        return "", ""
    early = md_text(got[0]["dates"][view["main"]])
    late = md_text(got[-1]["dates"][view["main"]])
    return (late, early) if view["invert"] else (early, late)


def never_text(view, row, t):
    """平年では決まった気温を下回らない（上回らない）都道府県の言い方。"""
    if view["dir"] == "down":
        if row["highest"] < t:
            return f"一年を通して{t:g}℃を下回っています"
        return f"{t:g}℃を下回りません"
    if row["lowest"] > t:
        return f"一年を通して{t:g}℃を上回っています"
    return f"{t:g}℃を上回りません"


def guide_summary(guide, view, rows):
    """表の早い地域と遅い地域、東京を、ひとことで。"""
    t = view["main"]
    verb = "下回る" if view["dir"] == "down" else "上回る"
    got = [r for r in rows if r["key"] is not None]
    if not got:
        return ""
    first, last = got[0], got[-1]
    text = (f"平年の{guide['element_label']}が{t:g}℃を{verb}のは、"
            f"早い{first['pref']}（{first['spot']}）で{md_text(first['dates'][t])}ごろ、"
            f"遅い{last['pref']}（{last['spot']}）で{md_text(last['dates'][t])}ごろです。")
    tokyo = next(r for r in rows if r["code"] == "13")
    if tokyo["key"] is not None:
        text += f"東京都（東京）は{md_text(tokyo['dates'][t])}ごろです。"
    groups = {}
    for r in rows:
        if r["key"] is None:
            groups.setdefault(never_text(view, r, t), []).append(f"{r['pref']}（{r['spot']}）")
    for reason, names in groups.items():
        text += f"{'・'.join(names)}は、平年では{reason}。"
    return text


def guide_prose(guide, view, rows):
    temps = "・".join(f"{t:g}℃" for t in view["thresholds"])
    verb = "下回る" if view["dir"] == "down" else "上回る"
    return (f"気象庁の平年値（1991〜2020年の平均）をもとに、{guide['element_text']}が{temps}を"
            f"{verb}時期を、都道府県ごとに並べています。{guide_summary(guide, view, rows)}"
            f"{guide['note']}")


# ---------- 地図 ----------

def short_pref(name):
    """マス目に入れる短い名前。北海道だけはそのまま。"""
    if name != "北海道" and name.endswith(("県", "都", "府")):
        return name[:-1]
    return name


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return "#%02X%02X%02X" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def heat_color(v, low, high):
    """その日の最低〜最高の間で色を割り振る。低いほど青、高いほど赤。"""
    if v is None:
        return C_NONE
    t = 0.5 if high - low < 0.1 else (v - low) / (high - low)
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return mix(C_COLD, C_MID, t * 2)
    return mix(C_MID, C_HOT, (t - 0.5) * 2)


def step_color(v, edges, deep):
    """決まった段階で塗り分ける。全国どこも0という日がある雨量などはこちら。

    その日の最小〜最大で割り振ると、0.2mmの地点が最も濃い色になってしまう。
    """
    if v is None:
        return C_NONE
    i = 0
    for edge in edges:
        if v <= edge:
            break
        i += 1
    return mix(C_MID, deep, i / len(edges))


def color_of(metric, v, low, high):
    scale = metric["scale"]
    if scale[0] == "heat":
        return heat_color(v, low, high)
    return step_color(v, scale[1], scale[2])


def japan_map_svg(rows, metric, low, high, mini=False):
    """マス目の日本地図。mini は色だけの小さい版（トップページ用）。

    行に "label" があればマス目の数字の代わりにそれを書く（平年値の目安のページの日付）。
    """
    e = html.escape
    by_name = {r["pref"]: r for r in rows}
    if mini:
        cw, ch, gap, rx = 16, 14, 2, 2
    else:
        cw, ch, gap, rx = 46, 40, 4, 3
    width = MAP_COLS * (cw + gap) - gap
    height = MAP_ROWS * (ch + gap) - gap

    parts = []
    for name, col, row in TILE_MAP:
        rec = by_name.get(name) or {}
        v = rec.get("val")
        x = col * (cw + gap)
        y = row * (ch + gap)
        fill = color_of(metric, v, low, high)
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="{rx}" fill="{fill}"/>'
        )
        if mini:
            continue
        if "label" in rec:
            label = rec["label"]
        else:
            label = fmt(metric, v) if v is not None else "—"
        cx = x + cw // 2
        parts.append(
            f'<a href="#p{PREF_CODE[name]}">'
            f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="{rx}" fill="transparent"/>'
            f'<text class="mn" x="{cx}" y="{y + 16}">{e(short_pref(name))}</text>'
            f'<text class="mv" x="{cx}" y="{y + 32}">{e(label)}</text>'
            "</a>"
        )

    cls = "jmap mini" if mini else "jmap"
    title = f"都道府県別の{metric['name']}を色で表した図"
    return (f'<svg class="{cls}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{title}"><title>{title}</title>{"".join(parts)}</svg>')


def legend_svg(metric, low, high, steps=9):
    scale = metric["scale"]
    parts = []
    bw = 20
    if scale[0] == "heat":
        for i in range(steps):
            t = low + (high - low) * i / (steps - 1)
            parts.append(f'<rect x="{i * bw}" y="0" width="{bw}" height="10" '
                         f'fill="{heat_color(t, low, high)}"/>')
    else:
        edges, deep = scale[1], scale[2]
        steps = len(edges) + 1
        for i in range(steps):
            parts.append(f'<rect x="{i * bw}" y="0" width="{bw}" height="10" '
                         f'fill="{mix(C_MID, deep, i / len(edges))}"/>')
    return (f'<svg class="jkey" viewBox="0 0 {steps * bw} 10" '
            f'role="presentation" aria-hidden="true">{"".join(parts)}</svg>')


def legend_labels(metric, low, high):
    """色の帯の左右に置く文字。"""
    unit = metric["unit"]
    scale = metric["scale"]
    if scale[0] == "heat":
        return f"{fmt(metric, low)}{unit}", f"{fmt(metric, high)}{unit}"
    edges = scale[1]
    left = f"{edges[0]:g}{unit}" if edges[0] == 0 else f"{edges[0]:g}{unit}以下"
    return left, f"{edges[-1]:g}{unit}超"


# ---------- 検査 ----------

def run_checks(ctx):
    """1つでも引っかかったら、どのページも書き換えずに終わる。

    ctx には集計結果・商品・本文などをまとめて渡す（main を参照）。
    """
    problems = []
    now = ctx["now"]
    if ctx["age"] > MAX_AGE_MINUTES:
        problems.append(f"アメダスのデータが古い（{ctx['age']:.0f}分前）")
    daily_age = (now - ctx["daily_at"]).total_seconds() / 60
    if daily_age > MAX_AGE_MINUTES:
        problems.append(f"その日の最高・最低気温のデータが古い（{daily_age:.0f}分前）")
    by_pref = ctx["by_pref"]
    if len(by_pref) != 47:
        problems.append(f"都道府県が47にならない（{len(by_pref)}）")

    tile_names = [n for n, _, _ in TILE_MAP]
    if len(tile_names) != len(set(tile_names)):
        problems.append("地図のマス目に同じ都道府県が重複している")
    if set(tile_names) != set(PREF.values()):
        problems.append("地図のマス目と都道府県の一覧が一致しない")
    placed = [(c, r) for _, c, r in TILE_MAP]
    if len(placed) != len(set(placed)):
        problems.append("地図のマス目が同じ位置に重なっている")

    obs, daily = ctx["obs"], ctx["daily"]
    for m in METRICS:
        name = m["name"]
        rows = ctx["results"][m["id"]]
        lo, hi = m["valid"]
        if m["source"] == "daily":
            got = sum(1 for d in daily.values() if d["max"] is not None and d["min"] is not None)
            for r in rows:
                for v in (r["max"], r["min"]):
                    if v is not None and not (lo <= v <= hi):
                        problems.append(f"{name}が異常値: {r['pref']} {v}")
        else:
            got = sum(1 for e in obs.values() if value_of(e, m["key"]) is not None)
            for r in rows:
                if r["val"] is not None and not (lo <= r["val"] <= hi):
                    problems.append(f"{name}が異常値: {r['pref']} {r['val']}")
        if got < m["min_spots"]:
            problems.append(f"{name}が取れている地点が少なすぎる（{got}）")
        missing = [r["pref"] for r in rows if r["val"] is None]
        if len(missing) > 5:
            problems.append(f"{name}が取れない県が多い（{len(missing)}）")

    heinen = ctx["heinen"]
    stations = heinen.get("stations", {})
    if set(stations) != set(PREF):
        problems.append(f"平年値の地点が47都道府県とそろわない（{len(stations)}）")
    for code, st in stations.items():
        for k in ("tmax", "tmin"):
            vals = st.get(k) or []
            if len(vals) != 366 or not all(-30.0 <= v <= 40.0 for v in vals):
                problems.append(f"平年値がおかしい: {st.get('name')} {k}")
    for g in GUIDES:
        rows = ctx["guides"][g["id"]]
        dated = sum(1 for r in rows if r["key"] is not None)
        if dated < 40:
            problems.append(f"{g['name']}の目安の日が求められた県が少ない（{dated}）")
        seen = sum(1 for r in rows if r["today"] is not None)
        if seen < 40:
            problems.append(f"{g['name']}のページのその日の観測が取れた県が少ない（{seen}）")
        for r in rows:
            if r["today"] is not None and not (-50.0 <= r["today"] <= 50.0):
                problems.append(f"{g['name']}のページの観測が異常値: {r['pref']} {r['today']}")

    for page in METRICS + GUIDES:
        name = page["name"]
        group = ctx["items"][page["id"]]
        if not group:
            problems.append(f"{name}のページの商品が0件")
        for it in group:
            if not it["url"] or "//" not in it["url"]:
                problems.append(f"商品リンクが不正: {it['name'][:20]}")
            if not isinstance(it["price"], int) or it["price"] <= 0:
                problems.append(f"価格が不正: {it['name'][:20]}")
        for word in BANNED:
            if word in ctx["proses"][page["id"]]:
                problems.append(f"禁止表現が{name}の本文にある: {word}")
    return problems


# ---------- 部品 ----------

GA = f"""<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{GA_ID}');
</script>"""

CSS_COMMON = """
  :root{
    --paper:#FBFAF6; --ink:#1C2A33; --ink-soft:#5A6670;
    --ai:#2F4E7C; --koke:#6B7F5B; --hi:#B4472B; --cold:#3C6E8F; --rule:#E2DFD5;
    --mincho:"Hiragino Mincho ProN","Yu Mincho","YuMincho","MS PMincho",serif;
    --gothic:"Hiragino Kaku Gothic ProN","Yu Gothic","YuGothic",Meiryo,system-ui,sans-serif;
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;background:var(--paper);color:var(--ink);
       font-family:var(--gothic);font-size:16px;line-height:1.9}
  a{color:var(--ai);text-underline-offset:.2em}
  a:focus-visible{outline:2px solid var(--ai);outline-offset:3px}
  h2{font-size:.75rem;letter-spacing:.2em;color:var(--ink-soft);font-weight:600;
     margin:0 0 1rem;padding-top:2.25rem;border-top:1px solid var(--rule)}
  .note{font-size:.875rem;color:var(--ink-soft)}
  ul.links{list-style:none;margin:0;padding:0;font-size:.9375rem}
  ul.links li{margin-bottom:.35rem}
  footer{margin-top:3rem;padding-top:1.25rem;border-top:1px solid var(--rule);
         font-size:.8125rem;color:var(--ink-soft)}
  .mapwrap{overflow-x:auto;margin:0 0 .85rem;padding-bottom:.2rem}
  svg.jmap{display:block;width:100%;height:auto}
  svg.jmap:not(.mini){min-width:30rem}
  svg.jmap text{text-anchor:middle;font-family:var(--gothic);fill:var(--ink)}
  svg.jmap .mn{font-size:11px}
  svg.jmap .mv{font-size:13px;font-variant-numeric:tabular-nums}
  svg.jmap a{cursor:pointer}
  svg.jmap a:hover rect{stroke:var(--ink);stroke-width:1.5}
  .mapkey{display:flex;align-items:center;gap:.5rem;margin:0 0 .85rem;
          font-size:.75rem;color:var(--ink-soft);font-variant-numeric:tabular-nums}
  svg.jkey{height:10px;width:11rem;flex:0 0 auto}
"""

CSS_KION = CSS_COMMON + """
  .wrap{max-width:44rem;margin:0 auto;padding:3rem 1.5rem}
  .home{font-size:.8125rem;color:var(--ink-soft);margin:0 0 2rem}
  h1{font-family:var(--mincho);font-size:2rem;font-weight:400;
     letter-spacing:.1em;margin:0 0 .4rem}
  .stamp{font-size:.75rem;letter-spacing:.16em;color:var(--ink-soft);margin:0 0 2.5rem}
  .lede{font-family:var(--mincho);font-size:1.0625rem;margin:0 0 2.5rem}
  table{width:100%;border-collapse:collapse;font-size:.9375rem}
  th,td{text-align:left;padding:.45rem .5rem;border-bottom:1px solid var(--rule);
        vertical-align:baseline}
  th[scope=row]{font-weight:400;width:38%}
  td.t{width:34%;font-variant-numeric:tabular-nums}
  td.s{color:var(--ink-soft);font-size:.875rem}
  tbody tr:target{background:#F1ECE0}
  .tag{font-size:.75rem;letter-spacing:.06em;margin-left:.3rem}
  .tag.hot{color:var(--hi)}
  .tag.cold{color:var(--cold)}
  table.cols th[scope=row]{width:auto}
  table.cols th[scope=col]{font-size:.8125rem;font-weight:600;white-space:nowrap}
  table.cols td{font-variant-numeric:tabular-nums;white-space:nowrap}
  table.cols caption{caption-side:top;text-align:left;font-size:.8125rem;
                     color:var(--ink-soft);padding:0 0 .5rem}
  .sub{display:block;font-size:.75rem;color:var(--ink-soft);line-height:1.5}
  h3.kw{font-size:.875rem;font-weight:600;margin:0 0 .75rem}
  h3.kw::before{content:"／ ";color:var(--koke)}
  ul.grid{list-style:none;margin:0 0 2rem;padding:0;display:grid;gap:1.5rem 1rem;
          grid-template-columns:repeat(auto-fill,minmax(9rem,1fr))}
  .card a{display:block;text-decoration:none;color:var(--ink)}
  .card img{width:100%;height:auto;aspect-ratio:1;object-fit:contain;
            background:#fff;border:1px solid var(--rule)}
  .pname{display:-webkit-box;font-size:.8125rem;line-height:1.6;margin-top:.5rem;
         -webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .price{display:block;font-size:.875rem;margin-top:.25rem}
  .rev,.shop{display:block;font-size:.75rem;color:var(--ink-soft)}
  @media (max-width:30rem){.wrap{padding:2.25rem 1rem} h1{font-size:1.625rem}
    table.cols{font-size:.875rem} table.cols th,table.cols td{padding:.4rem .3rem}}
"""

CSS_TOP = CSS_COMMON + """
  .wrap{max-width:38rem;margin:0 auto;padding:4rem 1.5rem 3rem}
  .mark{font-family:var(--mincho);font-size:2.5rem;line-height:1.3;
        letter-spacing:.16em;margin:0 0 .6rem;font-weight:400}
  .romaji{font-size:.7rem;letter-spacing:.28em;text-transform:uppercase;
          color:var(--ink-soft);margin:0 0 2.75rem}
  .lede{font-family:var(--mincho);font-size:1.0625rem;margin:0 0 3rem}
  ul.pages{list-style:none;margin:0 0 1rem;padding:0}
  ul.pages li{margin-bottom:.75rem}
  .entry{display:block;text-decoration:none;border:1px solid var(--rule);
         background:#fff;padding:1rem 1.15rem}
  .entry:hover{border-color:var(--koke)}
  .entry .t{display:block;font-family:var(--mincho);font-size:1.125rem;
            color:var(--ink);margin-bottom:.15rem}
  .entry .d{display:block;font-size:.8125rem;color:var(--ink-soft);line-height:1.7}
  .entry .mapwrap{margin:.9rem 0 .6rem}
  .entry svg.jmap.mini{max-width:15rem;margin:0 auto}
  .entry .mapkey{margin:0;justify-content:center}
  .band{list-style:none;margin:0 0 1rem;padding:0;display:flex;
        flex-wrap:wrap;gap:.4rem .45rem}
  .band li{font-size:.8125rem;line-height:1;padding:.5rem .7rem;
           border:1px dashed var(--rule);color:var(--ink-soft);background:#fff}
  @media (max-width:30rem){.wrap{padding:2.75rem 1.25rem 2.5rem} .mark{font-size:2rem}}
"""

OFFICIAL_LINKS = """  <h2>公式情報</h2>
  <ul class="links">
    <li><a href="https://www.jma.go.jp/jma/index.html" rel="noopener">気象庁</a></li>
    <li><a href="https://www.jma.go.jp/bosai/warning/" rel="noopener">気象庁 警報・注意報</a></li>
    <li><a href="https://www.jma.go.jp/bosai/map.html" rel="noopener">気象庁 天気予報</a></li>
    <li><a href="https://www.wbgt.env.go.jp/" rel="noopener">環境省 熱中症予防情報サイト</a></li>
    <li><a href="https://www.bousai.go.jp/" rel="noopener">内閣府 防災情報のページ</a></li>
  </ul>
  <p class="note">お住まいの自治体が出す情報もあわせてご確認ください。</p>"""

ITEMS_NOTE = ("楽天市場でレビュー件数の多い商品の中から選んで表示しています。商品名は各店舗が登録したものを"
              "そのままにしています。価格・在庫は変動するため、最新の情報は各商品ページでご確認ください。")

PLACE_NOTE = ("当サイトは商品を紹介することを目的としています。気象情報や防災情報を提供するものではなく、"
              "健康や安全に関する判断の根拠として使えるものではありません。気象に関する情報や警戒の呼びかけは、"
              "下記の公式発表をご確認ください。")


# ---------- 組み立て ----------

def stamp_date(at):
    """「2026年8月8日 08:30」の形。%-m は Windows では使えないので数字を組み立てる。"""
    return f"{at.year}年{at.month}月{at.day}日 {at:%H:%M}"


def day_text(at):
    return f"{at.year}年{at.month}月{at.day}日"


def hm_text(at):
    """「7時」「7時30分」の形。「最新の気象データ」は毎正時なので、ふつうは「◯時」になる。"""
    return f"{at.hour}時" if at.minute == 0 else f"{at.hour}時{at.minute:02d}分"


def prose_text(obs_at, metric, season, rows, daily_at):
    """毎日変わるのは日時と数字だけ。言い回しは固定。"""
    low, high = value_range(rows)
    unit = metric["unit"]
    if metric["source"] == "daily":
        which = "最高気温" if by_season(metric["pick"], season) == "max" else "最低気温"
        span = ""
        if any(r["val"] is not None for r in rows):
            span = (f"全国の代表地点の{which}は{fmt(metric, low)}{unit}から"
                    f"{fmt(metric, high)}{unit}までの幅がありました。")
        return (
            f"{day_text(daily_at)}（日本時間）の0時から{hm_text(daily_at)}までの気象庁の観測をもとに、"
            f"都道府県ごとの最高気温と最低気温を{by_season(metric['order_label'], season)}に並べています。"
            f"{span}地図の色はこの幅にあわせて自動で割り振っています。{metric['tail']}"
        )
    span = ""
    if any(r["val"] is not None for r in rows):
        span = (f"全国の代表地点では{fmt(metric, low)}{unit}から"
                f"{fmt(metric, high)}{unit}までの幅がありました。")
    if metric["scale"][0] == "heat":
        color_note = "地図の色はこの幅にあわせて自動で割り振っています。"
    else:
        color_note = "地図の色は決まった段階で塗り分けています。"
    return (
        f"{stamp_date(obs_at)}（日本時間）時点の気象庁の観測をもとに、"
        f"都道府県ごとの{metric['name']}を{by_season(metric['order_label'], season)}に"
        f"並べています。{span}{color_note}{metric['tail']}"
    )


def items_html(keywords, items):
    e = html.escape
    groups = {}
    for it in items:
        groups.setdefault(it["keyword"], []).append(it)
    sections = []
    for kw in keywords:
        group = groups.get(kw, [])
        if not group:
            continue
        cards = []
        for it in group:
            price = f"{it['price']:,}円" if isinstance(it["price"], int) else ""
            img = (f'<img src="{e(it["image"])}" alt="" loading="lazy" width="128" height="128">'
                   if it["image"] else "")
            rev = f'<span class="rev">レビュー{it["reviews"]:,}件</span>' if it.get("reviews") else ""
            cards.append(
                '<li class="card">'
                f'<a href="{e(it["url"])}" rel="nofollow sponsored noopener" target="_blank">'
                f'{img}<span class="pname">{e(it["name"])}</span></a>'
                f'<span class="price">{price}</span>{rev}'
                f'<span class="shop">{e(it["shop"])}</span>'
                "</li>"
            )
        sections.append(f'<h3 class="kw">{e(kw)}</h3><ul class="grid">{"".join(cards)}</ul>')
    return "".join(sections)


def other_links(current_id):
    pages = METRICS + GUIDES
    return "".join(f'<li><a href="{p["path"]}">{p["h1"]}</a></li>'
                   for p in pages if p["id"] != current_id)


def page_html(page, body):
    """観測項目のページと平年値の目安のページで共通の外側。"""
    e = html.escape
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{GA}
<title>{e(page['title'])}｜季節かご</title>
<meta name="description" content="{e(page['desc'])}">
<link rel="canonical" href="{SITE}{page['path']}">
<style>{CSS_KION}</style>
</head>
<body>
<div class="wrap">

  <p class="home"><a href="/">季節かご</a></p>

  <h1>{e(page['h1'])}</h1>
{body}
  <h2>ほかのページ</h2>
  <ul class="links">{other_links(page['id'])}</ul>

  <h2>このページの位置づけ</h2>
  <p class="note">{PLACE_NOTE}</p>

{OFFICIAL_LINKS}

  <footer>
    当サイトは楽天アフィリエイトを利用した商品紹介を行っています。<br>
    季節かご / kisetsukago.com
  </footer>

</div>
</body>
</html>
"""


def render_page(obs_at, metric, rows, items, prose, season, daily_at):
    e = html.escape
    low, high = value_range(rows)
    unit = metric["unit"]
    order_label = by_season(metric["order_label"], season)
    key_left, key_right = legend_labels(metric, low, high)

    table_html = []
    if metric["source"] == "daily":
        for r in rows:
            pid = PREF_CODE.get(r["pref"], "")
            if r["val"] is None:
                cells = '<td>—</td><td>—</td><td class="s">データなし</td>'
            else:
                mark = ""
                if r["tag"]:
                    label, cls = r["tag"]
                    mark = f' <span class="tag {cls}">{label}</span>'
                hi_mark = mark if r["val"] == r["max"] else ""
                lo_mark = mark if r["val"] == r["min"] and not hi_mark else ""

                def cell(v, diff, extra):
                    if v is None:
                        return "<td>—</td>"
                    return (f'<td>{fmt(metric, v)}{unit}{extra}'
                            f'<span class="sub">平年差 {fmt_diff(diff)}</span></td>')
                cells = (cell(r["max"], r["max_diff"], hi_mark) + cell(r["min"], r["min_diff"], lo_mark)
                         + f'<td class="s">{e(r["spot"])}</td>')
            table_html.append(f'<tr id="p{pid}"><th scope="row">{e(r["pref"])}</th>{cells}</tr>')
        table = f"""  <table class="cols">
    <thead><tr><th scope="col">都道府県</th><th scope="col">最高気温</th><th scope="col">最低気温</th><th scope="col">地点</th></tr></thead>
    <tbody>
      {"".join(table_html)}
    </tbody>
  </table>
  <p class="note">気象庁の「最新の気象データ」をもとにしています。その日の0時から、上に記した時刻までの最高気温・最低気温です。平年差は、その地点のその日の平年値との差（気象庁の発表値）で、まだ発表されていないものは「—」としています。</p>"""
        stamp = (f"観測 {daily_at:%Y-%m-%d} 0時〜{hm_text(daily_at)} 日本時間／{order_label}／"
                 f"{fmt(metric, low)}〜{fmt(metric, high)}{unit}")
        table_heading = "都道府県別 代表地点の最高気温・最低気温"
    else:
        for r in rows:
            if r["val"] is None:
                cells = '<td class="t">—</td><td class="s">データなし</td>'
            else:
                mark = ""
                if r["tag"]:
                    label, cls = r["tag"]
                    mark = f' <span class="tag {cls}">{label}</span>'
                cells = (f'<td class="t">{fmt(metric, r["val"])}{unit}{mark}</td>'
                         f'<td class="s">{e(r["spot"])}</td>')
            pid = PREF_CODE.get(r["pref"], "")
            table_html.append(
                f'<tr id="p{pid}"><th scope="row">{e(r["pref"])}</th>{cells}</tr>')
        table = f"""  <table>
    <thead><tr><th scope="col">都道府県</th><th scope="col">{metric['column']}</th><th scope="col">地点</th></tr></thead>
    <tbody>
      {"".join(table_html)}
    </tbody>
  </table>
  <p class="note">気象庁が公開しているアメダスの観測値をもとにしています。10分ごとに更新される値のうち、上に記した時刻のものです。</p>"""
        stamp = (f"観測 {obs_at:%Y-%m-%d %H:%M} 日本時間／{order_label}／"
                 f"{fmt(metric, low)}〜{fmt(metric, high)}{unit}")
        table_heading = f"都道府県別 代表地点の{metric['name']}"

    body = f"""  <p class="stamp">{stamp}</p>

  <p class="lede">{e(prose)}</p>

  <h2>全国の分布</h2>
  <div class="mapwrap">{japan_map_svg(rows, metric, low, high)}</div>
  <p class="mapkey"><span>{key_left}</span>{legend_svg(metric, low, high)}<span>{key_right}</span></p>
  <p class="note">都道府県をおおよその位置に並べたもので、実際の面積や形とは異なります。マス目を押すと<a href="#hyou">下の表</a>の該当する行に移動します。</p>

  <h2 id="hyou">{table_heading}</h2>
{table}

  <h2>{e(by_season(metric['heading'], season))}</h2>
  {items_html(by_season(metric['keywords'], season), items)}
  <p class="note">{ITEMS_NOTE}</p>
"""
    return page_html(metric, body)


def render_guide(guide, view, rows, items, prose, daily_at, heinen):
    e = html.escape
    pm = guide_map_metric(guide, view)
    got = [r["val"] for r in rows if r["val"] is not None]
    low, high = (min(got), max(got)) if got else (0.0, 0.0)
    key_left, key_right = guide_legend(view, rows)
    verb = "下回る" if view["dir"] == "down" else "上回る"
    elem = guide["element_label"]

    head = "".join(f'<th scope="col">{t:g}℃</th>' for t in view["thresholds"])
    date_rows = []
    for r in rows:
        cells = "".join(
            f"<td>{md_text(r['dates'][t]) if r['dates'][t] is not None else '—'}</td>"
            for t in view["thresholds"])
        date_rows.append(
            f'<tr id="p{r["code"]}"><th scope="row">{e(r["pref"])}'
            f'<span class="sub">{e(r["spot"])}</span></th>{cells}</tr>')

    today_rows = []
    for r in sorted(rows, key=lambda r: r["code"]):
        val = f"{r['today']:.1f}℃" if r["today"] is not None else "—"
        today_rows.append(
            f'<tr><th scope="row">{e(r["pref"])}<span class="sub">{e(r["spot"])}</span></th>'
            f"<td>{val}</td><td>{r['normal']:.1f}℃</td><td>{fmt_diff(r['diff'])}</td></tr>")

    nevers = [r for r in rows if r["dates"][view["main"]] is None]
    never_note = ""
    if nevers:
        never_note = ("地図で「—」の都道府県は、平年では"
                      f"{view['main']:g}℃を{verb}日がないところです。")

    body = f"""  <p class="stamp">平年値 1991〜2020年／{e(view['label'])}／観測 {daily_at:%Y-%m-%d} 0時〜{hm_text(daily_at)} 日本時間</p>

  <p class="lede">{e(prose)}</p>

  <h2>全国の分布（平年の{elem}が{view['main']:g}℃を{verb}日）</h2>
  <div class="mapwrap">{japan_map_svg(rows, pm, low, high)}</div>
  <p class="mapkey"><span>{e(key_left)}</span>{legend_svg(pm, low, high)}<span>{e(key_right)}</span></p>
  <p class="note">都道府県をおおよその位置に並べたもので、実際の面積や形とは異なります。マス目の日付は月/日です。{never_note}マス目を押すと<a href="#hyou">下の表</a>の該当する行に移動します。</p>

  <h2 id="hyou">都道府県別 平年の{elem}が{verb}日</h2>
  <table class="cols">
    <caption>平年の{elem}が、それぞれの気温を{verb}日（{view['main']:g}℃の早い順）</caption>
    <thead><tr><th scope="col">都道府県</th>{head}</tr></thead>
    <tbody>
      {"".join(date_rows)}
    </tbody>
  </table>
  <p class="note">各都道府県の県庁所在地の観測点（埼玉県は熊谷、滋賀県は彦根）の日別平年値から求めています。平年値は、気象庁が1991〜2020年の30年間の観測から求めた値です。「—」は、平年ではその気温を{verb}日がないことを表します。出典：気象庁「<a href="{e(heinen.get('source_url', 'https://www.data.jma.go.jp/stats/etrn/index.php'))}" rel="noopener">過去の気象データ検索</a>」。</p>

  <h2>{day_text(daily_at)}の{elem}と平年</h2>
  <table class="cols">
    <caption>{day_text(daily_at)} 0時から{hm_text(daily_at)}までの観測（日本時間）。平年は、その日の平年値です</caption>
    <thead><tr><th scope="col">都道府県</th><th scope="col">{elem}</th><th scope="col">平年</th><th scope="col">平年差</th></tr></thead>
    <tbody>
      {"".join(today_rows)}
    </tbody>
  </table>
  <p class="note">観測値は気象庁の「最新の気象データ」をもとにしています。平年差は気象庁の発表値で、まだ発表されていないときは同じ平年値から求めています。</p>

  <h2>{e(guide['heading'])}</h2>
  {items_html(guide['keywords'], items)}
  <p class="note">{ITEMS_NOTE}</p>
"""
    return page_html(guide, body)


def render_top(obs_at, results, season, guides, daily_at):
    entries = []
    for m in METRICS:
        rows = results[m["id"]]
        low, high = value_range(rows)
        left, right = legend_labels(m, low, high)
        if m["source"] == "daily":
            what = "その日の最高気温・最低気温と平年差"
            when = f"観測 {daily_at:%Y-%m-%d} 0時〜{hm_text(daily_at)} 日本時間"
        else:
            what = m["name"]
            when = f"観測 {obs_at:%Y-%m-%d %H:%M} 日本時間"
        entries.append(f"""    <li>
      <a class="entry" href="{m['path']}">
        <span class="t">{m['h1']}</span>
        <span class="d">気象庁の観測をもとに、47都道府県の代表地点の{what}を並べています。{by_season(m['order_label'], season)}。毎日入れ替わります。</span>
        <span class="mapwrap">{japan_map_svg(rows, m, low, high, mini=True)}</span>
        <span class="mapkey"><span>{left}</span>{legend_svg(m, low, high)}<span>{right}</span></span>
        <span class="d">{when}／押すと都道府県名と地点名の一覧へ</span>
      </a>
    </li>""")
    for g in GUIDES:
        view, rows = guides[g["id"]]
        pm = guide_map_metric(g, view)
        got = [r["val"] for r in rows if r["val"] is not None]
        low, high = (min(got), max(got)) if got else (0.0, 0.0)
        left, right = guide_legend(view, rows)
        verb = "下回る" if view["dir"] == "down" else "上回る"
        temps = "・".join(f"{t:g}℃" for t in view["thresholds"])
        entries.append(f"""    <li>
      <a class="entry" href="{g['path']}">
        <span class="t">{g['h1']}</span>
        <span class="d">気象庁の平年値をもとに、{g['element_text']}が{temps}を{verb}時期を47都道府県で並べています。その日の{g['element_label']}と平年差も毎日入れ替わります。</span>
        <span class="mapwrap">{japan_map_svg(rows, pm, low, high, mini=True)}</span>
        <span class="mapkey"><span>{left}</span>{legend_svg(pm, low, high)}<span>{right}</span></span>
        <span class="d">地図は平年の{g['element_label']}が{view['main']:g}℃を{verb}日／押すと都道府県別の一覧へ</span>
      </a>
    </li>""")
    pages_html = "\n".join(entries)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{GA}
<title>季節かご｜都道府県別の気温と、暖房・衣替えの目安（気象庁のデータで毎日更新）</title>
<meta name="description" content="気象庁のデータをもとに、都道府県別のその日の最高気温・最低気温、暖房や衣替えの目安になる平年の気温、雨量・風速・湿度を毎日まとめています。">
<link rel="canonical" href="{SITE}/">
<style>{CSS_TOP}</style>
</head>
<body>
<div class="wrap">

  <h1 class="mark">季節かご</h1>
  <p class="romaji">kisetsukago</p>

  <p class="lede">
    暑い日、寒い朝、衣替えの季節。天気や季節の条件ごとに、
    気象庁のデータを都道府県別にまとめ、その時期に選ばれているものと一緒に置いておくサイトです。
  </p>

  <h2>いま見られるページ</h2>
  <ul class="pages">
{pages_html}
  </ul>

  <h2>これから増やす予定の条件</h2>
  <ul class="band">
    <li>台風</li><li>花粉</li><li>黄砂</li><li>紫外線</li><li>雪</li>
  </ul>
  <p class="note">条件ごとに1ページずつ用意し、中身を毎日入れ替えていきます。</p>

  <h2>このサイトの位置づけ</h2>
  <p class="note">
    当サイトは商品を紹介することを目的としています。気象情報や防災情報を提供するものではなく、
    避難や安全に関する判断の根拠として使えるものではありません。
    警報・注意報や避難に関する情報は、必ず下記の公式発表をご確認ください。
  </p>

{OFFICIAL_LINKS}

  <footer>
    当サイトは楽天アフィリエイトを利用した商品紹介を行っています。<br>
    掲載している価格や在庫は変動するため、最新の情報は各販売ページでご確認ください。
  </footer>

</div>
</body>
</html>
"""


def render_sitemap(day):
    """全ページの一覧。中身は毎日入れ替わるので、更新日はすべてその日にする。"""
    paths = ["/"] + [m["path"] for m in METRICS] + [g["path"] for g in GUIDES]
    urls = "".join(f"  <url><loc>{SITE}{p}</loc><lastmod>{day}</lastmod></url>\n" for p in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}</urlset>\n")


def write(path, text):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    mock = os.environ.get("RAKUTEN_MOCK") == "1"
    app_id = os.environ.get("RAKUTEN_APP_ID", "")
    access_key = os.environ.get("RAKUTEN_ACCESS_KEY", "")
    affiliate_id = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
    if not mock and not (app_id and access_key and affiliate_id):
        print("楽天の設定が渡されていません")
        sys.exit(1)
    if mock:
        print("※ RAKUTEN_MOCK=1。商品は架空データです（手元確認用）")

    now_jst = datetime.now(JST)
    season = season_of(now_jst.month)
    day = now_jst.strftime("%Y-%m-%d")
    print(f"日付(JST): {day} / 季節: {season} / ページ: {len(METRICS) + len(GUIDES)}枚")

    daily_at, daily = fetch_daily()
    if daily_at.hour < EARLIEST_HOUR:
        print(f"その日の観測が {hm_text(daily_at)} までしかないため、書き換えずに終わります"
              "（前の日のページがそのまま残ります）")
        return

    obs_at, obs, table, area = fetch_weather()
    age = (now_jst - obs_at).total_seconds() / 60
    by_pref = stations_by_pref(area)
    results = {}
    for m in METRICS:
        if m["source"] == "daily":
            results[m["id"]] = summarize_daily(by_pref, daily, table, m, season)
        else:
            results[m["id"]] = summarize(by_pref, obs, table, m, season)

    heinen = load_heinen()
    obs_day = daily_at.date()
    guides = {}
    for g in GUIDES:
        view = view_of(g, obs_day.month)
        guides[g["id"]] = (view, summarize_guide(g, view, heinen, daily, obs_day))

    # 同じ言葉は1回だけ取りに行く。続けて叩くと止められるため間を空ける。
    keyword_sets = [(m["id"], by_season(m["keywords"], season)) for m in METRICS]
    keyword_sets += [(g["id"], g["keywords"]) for g in GUIDES]
    pools = {}
    for _, words in keyword_sets:
        for kw in words:
            if kw in pools:
                continue
            if pools:
                time.sleep(RAKUTEN_INTERVAL_SEC)
            pools[kw] = fetch_pool(kw, app_id, access_key, affiliate_id)
            print(f"  「{kw}」: 候補{len(pools[kw])}件")

    items = {}
    for page_id, words in keyword_sets:
        chosen = []
        for kw in words:
            chosen.extend(pick_daily(pools[kw], day, kw))
        items[page_id] = chosen

    proses = {m["id"]: prose_text(obs_at, m, season, results[m["id"]], daily_at) for m in METRICS}
    for g in GUIDES:
        view, rows = guides[g["id"]]
        proses[g["id"]] = guide_prose(g, view, rows)

    problems = run_checks({
        "now": now_jst, "age": age, "daily_at": daily_at, "by_pref": by_pref,
        "obs": obs, "daily": daily, "results": results, "heinen": heinen,
        "guides": {k: v[1] for k, v in guides.items()}, "items": items, "proses": proses,
    })

    print(f"アメダス: {obs_at:%Y-%m-%d %H:%M}（{age:.0f}分前） / 最高・最低気温: "
          f"{daily_at:%Y-%m-%d %H:%M}まで / 都道府県: {len(by_pref)}")
    for m in METRICS:
        rows = results[m["id"]]
        low, high = value_range(rows)
        blank = sum(1 for r in rows if r["val"] is None)
        print(f"  {m['name']}: {fmt(m, low)}〜{fmt(m, high)}{m['unit']} / "
              f"商品{len(items[m['id']])}件 / データなし{blank}県")
    for g in GUIDES:
        view, rows = guides[g["id"]]
        dated = sum(1 for r in rows if r["key"] is not None)
        seen = sum(1 for r in rows if r["today"] is not None)
        print(f"  {g['name']}（{view['label']}）: 目安の日 {dated}県 / その日の観測 {seen}県 / "
              f"商品{len(items[g['id']])}件")
    if problems:
        print("--- 検査で問題を検出。公開しません ---")
        for p in problems:
            print(" -", p)
        sys.exit(1)

    for m in METRICS:
        write(m["out"], render_page(obs_at, m, results[m["id"]], items[m["id"]],
                                    proses[m["id"]], season, daily_at))
    for g in GUIDES:
        view, rows = guides[g["id"]]
        write(g["out"], render_guide(g, view, rows, items[g["id"]], proses[g["id"]],
                                     daily_at, heinen))
    write(OUT_TOP, render_top(obs_at, results, season, guides, daily_at))
    write(OUT_SITEMAP, render_sitemap(obs_day.isoformat()))
    written = ", ".join([m["out"] for m in METRICS] + [g["out"] for g in GUIDES]
                        + [OUT_TOP, OUT_SITEMAP])
    print(f"検査: 問題なし / 書き出し: {written}")


if __name__ == "__main__":
    main()
