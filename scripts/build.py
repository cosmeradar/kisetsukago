#!/usr/bin/env python3
"""観測項目のページ、平年値の目安のページ、トップページ、sitemap.xml を組み立てる。

いま作るのは次の6ページとトップページ。
- 観測項目のページ（METRICS）: 気温・雨量・風速・湿度
  気温は気象庁「最新の気象データ」（その日の最高・最低気温と平年差）を使う。
  雨量・風速・湿度は、アメダスのその時点の観測値を使う。
- 平年値の目安のページ（GUIDES）: 暖房・衣替え
  data/heinen.json（県庁所在地などの日別平年値。scripts/make_heinen.py で作る）から、
  決まった気温を下回る（上回る）日を求め、その日の観測とあわせて載せる。
- 灯油の値段のページ（TOUYU）: 総務省「小売物価統計調査（動向編）」の県庁所在市の灯油18Lの値段。
  data/touyu.json（過去の分は scripts/make_touyu.py で作る）に、新しい月が公表されたら e-Stat から足していく。
  取りに行けない日も前の値でページを作り、長く新しくならないときだけ build_warnings.txt に書いて知らせる。
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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
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

# 灯油の値段（総務省「小売物価統計調査（動向編）」。毎月1回、翌月の下旬に公表）
ESTAT_LIST = ("https://www.e-stat.go.jp/stat-search/files?page=1&layout=datalist&cycle=1"
              "&toukei=00200571&tstat=000000680001&tclass1val=0&year={year}0&month={code}&result_back=1")
ESTAT_FILE = "https://www.e-stat.go.jp/stat-search/file-download?statInfId={sid}&fileKind={kind}"
TOKYO_KEROSENE = "https://www.stat.go.jp/data/kouri/doukou/zuhyou/7301_3701_13.xlsx"
KOURI_SCHEDULE = "https://www.stat.go.jp/data/kouhyou/e-stat_kouri_schedule.xml"
WEEKLY_OFFICIAL = "https://www.enecho.meti.go.jp/statistics/petroleum_and_lpgas/pl007/results.html"

OUT_TOP = "index.html"
OUT_SITEMAP = "sitemap.xml"
HEINEN_FILE = "data/heinen.json"
TOUYU_FILE = "data/touyu.json"
# 灯油の新しい月がこの日数より長く入ってこなければ、自動実行を「失敗」にして知らせる（ページは前の値で作る）
TOUYU_STALE_DAYS = 80
WARN_FILE = "build_warnings.txt"
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

# 都道府県庁所在市（小売物価統計の都市名）。古い年の表は「市」が付かないので別名も持つ
CAPITALS = {
    "01": ("札幌市", ("札幌",)), "02": ("青森市", ("青森",)), "03": ("盛岡市", ("盛岡",)),
    "04": ("仙台市", ("仙台",)), "05": ("秋田市", ("秋田",)), "06": ("山形市", ("山形",)),
    "07": ("福島市", ("福島",)), "08": ("水戸市", ("水戸",)), "09": ("宇都宮市", ("宇都宮",)),
    "10": ("前橋市", ("前橋",)), "11": ("さいたま市", ("さいたま",)), "12": ("千葉市", ("千葉",)),
    "13": ("東京都区部", ("区部", "東京")), "14": ("横浜市", ("横浜",)), "15": ("新潟市", ("新潟",)),
    "16": ("富山市", ("富山",)), "17": ("金沢市", ("金沢",)), "18": ("福井市", ("福井",)),
    "19": ("甲府市", ("甲府",)), "20": ("長野市", ("長野",)), "21": ("岐阜市", ("岐阜",)),
    "22": ("静岡市", ("静岡",)), "23": ("名古屋市", ("名古屋",)), "24": ("津市", ("津",)),
    "25": ("大津市", ("大津",)), "26": ("京都市", ("京都",)), "27": ("大阪市", ("大阪",)),
    "28": ("神戸市", ("神戸",)), "29": ("奈良市", ("奈良",)), "30": ("和歌山市", ("和歌山",)),
    "31": ("鳥取市", ("鳥取",)), "32": ("松江市", ("松江",)), "33": ("岡山市", ("岡山",)),
    "34": ("広島市", ("広島",)), "35": ("山口市", ("山口",)), "36": ("徳島市", ("徳島",)),
    "37": ("高松市", ("高松",)), "38": ("松山市", ("松山",)), "39": ("高知市", ("高知",)),
    "40": ("福岡市", ("福岡",)), "41": ("佐賀市", ("佐賀",)), "42": ("長崎市", ("長崎",)),
    "43": ("熊本市", ("熊本",)), "44": ("大分市", ("大分",)), "45": ("宮崎市", ("宮崎",)),
    "46": ("鹿児島市", ("鹿児島",)), "47": ("那覇市", ("那覇",)),
}

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
        "desc": "ストーブやこたつを出す時期を考える材料に。気象庁の平年値をもとに、朝の最低気温が15℃・10℃・5℃を"
                "下回る時期（春は上回る時期）を47都道府県で並べています。その日の最低気温と平年差も毎日入れ替わります。",
        "element": "tmin",
        "element_label": "最低気温",
        "element_text": "朝の最低気温",
        "views": [
            {"months": (6, 7, 8, 9, 10, 11, 12), "label": "いつから（秋から冬）", "dir": "down",
             "thresholds": [15.0, 10.0, 5.0], "main": 10.0, "order_from": (7, 1), "invert": False},
            {"months": (1, 2, 3, 4, 5), "label": "いつまで（冬から春）", "dir": "up",
             "thresholds": [5.0, 10.0, 15.0], "main": 10.0, "order_from": (1, 1), "invert": True},
        ],
        "note": "暖房（エアコン・ストーブ・こたつ）を使い始める時期や使い終える時期は、住まいのつくりや体感によって人それぞれです。"
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

# ---------- 灯油の値段のページ ----------
# 総務省「小売物価統計調査（動向編）」の品目3701 灯油（18L）。県庁所在市の値段を毎月入れ替える。
# 公式の毎週の調査（資源エネルギー庁）はプログラムからの取得を受け付けていないので使わず、リンクで案内する。
TOUYU = {
    "id": "touyu",
    "out": "touyu/index.html",
    "path": "/touyu/",
    "name": "灯油",
    "h1": "灯油の値段（18リットル）",
    "title": "灯油の値段と推移（18リットル）｜47都道府県の県庁所在市別・総務省の毎月の調査",
    "desc": "総務省の小売物価統計調査をもとに、47都道府県の県庁所在市の灯油18リットルの店頭価格と、"
            "前の月・前の年からの値動き、東京都区部の長い推移を毎月まとめています。",
    "heading": "灯油を使う季節に選ばれているもの",
    "keywords": ["石油ファンヒーター", "灯油ポンプ", "灯油タンク"],
}

# ---------- 読み物（蓄積記事） ----------
# 手持ちのデータで1つの問いに答える読み物。毎日の観測のページと違い、言い回しは固定のまま、
# 数字だけを毎回データから計算し直す（新しい月の値段が入ると自動で増える）。
# 言い回しが成り立たなくなったら（例：夏がいちばん安い年が半分を超えた）、検査で止めて知らせる。
TOUYU_YASUI = {
    "id": "touyu-yasui-jiki",
    "out": "yomimono/touyu-yasui-jiki/index.html",
    "path": "/yomimono/touyu-yasui-jiki/",
    "name": "灯油の安い月",
    "h1": "灯油は何月が安い？",
    "title": "灯油は何月が安い？総務省の2008年からの値段では「夏が安い」とは言えなかった",
    "desc": "「灯油は夏が安い」は本当か。総務省の小売物価統計調査の灯油18リットルの値段"
            "（東京都区部は2008年11月から、47都道府県の県庁所在市は2020年から）で、その年いちばん安かった月を数えました。",
    "lead": "「灯油は夏が安い」は本当か。総務省の2008年からの値段で、その年いちばん安かった月を数えました。",
    "heading": "灯油を使う季節に選ばれているもの",
    "keywords": ["灯油ポンプ", "灯油タンク", "石油ファンヒーター"],
    "published": "2026-10-18",
}
SAMUI_KEN = {
    "id": "samui-ken-ranking",
    "out": "yomimono/samui-ken-ranking/index.html",
    "path": "/yomimono/samui-ken-ranking/",
    "name": "寒い県",
    "h1": "寒い県ランキング",
    "title": "寒い県ランキング｜本州で一番寒いのは？朝の冷え込みなら岩手（盛岡）、昼の寒さなら青森（県庁所在地の平年値）",
    "desc": "気象庁の平年値（1991〜2020年）で、47都道府県の県庁所在地などの1月の最低気温・最高気温と、"
            "冬日・真冬日の日数を比べました。本州で一番寒い県は、比べ方で岩手と青森に分かれます。",
    "lead": "本州で一番寒い県は？気象庁の平年値で、朝の冷え込み・昼の寒さ・冬日と真冬日の日数を47都道府県で比べました。",
    "heading": "寒い季節に選ばれているもの",
    "keywords": ["あったかインナー", "電気毛布", "湯たんぽ"],
    "published": "2026-10-18",
}
YOMIMONO = [TOUYU_YASUI, SAMUI_KEN]
HEINEN_MONTH_FILE = "data/heinen_month.json"
HONSHU = {f"{i:02d}" for i in range(2, 36)}  # 青森〜山口
JMA_YOUGO_KION = "https://www.jma.go.jp/jma/kishou/know/yougo_hp/kion.html"
JMA_ETRN = "https://www.data.jma.go.jp/stats/etrn/index.php"
SUMMER_MONTHS = (6, 7, 8)
KOURI_HANASHI = "https://www.stat.go.jp/data/kouri/handbook/pdf/hanashi05_2026.pdf"
TOYOTOMI_MANUAL = "https://www.toyotomi.jp/manual/operation/LC-32P.pdf"

ALL_PAGES = METRICS + GUIDES + [TOUYU] + YOMIMONO


# ---------- 取得 ----------

def get_bytes(url, headers=None, tries=4):
    """取得する（中身はそのまま）。混雑や一時的な不調なら間を空けて数回試す。"""
    h = {"User-Agent": "kisetsukago/0.1"}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
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


def get(url, as_json=True, headers=None, tries=4, encoding="utf-8"):
    """取得して文字にする。as_json なら JSON として読む。"""
    raw = get_bytes(url, headers=headers, tries=tries).decode(encoding)
    return json.loads(raw) if as_json else raw.strip()


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


# ---------- 灯油の値段 ----------

def col_index(ref):
    """「AB12」のようなセル番地から、列の番号（0から）を返す。"""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_xlsx(data):
    """Excel（.xlsx）を標準の道具だけで読む。{シート名: [行（列番号どおりの並び）]} を返す。"""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
            # ふりがな（rPh）の文字は除き、本体の文字（t と、書式つきの r の中の t）だけをつなぐ
            parts = [t.text or "" for t in si.findall(ns + "t")]
            parts += [t.text or "" for r in si.findall(ns + "r") for t in r.findall(ns + "t")]
            shared.append("".join(parts))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    book = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(ns + "sheet"):
        target = rels.get(sh.get(rid), "")
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        if path not in names:
            continue
        rows = []
        for row in ET.fromstring(z.read(path)).iter(ns + "row"):
            cells = {}
            for c in row.iter(ns + "c"):
                kind, v = c.get("t"), c.find(ns + "v")
                if kind == "s" and v is not None:
                    val = shared[int(v.text)]
                elif kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(ns + "t"))
                elif v is not None and v.text is not None:
                    val = to_float(v.text)
                    if val is None:
                        val = v.text
                else:
                    val = None
                cells[col_index(c.get("r", "A"))] = val
            rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
        book[sh.get("name")] = rows
    return book


def first_sheet_rows(data):
    if data[:2] != b"PK":
        raise ValueError("Excel（.xlsx）の形ではありません")
    return next(iter(read_xlsx(data).values()))


def estat_month_code(month):
    """e-Stat の一覧の「月」の指定。1〜3月は110103、4〜6月は120406、7〜9月は230709、10〜12月は241012 に月を2桁で付ける。"""
    head = {1: "110103", 2: "120406", 3: "230709", 4: "241012"}[(month - 1) // 3 + 1]
    return f"{head}{month:02d}"


def find_touyu_files(year, month):
    """e-Stat の一覧から、その月の「主要品目の都市別小売価格」（品目3001〜の表）と、市町村銘柄設定一覧の番号を探す。

    まだ公表されていない月は (None, None, None) を返す。
    """
    page = get(ESTAT_LIST.format(year=year, code=estat_month_code(month)), as_json=False)
    want = f"主要品目の都市別小売価格【{year}年{month}月】"
    table = brand = released = None
    for block in page.split("stat-dataset_list-item")[1:]:
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", block)))
        if want not in text:
            continue
        sid = re.search(r"statInfId=(\d+)&(?:amp;)?fileKind=(\d)", block)
        if not sid:
            continue
        if "「3001" in text and sid.group(2) == "0":
            table = sid.group(1)
            m = re.search(r"公開（更新）日 (\d{4}-\d{2}-\d{2})", text)
            released = m.group(1) if m else None
        elif "市町村銘柄設定一覧" in text:
            brand = sid.group(1)
    return table, brand, released


def touyu_prices(rows):
    """都市別小売価格の表から、灯油（品目3701）の県庁所在市の値段を取り出す。{都道府県コード: 円}。"""
    names = kero = None
    for r in rows:
        cells = [str(v).strip() if v is not None else "" for v in r]
        if names is None and ("札幌市" in cells or "札幌" in cells):
            names = cells
        if kero is None and "灯油" in cells and any(c in ("3701", "3701.0") for c in cells):
            kero = r
    if names is None or kero is None:
        raise ValueError("都市名の行か灯油の行が見つかりません")
    prices = {}
    for code, (city, alias) in CAPITALS.items():
        for cand in (city,) + alias:
            if cand in names:
                j = names.index(cand)
                v = kero[j] if j < len(kero) else None
                if isinstance(v, (int, float)):
                    prices[code] = int(round(v))
                break
    return prices


def local_brand_capitals(data):
    """市町村銘柄設定一覧（CSV）から、灯油を基本銘柄とは別の銘柄で調べている県庁所在市を返す。"""
    text = data.decode("cp932", "replace")
    by_name = {city: code for code, (city, _) in CAPITALS.items()}
    out = set()
    for r in csv.reader(io.StringIO(text)):
        if len(r) >= 5 and r[1].strip() == "3701" and r[4].strip() in by_name:
            out.add(by_name[r[4].strip()])
    return sorted(out)


def tokyo_series(book):
    """東京都区部の灯油の長い表から、店頭売り（2008年11月から）の月ごとの値段を取り出す。{"YYYY-MM": 円}。"""
    rows = next(v for k, v in book.items() if "3701" in k and "グラフ" not in k)
    out = {}
    for r in rows:
        if len(r) < 4 or not isinstance(r[1], float) or not isinstance(r[3], float):
            continue
        d = date(1899, 12, 30) + timedelta(days=int(r[1]))
        out[f"{d.year}-{d.month:02d}"] = int(round(r[3]))
    return out


def next_release(data, today):
    """統計局の公表予定（UTF-16 の XML）から、都市別小売価格の次の公表を返す。"""
    text = data.decode("utf-16")
    block = re.search(r'<class_1 name="【動向編】主要品目の都市別小売価格[^"]*">(.*?)</class_1>', text, re.S)
    if not block:
        return None
    pat = (r'<class_2 name="(\d{4})年(\d{1,2})月分">.*?<release_year>(\d+)</release_year>\s*'
           r'<release_month>(\d+)</release_month>\s*<release_day>(\d+)</release_day>')
    for m in re.finditer(pat, block.group(1), re.S):
        when = date(int(m.group(3)), int(m.group(4)), int(m.group(5)))
        if when > today:
            return {"month": f"{m.group(1)}-{int(m.group(2)):02d}", "date": when.isoformat()}
    return None


def month_shift(key, n):
    y, m = map(int, key.split("-"))
    i = y * 12 + (m - 1) + n
    return f"{i // 12}-{i % 12 + 1:02d}"


def month_text(key):
    y, m = map(int, key.split("-"))
    return f"{y}年{m}月"


def iso_text(iso):
    d = date.fromisoformat(iso)
    return f"{d.year}年{d.month}月{d.day}日"


def update_touyu(data, today):
    """新しく公表された月の値段と、東京都区部の推移・次の公表予定を取りに行く。

    取れなくてもページは前の値で作る（ここで止めない）。返すのは (書き換えたか, 気になったことの一覧)。
    """
    notes = []
    changed = False
    months = data.setdefault("months", {})
    key = max(months) if months else "2019-12"
    for _ in range(3):  # 取りこぼした月があっても3か月分までは追いつく
        key = month_shift(key, 1)
        y, m = map(int, key.split("-"))
        if (y, m) >= (today.year, today.month):
            break
        try:
            table, brand, released = find_touyu_files(y, m)
        except Exception as err:
            notes.append(f"e-Stat の一覧を読めませんでした（{month_text(key)}分）: {err}")
            break
        if not table:
            break  # まだ公表されていない
        try:
            prices = touyu_prices(first_sheet_rows(get_bytes(ESTAT_FILE.format(sid=table, kind=0))))
        except Exception as err:
            notes.append(f"{month_text(key)}分の表を読めませんでした: {err}")
            break
        odd = [c for c, v in prices.items() if not 500 <= v <= 6000]
        if len(prices) < 40 or odd:
            notes.append(f"{month_text(key)}分の値がそろいません（{len(prices)}市・範囲外 {odd}）")
            break
        months[key] = prices
        data.setdefault("released", {})[key] = released
        data.setdefault("statinf", {})[key] = table
        if brand:
            try:
                data.setdefault("local_brand", {})[key] = local_brand_capitals(
                    get_bytes(ESTAT_FILE.format(sid=brand, kind=1)))
            except Exception as err:
                notes.append(f"{month_text(key)}分の市町村銘柄設定一覧を読めませんでした: {err}")
        changed = True
        print(f"  灯油: {month_text(key)}分を足しました（{len(prices)}市）")
        time.sleep(2)
    try:
        tokyo = tokyo_series(read_xlsx(get_bytes(TOKYO_KEROSENE)))
        if len(tokyo) >= len(data.get("tokyo", {})) and tokyo != data.get("tokyo"):
            data["tokyo"] = tokyo
            changed = True
        print(f"  灯油: 東京都区部の推移 {min(tokyo)}〜{max(tokyo)}")
    except Exception as err:
        notes.append(f"東京都区部の推移を読めませんでした: {err}")
    try:
        nxt = next_release(get_bytes(KOURI_SCHEDULE), today)
        if nxt and nxt != data.get("next_release"):
            data["next_release"] = nxt
            changed = True
    except Exception as err:
        notes.append(f"公表予定を読めませんでした: {err}")
    return changed, notes


def touyu_stale_days(data, today):
    """いちばん新しい月の末日から何日たったか。"""
    key = max(data["months"])
    end = date.fromisoformat(month_shift(key, 1) + "-01") - timedelta(days=1)
    return (today - end).days


def summarize_touyu(data):
    """いちばん新しい月の、県庁所在市ごとの値段と前月差・前年同月差。安い順に並べる。"""
    months = data["months"]
    latest = max(months)
    cur, prev, last_year = months[latest], months.get(month_shift(latest, -1), {}), months.get(month_shift(latest, -12), {})
    brand = set(data.get("local_brand", {}).get(latest, []))
    rows = []
    for code, pref in PREF.items():
        v = cur.get(code)
        rows.append({
            "pref": pref, "code": code, "city": CAPITALS[code][0], "val": v,
            "label": f"{v:,}" if v is not None else "—",
            "mom": v - prev[code] if v is not None and code in prev else None,
            "yoy": v - last_year[code] if v is not None and code in last_year else None,
            "brand": code in brand,
        })
    rows.sort(key=lambda r: (r["val"] is None, r["val"] or 0, r["code"]))
    return latest, rows


def average(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals)) if vals else None


def touyu_prose(data, latest, rows):
    """値段の本文。言い回しは固定で、毎月変わるのは月と数字だけ。"""
    got = [r for r in rows if r["val"] is not None]
    low, high = got[0], got[-1]
    avg = average(r["val"] for r in rows)
    text = (f"総務省統計局の小売物価統計調査をもとに、47都道府県の県庁所在市の灯油18リットルの店頭価格を、"
            f"安い順に並べています。{month_text(latest)}分は、最も安い{low['city']}（{low['pref']}）で{low['val']:,}円、"
            f"最も高い{high['city']}（{high['pref']}）で{high['val']:,}円でした。")
    last = data["months"].get(month_shift(latest, -12))
    if avg is not None and last:
        old = average(last.get(code) for code in PREF)
        if old:
            diff = avg - old
            how = "高く" if diff > 0 else "安く" if diff < 0 else "同じ値段に"
            amount = f"{abs(diff):,}円" if diff else ""
            text += (f"47市の平均は{avg:,}円で、1年前の同じ月（{old:,}円）より{amount}{how}なっています"
                     f"（平均は当サイトの計算）。")
    tokyo = next(r for r in rows if r["code"] == "13")
    if tokyo["val"] is not None:
        text += f"東京都区部は{tokyo['val']:,}円です。"
    nxt = data.get("next_release")
    if nxt:
        text += f"値段は毎月1回の調査で、次の{month_text(nxt['month'])}分は{iso_text(nxt['date'])}に公表される予定です。"
    return text


def tokyo_yearly(tokyo):
    """東京都区部の店頭売りの年平均（12か月そろった年だけ）。"""
    years = {}
    for key, v in tokyo.items():
        years.setdefault(int(key[:4]), []).append(v)
    return [(y, round(sum(v) / len(v))) for y, v in sorted(years.items()) if len(v) == 12]


def tokyo_chart_svg(tokyo):
    """東京都区部の店頭売りの月ごとの値段を、折れ線で描く。"""
    keys = sorted(tokyo)
    vals = [tokyo[k] for k in keys]
    w, h, left, right, top, bottom = 640, 240, 52, 12, 14, 30
    lo = min(vals) // 500 * 500
    hi = -(-max(vals) // 500) * 500
    def x(i):
        return left + (w - left - right) * i / max(1, len(keys) - 1)
    def y(v):
        return top + (h - top - bottom) * (hi - v) / max(1, hi - lo)
    parts = []
    for v in range(lo, hi + 1, 500):
        parts.append(f'<line x1="{left}" y1="{y(v):.1f}" x2="{w - right}" y2="{y(v):.1f}" class="grid"/>'
                     f'<text x="{left - 6}" y="{y(v) + 4:.1f}" class="ax" text-anchor="end">{v:,}</text>')
    for i, k in enumerate(keys):
        if k.endswith("-01") and int(k[:4]) % 2 == 0:
            parts.append(f'<text x="{x(i):.1f}" y="{h - 10}" class="ax" text-anchor="middle">{k[:4]}</text>')
    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    parts.append(f'<polyline points="{points}" class="line"/>')
    title = (f"東京都区部の灯油18リットルの店頭価格（{month_text(keys[0])}〜{month_text(keys[-1])}）。"
             f"最も安い月は{min(vals):,}円、最も高い月は{max(vals):,}円")
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{title}">'
            f"<title>{title}</title>{''.join(parts)}</svg>")


# ---------- 読み物：灯油は何月が安い？ ----------

def full_years(series):
    """{"YYYY-MM": 値} を {年: {月: 値}} にする。12か月そろった年だけ。"""
    years = {}
    for key, v in series.items():
        if v is not None:
            years.setdefault(int(key[:4]), {})[int(key[5:7])] = v
    return {y: r for y, r in years.items() if len(r) == 12}


def low_high(row):
    """その年でいちばん安い月・高い月（同じ値が並んだら全部）と、その値。"""
    lo, hi = min(row.values()), max(row.values())
    return ([m for m in sorted(row) if row[m] == lo], lo, [m for m in sorted(row) if row[m] == hi], hi)


def month_counts_text(counts):
    """{月: 回数} を「1月が8回、8月が3回、2月・12月が2回」の形にする（多い順・0回は書かない）。"""
    by_n = {}
    for m, n in counts.items():
        if n:
            by_n.setdefault(n, []).append(m)
    return "、".join("・".join(f"{m}月" for m in sorted(ms)) + f"が{n}回"
                    for n, ms in sorted(by_n.items(), reverse=True))


def years_text(years):
    """[2010, 2011, 2012, 2018] を「2010〜2012・2018年」の形にする。"""
    ys = sorted(years)
    if not ys:
        return ""
    parts, start, prev = [], ys[0], ys[0]
    for y in ys[1:] + [None]:
        if y is not None and y == prev + 1:
            prev = y
            continue
        parts.append(f"{start}" if start == prev else f"{start}〜{prev}")
        if y is not None:
            start = prev = y
    return "・".join(parts) + "年"


def touyu_month_stats(data):
    """東京都区部と47市で、その年いちばん安かった月を数える（12か月そろった年だけ）。"""
    tokyo = full_years(data.get("tokyo", {}))
    t_rows = []
    t_count = {m: 0 for m in range(1, 13)}
    for y in sorted(tokyo):
        lo_m, lo, hi_m, hi = low_high(tokyo[y])
        t_rows.append({"year": y, "lo_m": lo_m, "lo": lo, "hi_m": hi_m, "hi": hi})
        for m in lo_m:
            t_count[m] += 1
    jan_low = [r["year"] for r in t_rows if 1 in r["lo_m"]]
    jan_high = [r["year"] for r in t_rows if 1 in r["hi_m"]]

    cities = {}
    for key, prices in data.get("months", {}).items():
        for code, v in prices.items():
            cities.setdefault(code, {})[key] = v
    c_count = {m: 0 for m in range(1, 13)}
    c_n = c_summer = 0
    c_years = set()
    for series in cities.values():
        for y, row in full_years(series).items():
            lo_m = low_high(row)[0]
            c_n += 1
            c_years.add(y)
            c_summer += any(m in SUMMER_MONTHS for m in lo_m)
            for m in lo_m:
                c_count[m] += 1

    # 47市の平均の値段が、その年の12か月の平均より高いか安いか（年ごと・月ごと）
    avg_by_key = {k: sum(p.values()) / len(p) for k, p in data.get("months", {}).items() if p}
    dev = {}
    for y, row in full_years(avg_by_key).items():
        mean = sum(row.values()) / 12
        dev[y] = {m: round(v - mean) for m, v in row.items()}
    dev_avg = {m: round(sum(d[m] for d in dev.values()) / len(dev)) for m in range(1, 13)} if dev else {}
    both_signs = sum(1 for m in range(1, 13)
                     if any(d[m] > 0 for d in dev.values()) and any(d[m] < 0 for d in dev.values()))

    return {
        "t_rows": t_rows, "t_count": t_count,
        "t_ties": [r["year"] for r in t_rows if len(r["lo_m"]) > 1],
        "t_summer": sum(1 for r in t_rows if any(m in SUMMER_MONTHS for m in r["lo_m"])),
        "jan_low": jan_low,
        "jan_low_rising": [y for y in jan_low if tokyo[y][12] > tokyo[y][1]],
        "jan_high": jan_high,
        "jan_high_falling": [y for y in jan_high if tokyo[y][12] < tokyo[y][1]],
        "c_count": c_count, "c_n": c_n, "c_summer": c_summer,
        "c_years": (min(c_years), max(c_years)) if c_years else None,
        "dev": dev, "dev_avg": dev_avg, "both_signs": both_signs,
        "c_rising": sum(1 for d in dev.values() if d[12] > d[1]),
    }


def signed(v, digits=0):
    """＋−つきの数字。マイナスは「−」にする（表と本文で読みやすくするため）。"""
    text = f"{v:+,.{digits}f}" if digits else f"{round(v):+,}"
    return text.replace("-", "−")


def touyu_yasui_text(st, data):
    """読み物の本文。言い回しは固定で、データが増えると変わるのは年と数字だけ。"""
    rows = st["t_rows"]
    n = len(rows)
    counts = st["t_count"]
    ties = "、".join(f"{r['year']}年は" + "と".join(f"{m}月" for m in r["lo_m"])
                    for r in rows if len(r["lo_m"]) > 1)
    tie_note = f"（{ties}が同じ値で並んだため、それぞれに数えています）" if ties else ""
    lede = ("総務省の灯油の値段からは、季節で決まる「安い月」は読み取れませんでした。"
            f"東京都区部の{rows[0]['year']}年から{rows[-1]['year']}年までの{n}年で、その年いちばん安かった月を数えると、"
            f"{month_counts_text(counts)}でした{tie_note}。"
            f"6〜8月（夏）がいちばん安かった年は{st['t_summer']}年だけです。")
    if max(counts, key=counts.get) == 1 and len(st["jan_low_rising"]) == len(st["jan_low"]):
        lede += "いちばん多い1月は、どれも1年を通して値上がりした年でした。"
    text = {"lede": lede}

    jl, jh, jf = st["jan_low"], st["jan_high"], st["jan_high_falling"]
    jan = ""
    if jl:
        jan = (f"1月がいちばん安かった{len(jl)}年（{years_text(jl)}）は、"
               + ("どれも" if len(st["jan_low_rising"]) == len(jl) else f"そのうち{len(st['jan_low_rising'])}年が")
               + "1年のはじめ（1月）より終わり（12月）のほうが高い、値上がりの年でした。")
        if jh:
            jan += (f"反対に、1月がその年でいちばん高かった{len(jh)}年（{years_text(jh)}）は、"
                    + ("どれも" if len(jf) == len(jh) else f"そのうち{len(jf)}年が")
                    + "12月のほうが1月より安い、値下がりの年でした。")
            if len(jf) == len(jh):
                jan += "いちばん安い月がどこに来るかは、季節よりも、その年に値段が上がったか下がったかで変わっていました。"
    text["jan"] = jan

    pct = round(100 * st["c_summer"] / st["c_n"]) if st["c_n"] else 0
    city = ""
    if st["c_n"]:
        y0, y1 = st["c_years"]
        top = max(st["c_count"], key=st["c_count"].get)
        top_pct = round(100 * st["c_count"][top] / st["c_n"])
        city = (f"47都道府県の県庁所在市それぞれの{y0}年から{y1}年まで（{st['c_n']}の組み合わせ）で数えると、"
                f"その年いちばん安い月（同じ値で並んだ月を含む）に6〜8月が入っていたのは{st['c_summer']}（{pct}％）でした。"
                f"いちばん多かったのは{top}月で、{st['c_count'][top]}（{top_pct}％）です。")
        if top == 1 and st["c_rising"] * 2 > len(st["dev"]):
            city += (f"この{len(st['dev'])}年のうち{st['c_rising']}年は、47市の平均で12月のほうが1月より高い"
                     "値上がりの年で、東京都区部と同じ形です。")
    text["city"] = city

    dv = st["dev_avg"]
    dev_note = ""
    if dv:
        lo_m, hi_m = min(dv, key=dv.get), max(dv, key=dv.get)
        each = ("どの月も、年によって＋の年と−の年の両方がありました。" if st["both_signs"] == 12
                else f"12か月のうち{st['both_signs']}か月は、年によって＋の年と−の年の両方がありました。")
        dev_note = ("＋はその年の12か月の平均より高い月、−は安い月です（47市の平均の値段・当サイトの計算）。"
                    + each
                    + f"{len(st['dev'])}年の平均にすると、いちばん安い側の{lo_m}月でも{signed(dv[lo_m])}円、"
                    f"いちばん高い側の{hi_m}月でも{signed(dv[hi_m])}円で、"
                    f"1リットルあたりにすると{signed(dv[lo_m] / 18, 1)}円から{signed(dv[hi_m] / 18, 1)}円です。")
    text["dev"] = dev_note

    text["summer"] = (f"上のとおり、夏（6〜8月）がいちばん安かった年は、東京都区部で{n}年のうち{st['t_summer']}年でした。"
                      f"47市でも、いちばん安い月に6〜8月が入っていたのは{pct}％です。"
                      "灯油の保管について、トヨトミの石油ファンヒーター（LC-32P）の取扱説明書は"
                      "「翌シーズンに持ち越さないようにしてください。」と書いています。")

    latest = max(data["months"])
    avg_now = average(data["months"][latest].values())
    now = f"{month_text(latest)}分の47市の平均は{avg_now:,}円です"
    jan_key = f"{latest[:4]}-01"
    if latest != jan_key and jan_key in data["months"]:
        now += (f"（{latest[:4]}年1月分は{average(data['months'][jan_key].values()):,}円）")
    text["now"] = now + "。"

    brand = data.get("local_brand", {}).get(latest) or []
    text["brand"] = ""
    if brand:
        names = "・".join(CAPITALS[c][0] for c in brand if c in CAPITALS)
        text["brand"] = (f"{month_text(latest)}分の{names}の値は、基本の銘柄の出回りが少ないため、"
                         "総務省がその市の実情に合わせて決めた銘柄の値です（総務省の注記）。")
    return text


def touyu_yasui_problems(st):
    """本文の言い回しが、いまのデータで成り立つか。

    成り立たないときは、この読み物だけ書き換えずに前の版を残し、ほかのページは公開したうえで知らせる
    （main で warnings に入れる。自動実行が「失敗」になり、GitHub からメールが届く）。
    """
    problems = []
    n = len(st["t_rows"])
    name = "読み物「灯油は何月が安い？」"
    if n < 10:
        problems.append(f"{name}：東京都区部の12か月そろった年が少ない（{n}年）")
        return problems
    if st["t_summer"] * 2 >= n:
        problems.append(f"{name}：東京都区部で夏がいちばん安い年が半分以上（{st['t_summer']}/{n}年）。題名と本文を見直す")
    if not st["c_n"]:
        problems.append(f"{name}：47市の12か月そろった年が無い")
    elif st["c_summer"] * 2 >= st["c_n"]:
        problems.append(f"{name}：47市で夏がいちばん安い組み合わせが半分以上。題名と本文を見直す")
    for m, c in st["t_count"].items():
        # 1月は値上がりの年にいちばん安くなるので、値下がりの年に1月がいちばん高い例がある限り、多くても説明がつく
        if c * 2 > n and (m != 1 or not st["jan_high"]):
            problems.append(f"{name}：東京都区部で{m}月がいちばん安い年が半分を超えた（{c}/{n}年）。"
                            "「季節で決まる安い月は読み取れない」を見直す")
    if len(st["jan_high_falling"]) < len(st["jan_high"]):
        problems.append(f"{name}：1月がいちばん高かった年に、値下がりでない年がある。"
                        "「上がったか下がったかで変わっていた」を見直す")
    return problems


# ---------- 読み物：寒い県ランキング ----------

# (キー, 比べ方, 寒いのはどちらか, 単位)
SAMUI_MEASURES = [
    ("jan_tmin", "1月の日最低気温（朝の冷え込み）", "low", "℃"),
    ("jan_tmax", "1月の日最高気温（昼の寒さ）", "low", "℃"),
    ("jan_tavg", "1月の平均気温", "low", "℃"),
    ("year_tavg", "1年の平均気温", "low", "℃"),
    ("fuyubi", "冬日の日数（1年）", "high", "日"),
    ("mafuyubi", "真冬日の日数（1年）", "high", "日"),
]


def load_heinen_month():
    with open(HEINEN_MONTH_FILE, encoding="utf-8") as f:
        return json.load(f)


def samui_rows(hm):
    """月ごとの平年値から、比べ方ごとの値を1地点1行にする。"""
    rows = []
    for code, s in sorted(hm.get("stations", {}).items()):
        jan, year = s["months"]["1"], s["year"]
        rows.append({"code": code, "pref": s["pref"], "name": s["name"],
                     "jan_tmin": jan["tmin"], "jan_tmax": jan["tmax"], "jan_tavg": jan["tavg"],
                     "year_tavg": year["tavg"], "fuyubi": year["d_min_lt0"], "mafuyubi": year["d_max_lt0"]})
    return rows


def coldest(rows, key, way):
    """いちばん寒い地点（同じ値が並んだら全部）と、その値。"""
    best = min(r[key] for r in rows) if way == "low" else max(r[key] for r in rows)
    return [r for r in rows if r[key] == best], best


def place_text(rs):
    return "・".join(f"{r['pref']}（{r['name']}）" for r in rs)


def value_text(v, unit):
    return f"{v:.1f}".replace("-", "−") + unit


def samui_text(rows):
    """読み物の本文。平年値は10年ごとにしか変わらないが、数字はいつもデータから作る。"""
    honshu = [r for r in rows if r["code"] in HONSHU]
    win = {key: coldest(rows, key, way) + coldest(honshu, key, way) for key, _, way, _ in SAMUI_MEASURES}
    text = {}
    lede = "比べ方によって変わります。気象庁の平年値（1991〜2020年）で、47都道府県の県庁所在地など1地点ずつを比べました。"
    national = {tuple(r["code"] for r in w[0]) for w in win.values()}
    if len(national) == 1:
        lede += f"全国では、どの比べ方でも{place_text(win['jan_tmin'][0])}がいちばん寒い地点です。"
    t_min, t_max, f_d, m_d = win["jan_tmin"], win["jan_tmax"], win["fuyubi"], win["mafuyubi"]
    lede += (f"本州に限ると、朝の冷え込み（1月の日最低気温の平年値）は{place_text(t_min[2])}の{value_text(t_min[3], '℃')}、"
             f"昼の寒さ（1月の日最高気温の平年値）は{place_text(t_max[2])}の{value_text(t_max[3], '℃')}がいちばん低く、"
             f"答えが分かれます。1年の冬日（最低気温が0℃未満の日）がいちばん多いのは{place_text(f_d[2])}の{value_text(f_d[3], '日')}、"
             f"真冬日（最高気温が0℃未満の日）がいちばん多いのは{place_text(m_d[2])}の{value_text(m_d[3], '日')}です。")
    text["lede"] = lede
    gap = ""
    if f_d[0][0]["code"] != f_d[2][0]["code"] and f_d[1] - f_d[3] < 1:
        gap = (f"冬日の日数は、全国でいちばん多い{place_text(f_d[0])}の{value_text(f_d[1], '日')}と、"
               f"{place_text(f_d[2])}の{value_text(f_d[3], '日')}で、ほとんど同じです。")
    text["gap"] = gap

    differ = [r for r in rows
              if r["name"] not in CAPITALS.get(r["code"], ("", ()))[0]
              and r["name"] not in CAPITALS.get(r["code"], ("", ()))[1]]
    point = "比べているのは、各都道府県の気象台などの観測点1か所の平年値です。"
    if differ:
        point += ("・".join(f"{r['pref']}は{r['name']}" for r in differ)
                  + "の値です（県庁所在地ではなく、その市に地方気象台があるため）。")
    point += ("同じ都道府県の中でも、山あいや内陸には、ここに並べた値より寒い地点があります。"
              "平年値は30年間の平均なので、その年の冬の寒さは年によって前後します。")
    text["point"] = point
    text["defs"] = "冬日・真冬日は気象庁の用語で、冬日は「日最低気温が０度未満の日。」、真冬日は「日最高気温が０度未満の日。」です。"
    return text, win


def samui_problems(rows, win):
    """題名と本文の言い回しが、いまのデータで成り立つか。成り立たなければ、この読み物だけ書き換えずに知らせる。"""
    name = "読み物「寒い県ランキング」"
    if len(rows) != 47:
        return [f"{name}：月ごとの平年値が47地点そろわない（{len(rows)}）"]
    problems = []
    if [r["code"] for r in win["jan_tmin"][2]] != ["03"]:
        problems.append(f"{name}：本州で1月の日最低気温がいちばん低いのが岩手（盛岡）でない。題名を見直す")
    if [r["code"] for r in win["jan_tmax"][2]] != ["02"]:
        problems.append(f"{name}：本州で1月の日最高気温がいちばん低いのが青森でない。題名を見直す")
    return problems


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

    touyu = ctx["touyu"]
    if not touyu.get("months"):
        problems.append("灯油の値段のデータが無い")
    else:
        latest = max(touyu["months"])
        cur = touyu["months"][latest]
        if len(cur) < 40:
            problems.append(f"灯油の値段がそろわない（{month_text(latest)}分・{len(cur)}市）")
        odd = [f"{CAPITALS[c][0]} {v}" for c, v in cur.items() if not 500 <= v <= 6000]
        if odd:
            problems.append(f"灯油の値段が異常値: {odd}")

    for page in ALL_PAGES:
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
</script>
<script src="/assets/track.js" defer></script>"""

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
  svg.chart{display:block;width:100%;height:auto;min-width:30rem}
  svg.chart .grid{stroke:var(--rule);stroke-width:1}
  svg.chart .ax{font-size:11px;fill:var(--ink-soft);font-family:var(--gothic)}
  svg.chart .line{fill:none;stroke:var(--hi);stroke-width:2}
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
    return "".join(f'<li><a href="{p["path"]}">{p["h1"]}</a></li>'
                   for p in ALL_PAGES if p["id"] != current_id)


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


TOUYU_MAP = {"name": "灯油18リットルの値段", "scale": ("heat",), "digits": 0, "unit": "円"}
SAMUI_MAP = {"name": "1月の日最低気温の平年値", "scale": ("heat",), "digits": 1, "unit": "℃"}


def signed_yen(v):
    return "—" if v is None else f"{v:+,}円"


def touyu_stamp(data, latest):
    released = data.get("released", {}).get(latest)
    text = f"総務省 小売物価統計調査（動向編）／{month_text(latest)}分"
    if released:
        text += f"（{iso_text(released)}公表）"
    nxt = data.get("next_release")
    if nxt:
        text += f"／次は{month_text(nxt['month'])}分を{iso_text(nxt['date'])}に公表予定"
    return text


def render_touyu(data, latest, rows, items, prose):
    e = html.escape
    got = [r["val"] for r in rows if r["val"] is not None]
    low, high = (min(got), max(got)) if got else (0, 0)

    table_rows = []
    for r in rows:
        mark = ' <span class="tag cold">※</span>' if r["brand"] else ""
        val = f"{r['val']:,}円" if r["val"] is not None else "—"
        table_rows.append(
            f'<tr id="p{r["code"]}"><th scope="row">{e(r["pref"])}<span class="sub">{e(r["city"])}</span></th>'
            f"<td>{val}{mark}</td><td>{signed_yen(r['mom'])}</td><td>{signed_yen(r['yoy'])}</td></tr>")
    brand_note = ""
    marked = [r for r in rows if r["brand"]]
    if marked:
        names = "・".join(f"{r['pref']}（{r['city']}）" for r in marked)
        brand_note = (f'<p class="note">※ {e(names)}は、基本の銘柄（店頭売り）の出回りが少ないため、'
                      "総務省がその市の実情に合わせて出回りの多い銘柄を決めて調べている値です（総務省の注記）。"
                      "ほかの市とそのまま比べられないことがあります。</p>")

    tokyo = data.get("tokyo", {})
    history = ""
    if len(tokyo) >= 24:
        first, last = min(tokyo), max(tokyo)
        yearly = "".join(f'<tr><th scope="row">{y}年</th><td>{v:,}円</td></tr>' for y, v in tokyo_yearly(tokyo))
        history = f"""
  <h2>東京都区部の推移（{month_text(first)}〜{month_text(last)}）</h2>
  <div class="mapwrap">{tokyo_chart_svg(tokyo)}</div>
  <p class="note">東京都区部の灯油18リットルの店頭価格です。{month_text(last)}分は{tokyo[last]:,}円でした（東京都区部だけは、都市別の表より早く公表されます）。</p>
  <table class="cols">
    <caption>東京都区部の年平均（12か月の平均・当サイトの計算）</caption>
    <thead><tr><th scope="col">年</th><th scope="col">18リットル</th></tr></thead>
    <tbody>
      {yearly}
    </tbody>
  </table>
  <p class="note">出典：総務省統計局「小売物価統計調査（動向編）」東京都区部の灯油の小売価格（長期時系列）を加工して作成。</p>
"""

    prev, last_year = month_shift(latest, -1), month_shift(latest, -12)
    body = f"""  <p class="stamp">{e(touyu_stamp(data, latest))}</p>

  <p class="lede">{e(prose)}</p>

  <h2>全国の分布（{month_text(latest)}分・18リットルの店頭価格）</h2>
  <div class="mapwrap">{japan_map_svg(rows, TOUYU_MAP, low, high)}</div>
  <p class="mapkey"><span>{low:,}円</span>{legend_svg(TOUYU_MAP, low, high)}<span>{high:,}円</span></p>
  <p class="note">都道府県をおおよその位置に並べたもので、実際の面積や形とは異なります。マス目の数字は、その都道府県の県庁所在市の値段（円）です。マス目を押すと<a href="#hyou">下の表</a>の該当する行に移動します。</p>

  <h2 id="hyou">都道府県別 県庁所在市の灯油の値段</h2>
  <table class="cols">
    <caption>{month_text(latest)}分・18リットルの店頭価格（安い順）。前月差・前年同月差は、同じ市の{month_text(prev)}分・{month_text(last_year)}分との差です</caption>
    <thead><tr><th scope="col">都道府県</th><th scope="col">18リットル</th><th scope="col">前月差</th><th scope="col">前年同月差</th></tr></thead>
    <tbody>
      {"".join(table_rows)}
    </tbody>
  </table>
  {brand_note}
  <p class="note">値段は総務省統計局の小売物価統計調査（毎月1回）によるもので、灯油は「白灯油，詰め替え売り，店頭売り」の18リットルの値段です。出典：総務省統計局「小売物価統計調査（動向編）」主要品目の都市別小売価格（<a href="https://www.e-stat.go.jp/" rel="noopener">政府統計の総合窓口 e-Stat</a>）を加工して作成。</p>
{history}
  <h2>読み物</h2>
  <ul class="links"><li><a href="{TOUYU_YASUI['path']}">{e(TOUYU_YASUI['h1'])}</a>（この値段で、その年いちばん安かった月を数えました）</li></ul>

  <h2>毎週の値段（都道府県別）</h2>
  <p class="note">最新の週ごとの値段（都道府県別）は、資源エネルギー庁の「石油製品価格調査」で毎週公表されています。</p>
  <ul class="links"><li><a href="{WEEKLY_OFFICIAL}" rel="noopener">資源エネルギー庁 石油製品価格調査 調査の結果</a></li></ul>

  <h2>{e(TOUYU['heading'])}</h2>
  {items_html(TOUYU['keywords'], items)}
  <p class="note">{ITEMS_NOTE}</p>
"""
    return page_html(TOUYU, body)


def render_touyu_yasui(page, data, st, items, text):
    """読み物「灯油は何月が安い？」。数字と表はデータから毎回作る。"""
    e = html.escape
    tokyo, months = data["tokyo"], data["months"]
    stamp = (f"総務省 小売物価統計調査（動向編）の灯油18リットルの値段（東京都区部 {month_text(min(tokyo))}〜"
             f"{month_text(max(tokyo))}分・47都道府県の県庁所在市 {month_text(min(months))}〜{month_text(max(months))}分）"
             f"をもとに当サイトが計算／公開 {iso_text(page['published'])}")

    def cell(ms, v):
        return "・".join(f"{m}月" for m in ms) + f'<span class="sub">{v:,}円</span>'

    rows = st["t_rows"]
    t_body = "".join(f'<tr><th scope="row">{r["year"]}年</th><td>{cell(r["lo_m"], r["lo"])}</td>'
                     f'<td>{cell(r["hi_m"], r["hi"])}</td><td>{r["hi"] - r["lo"]:,}円</td></tr>' for r in rows)
    years = sorted(st["dev"])
    d_head = "".join(f'<th scope="col">{y}年</th>' for y in years)
    d_body = "".join(
        f'<tr><th scope="row">{m}月</th>' + "".join(f"<td>{signed(st['dev'][y][m])}</td>" for y in years)
        + f"<td>{signed(st['dev_avg'][m])}</td></tr>" for m in range(1, 13))

    body = f"""  <p class="stamp">{e(stamp)}</p>

  <p class="lede">{e(text['lede'])}</p>

  <h2>東京都区部　その年いちばん安い月と高い月（{rows[0]['year']}〜{rows[-1]['year']}年）</h2>
  <table class="cols">
    <caption>灯油18リットルの店頭価格。同じ値の月が並んだ年は、その月をすべて書いています（当サイトの集計）</caption>
    <thead><tr><th scope="col">年</th><th scope="col">いちばん安い月</th><th scope="col">いちばん高い月</th><th scope="col">差</th></tr></thead>
    <tbody>
      {t_body}
    </tbody>
  </table>

  <h2>いちばん安い月が1月の年が多いのは</h2>
  <p>{e(text['jan'])}</p>

  <h2>47都道府県の県庁所在市では</h2>
  <p>{e(text['city'])}</p>
  <div class="mapwrap">
  <table class="cols">
    <caption>47市の平均の値段の、その年の12か月の平均との差（円・18リットル）</caption>
    <thead><tr><th scope="col">月</th>{d_head}<th scope="col">平均</th></tr></thead>
    <tbody>
      {d_body}
    </tbody>
  </table>
  </div>
  <p class="note">{e(text['dev'])}</p>

  <h2>夏にまとめて買えば安い？</h2>
  <p>{e(text['summer'])}</p>
  <ul class="links"><li><a href="{TOYOTOMI_MANUAL}" rel="noopener">トヨトミ LC-32P 取扱説明書（PDF）</a></li></ul>

  <h2>いまの値段</h2>
  <p>{e(text['now'])}都道府県別の最新の値段と前の月からの値動きは、毎月入れ替えている<a href="{TOUYU['path']}">{e(TOUYU['h1'])}</a>のページにあります。週ごとの値段は、資源エネルギー庁の「石油製品価格調査」で毎週公表されています。</p>
  <ul class="links"><li><a href="{WEEKLY_OFFICIAL}" rel="noopener">資源エネルギー庁 石油製品価格調査 調査の結果</a></li></ul>

  <h2>調べ方と出典</h2>
  <p class="note">値段は総務省統計局「小売物価統計調査（動向編）」の灯油（「白灯油，詰め替え売り，店頭売り」・18リットル）です。総務省の説明では、月ごとの値段は「原則として、毎月12日を含む週の水曜日、木曜日又は金曜日のいずれか１日を調査日としています。」（<a href="{KOURI_HANASHI}" rel="noopener">小売物価統計調査のはなし（PDF）</a>）。月に1回の値なので、その月の中の上がり下がりは表れません。{e(text['brand'])}12か月そろった年だけを数え、いちばん安い月・高い月と平均は当サイトの計算です。</p>
  <p class="note">出典：総務省統計局「小売物価統計調査（動向編）」主要品目の都市別小売価格、東京都区部の灯油の小売価格（長期時系列）（<a href="https://www.e-stat.go.jp/" rel="noopener">政府統計の総合窓口 e-Stat</a>）を加工して作成。</p>

  <h2>{e(page['heading'])}</h2>
  {items_html(page['keywords'], items)}
  <p class="note">{ITEMS_NOTE}</p>
"""
    return page_html(page, body)


def render_samui(page, rows, items, text, win):
    """読み物「寒い県ランキング」。表と地図は月ごとの平年値から毎回作る。"""
    e = html.escape
    measure = "".join(
        f'<tr><th scope="row">{e(label)}</th><td>{e(place_text(win[key][0]))}'
        f'<span class="sub">{value_text(win[key][1], unit)}</span></td>'
        f'<td>{e(place_text(win[key][2]))}<span class="sub">{value_text(win[key][3], unit)}</span></td></tr>'
        for key, label, _, unit in SAMUI_MEASURES)
    ranked = sorted(rows, key=lambda r: (r["jan_tmin"], r["code"]))
    lines, rank = [], 0
    for i, r in enumerate(ranked):
        if i == 0 or r["jan_tmin"] != ranked[i - 1]["jan_tmin"]:
            rank = i + 1
        lines.append(f'<tr id="p{r["code"]}"><td>{rank}</td><th scope="row">{e(r["pref"])}'
                     f'<span class="sub">{e(r["name"])}</span></th><td>{value_text(r["jan_tmin"], "℃")}</td>'
                     f'<td>{value_text(r["jan_tmax"], "℃")}</td><td>{value_text(r["fuyubi"], "日")}</td>'
                     f'<td>{value_text(r["mafuyubi"], "日")}</td></tr>')
    map_rows = [{"pref": r["pref"], "val": r["jan_tmin"]} for r in rows]
    low, high = min(r["jan_tmin"] for r in rows), max(r["jan_tmin"] for r in rows)

    body = f"""  <p class="stamp">気象庁「過去の気象データ検索」の平年値（統計期間1991〜2020年）をもとに当サイトが並べた／公開 {iso_text(page['published'])}</p>

  <p class="lede">{e(text['lede'])}</p>

  <h2>比べ方ごとの「いちばん寒い」（全国と本州）</h2>
  <table class="cols">
    <caption>県庁所在地などの観測点の平年値（1991〜2020年）。本州は青森県から山口県まで（当サイトの集計）</caption>
    <thead><tr><th scope="col">比べ方</th><th scope="col">全国</th><th scope="col">本州</th></tr></thead>
    <tbody>
      {measure}
    </tbody>
  </table>
  <p class="note">{e(text['gap'])}{e(text['defs'])}</p>

  <h2>1月の朝の冷え込みの順位（47都道府県）</h2>
  <div class="mapwrap">{japan_map_svg(map_rows, SAMUI_MAP, low, high)}</div>
  <p class="mapkey"><span>{value_text(low, "℃")}</span>{legend_svg(SAMUI_MAP, low, high)}<span>{value_text(high, "℃")}</span></p>
  <p class="note">都道府県をおおよその位置に並べたもので、実際の面積や形とは異なります。マス目の数字は1月の日最低気温の平年値（℃）です。マス目を押すと下の表の該当する行に移動します。</p>
  <div class="mapwrap">
  <table class="cols">
    <caption>1月の日最低気温の平年値が低い順（同じ値は同じ順位）。冬日・真冬日は1年の日数の平年値</caption>
    <thead><tr><th scope="col">順位</th><th scope="col">都道府県</th><th scope="col">1月の最低</th><th scope="col">1月の最高</th><th scope="col">冬日</th><th scope="col">真冬日</th></tr></thead>
    <tbody>
      {"".join(lines)}
    </tbody>
  </table>
  </div>

  <h2>「県」ではなく「1つの地点」の比べです</h2>
  <p>{e(text['point'])}</p>

  <h2>毎日の気温と、暖房の目安</h2>
  <p>その日の最高・最低気温と平年との差は<a href="{METRICS[0]['path']}">{e(METRICS[0]['h1'])}</a>、朝の最低気温が10℃を下回る時期の平年の目安は<a href="{GUIDES[0]['path']}">{e(GUIDES[0]['h1'])}</a>で、毎日入れ替えています。</p>

  <h2>調べ方と出典</h2>
  <p class="note">値は、気象庁「過去の気象データ検索」の平年値（年・月ごとの値）の「詳細（気温・蒸気圧・湿度）」の表（統計期間1991〜2020年）です。いちばん寒い地点と順位は当サイトの集計です。冬日・真冬日の意味は気象庁の予報用語によります。</p>
  <ul class="links">
    <li><a href="{JMA_ETRN}" rel="noopener">気象庁 過去の気象データ検索</a></li>
    <li><a href="{JMA_YOUGO_KION}" rel="noopener">気象庁 予報用語（気温、湿度）</a></li>
  </ul>

  <h2>{e(page['heading'])}</h2>
  {items_html(page['keywords'], items)}
  <p class="note">{ITEMS_NOTE}</p>
"""
    return page_html(page, body)


def render_top(obs_at, results, season, guides, daily_at, touyu):
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
    t_data, t_latest, t_rows = touyu
    got = [r["val"] for r in t_rows if r["val"] is not None]
    t_low, t_high = (min(got), max(got)) if got else (0, 0)
    entries.append(f"""    <li>
      <a class="entry" href="{TOUYU['path']}">
        <span class="t">{TOUYU['h1']}</span>
        <span class="d">総務省の毎月の調査をもとに、47都道府県の県庁所在市の灯油18リットルの店頭価格を並べています。前の月・前の年との差と、東京都区部の長い推移も載せています。</span>
        <span class="mapwrap">{japan_map_svg(t_rows, TOUYU_MAP, t_low, t_high, mini=True)}</span>
        <span class="mapkey"><span>{t_low:,}円</span>{legend_svg(TOUYU_MAP, t_low, t_high)}<span>{t_high:,}円</span></span>
        <span class="d">{html.escape(touyu_stamp(t_data, t_latest))}／押すと都道府県別の一覧へ</span>
      </a>
    </li>""")
    pages_html = "\n".join(entries)
    yomi_html = "\n".join(f"""    <li>
      <a class="entry" href="{p['path']}">
        <span class="t">{html.escape(p['h1'])}</span>
        <span class="d">{html.escape(p['lead'])}</span>
        <span class="d">公開 {iso_text(p['published'])}／データが増えると数字を自動で計算し直します</span>
      </a>
    </li>""" for p in reversed(YOMIMONO))

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{GA}
<title>季節かご｜都道府県別の気温と、暖房・衣替えの目安、灯油の値段（毎日更新）</title>
<meta name="description" content="気象庁と総務省のデータをもとに、都道府県別のその日の最高気温・最低気温、暖房や衣替えの目安になる平年の気温、県庁所在市の灯油の値段、雨量・風速・湿度をまとめています。">
<link rel="canonical" href="{SITE}/">
<style>{CSS_TOP}</style>
</head>
<body>
<div class="wrap">

  <h1 class="mark">季節かご</h1>
  <p class="romaji">kisetsukago</p>

  <p class="lede">
    暑い日、寒い朝、衣替えの季節。天気や季節の条件ごとに、
    気象庁などの公的なデータを都道府県別にまとめ、その時期に選ばれているものと一緒に置いておくサイトです。
  </p>

  <h2>いま見られるページ</h2>
  <ul class="pages">
{pages_html}
  </ul>

  <h2>読み物</h2>
  <ul class="pages">
{yomi_html}
  </ul>

  <h2>これから増やす予定のページ</h2>
  <ul class="band">
    <li>積雪</li>
  </ul>
  <p class="note">雪の季節に、都道府県別の積雪のページを用意する予定です。</p>

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
    paths = ["/"] + [p["path"] for p in ALL_PAGES]
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
    print(f"日付(JST): {day} / 季節: {season} / ページ: {len(ALL_PAGES)}枚")

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

    # 灯油：新しい月が公表されていれば足す。取れなくても前の値でページを作り、ここでは止めない
    with open(TOUYU_FILE, encoding="utf-8") as f:
        touyu = json.load(f)
    touyu_changed, touyu_notes = update_touyu(touyu, now_jst.date())
    for note in touyu_notes:
        print(f"  ※ 灯油: {note}")
    warnings = []
    stale = touyu_stale_days(touyu, now_jst.date())
    if stale > TOUYU_STALE_DAYS:
        warnings.append(f"灯油の値段が新しくなっていません（いちばん新しいのは{month_text(max(touyu['months']))}分・"
                        f"月末から{stale}日）。" + " / ".join(touyu_notes))
    touyu_latest, touyu_rows = summarize_touyu(touyu)

    # 同じ言葉は1回だけ取りに行く。続けて叩くと止められるため間を空ける。
    keyword_sets = [(m["id"], by_season(m["keywords"], season)) for m in METRICS]
    keyword_sets += [(g["id"], g["keywords"]) for g in GUIDES]
    keyword_sets += [(TOUYU["id"], TOUYU["keywords"])]
    keyword_sets += [(p["id"], p["keywords"]) for p in YOMIMONO]
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
    proses[TOUYU["id"]] = touyu_prose(touyu, touyu_latest, touyu_rows)
    # 読み物：数字は毎回データから計算し、言い回しが成り立たなければ検査で止める
    yasui_stats = touyu_month_stats(touyu)
    yasui_text = touyu_yasui_text(yasui_stats, touyu)
    proses[TOUYU_YASUI["id"]] = "".join(yasui_text.values())
    # 読み物「寒い県ランキング」：月ごとの平年値が読めなければ、この読み物だけ書き換えずに知らせる
    try:
        s_rows = samui_rows(load_heinen_month())
        s_text, s_win = samui_text(s_rows)
        samui_probs = samui_problems(s_rows, s_win)
    except (OSError, ValueError, KeyError) as err:
        s_rows, s_text, s_win = [], {}, {}
        samui_probs = [f"読み物「寒い県ランキング」：月ごとの平年値を読めない（{err}）"]
    proses[SAMUI_KEN["id"]] = "".join(s_text.values())

    problems = run_checks({
        "now": now_jst, "age": age, "daily_at": daily_at, "by_pref": by_pref,
        "obs": obs, "daily": daily, "results": results, "heinen": heinen,
        "guides": {k: v[1] for k, v in guides.items()}, "items": items, "proses": proses,
        "touyu": touyu,
    })
    # 読み物の言い回しが成り立たないときは、その読み物だけ書き換えずに知らせる（ほかのページは止めない）
    yasui_problems = touyu_yasui_problems(yasui_stats)

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
    print(f"  灯油: {month_text(touyu_latest)}分 / {sum(1 for r in touyu_rows if r['val'] is not None)}市 / "
          f"東京都区部の推移 {max(touyu.get('tokyo', {'-': 0}))}まで / 商品{len(items[TOUYU['id']])}件")
    print(f"  読み物「{TOUYU_YASUI['h1']}」: 東京都区部 {len(yasui_stats['t_rows'])}年 / "
          f"47市 {yasui_stats['c_n']}組 / 商品{len(items[TOUYU_YASUI['id']])}件")
    print(f"  読み物「{SAMUI_KEN['h1']}」: {len(s_rows)}地点 / 商品{len(items[SAMUI_KEN['id']])}件"
          f"{' / ★' + ' / '.join(samui_probs) if samui_probs else ''}")
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
    write(TOUYU["out"], render_touyu(touyu, touyu_latest, touyu_rows, items[TOUYU["id"]],
                                     proses[TOUYU["id"]]))
    written = [p["out"] for p in METRICS + GUIDES + [TOUYU]]
    if yasui_problems:
        warnings += yasui_problems
    else:
        write(TOUYU_YASUI["out"], render_touyu_yasui(TOUYU_YASUI, touyu, yasui_stats,
                                                     items[TOUYU_YASUI["id"]], yasui_text))
        written.append(TOUYU_YASUI["out"])
    if samui_probs:
        warnings += samui_probs
    else:
        write(SAMUI_KEN["out"], render_samui(SAMUI_KEN, s_rows, items[SAMUI_KEN["id"]], s_text, s_win))
        written.append(SAMUI_KEN["out"])
    write(OUT_TOP, render_top(obs_at, results, season, guides, daily_at,
                              (touyu, touyu_latest, touyu_rows)))
    write(OUT_SITEMAP, render_sitemap(obs_day.isoformat()))
    written += [OUT_TOP, OUT_SITEMAP]
    if touyu_changed:
        write(TOUYU_FILE, json.dumps(touyu, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        written.append(TOUYU_FILE)
    print(f"検査: 問題なし / 書き出し: {', '.join(written)}")
    # ページは書いたうえで、知らせたいことがあれば残す（自動実行はこれを見て「失敗」にする）
    if warnings:
        write(WARN_FILE, "\n".join(warnings) + "\n")
        print("--- 知らせたいこと ---")
        for w in warnings:
            print(" -", w)
    elif os.path.exists(WARN_FILE):
        os.remove(WARN_FILE)


if __name__ == "__main__":
    main()
