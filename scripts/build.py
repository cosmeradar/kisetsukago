#!/usr/bin/env python3
"""都道府県別の気温ページと、トップページを組み立てる。

夏（4〜9月）は気温の高い順、冬（10〜3月）は低い順に並べる。
気温はマス目状の日本地図で色分けする。色はその日の全国の
最低〜最高の間で自動的に割り振るので、季節ごとの設定は要らない。
商品は楽天のレビュー件数上位から、日付を種にして日替わりで選ぶ。
公開前の検査に1つでも引っかかったら何も書かずに終了する。
"""

import hashlib
import html
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

LATEST_TIME = "https://www.jma.go.jp/bosai/amedas/data/latest_time.txt"
MAP_TMPL = "https://www.jma.go.jp/bosai/amedas/data/map/{ts}.json"
STATION_TABLE = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
FORECAST_AREA = "https://www.jma.go.jp/bosai/forecast/const/forecast_area.json"
RAKUTEN = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
SITE = "https://kisetsukago.com"
GA_ID = "G-C46GBVZFLL"

OUT_KION = "kion/index.html"
OUT_TOP = "index.html"
MAX_AGE_MINUTES = 180
HOT = 35.0
COLD = 0.0

# 楽天から取る候補数と、その中から見せる数
POOL_SIZE = 20
SHOW_PER_KEYWORD = 3
RAKUTEN_INTERVAL_SEC = 3
RETRY_CODES = (429, 500, 502, 503, 504)

SEASONS = {
    "summer": {
        "months": (4, 5, 6, 7, 8, 9),
        "order": "desc",
        "order_label": "気温の高い順",
        "heading": "暑い時期に選ばれているもの",
        "keywords": ["ハンディファン", "冷感 タオル", "日傘"],
    },
    "winter": {
        "months": (10, 11, 12, 1, 2, 3),
        "order": "asc",
        "order_label": "気温の低い順",
        "heading": "寒い時期に選ばれているもの",
        "keywords": ["電気毛布", "加湿器", "あったかインナー"],
    },
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


# ---------- 取得 ----------

def get(url, as_json=True, headers=None, tries=4):
    """取得する。混雑や一時的な不調なら間を空けて数回試す。"""
    h = {"User-Agent": "kisetsukago/0.1"}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
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


def fetch_pool(keyword, app_id, access_key, affiliate_id):
    """レビュー件数の多い順に候補を取る。"""
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


def summarize(by_pref, obs, table, order):
    """都道府県ごとに代表地点をまとめる。夏は最も高い地点、冬は最も低い地点を選ぶ。"""
    want_high = (order == "desc")
    rows = []
    for pref, codes in by_pref.items():
        best = None
        for code in codes:
            temp = value_of(obs.get(code, {}), "temp")
            if temp is None:
                continue
            if best is None or (temp > best["temp"] if want_high else temp < best["temp"]):
                best = {"temp": temp, "spot": table.get(code, {}).get("kjName") or code}
        rows.append({
            "pref": pref,
            "temp": best["temp"] if best else None,
            "spot": best["spot"] if best else None,
            "hot": bool(best and best["temp"] >= HOT),
            "cold": bool(best and best["temp"] <= COLD),
        })
    if want_high:
        rows.sort(key=lambda r: (r["temp"] is None, -(r["temp"] or 0), r["pref"]))
    else:
        rows.sort(key=lambda r: (r["temp"] is None, (r["temp"] if r["temp"] is not None else 999), r["pref"]))
    return rows


def temp_range(rows):
    got = [r["temp"] for r in rows if r["temp"] is not None]
    if not got:
        return 0.0, 0.0
    return min(got), max(got)


# ---------- 地図 ----------

def short_pref(name):
    """マス目に入れる短い名前。北海道だけはそのまま。"""
    if name != "北海道" and name.endswith(("県", "都", "府")):
        return name[:-1]
    return name


def heat_color(temp, low, high):
    """その日の最低〜最高の間で色を割り振る。低いほど青、高いほど赤。"""
    if temp is None:
        return C_NONE
    if high - low < 0.1:
        t = 0.5
    else:
        t = (temp - low) / (high - low)
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        a, b, u = C_COLD, C_MID, t * 2
    else:
        a, b, u = C_MID, C_HOT, (t - 0.5) * 2
    rgb = tuple(round(a[i] + (b[i] - a[i]) * u) for i in range(3))
    return "#%02X%02X%02X" % rgb


def japan_map_svg(rows, low, high, mini=False):
    """マス目の日本地図。mini は色だけの小さい版（トップページ用）。"""
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
        temp = rec.get("temp")
        x = col * (cw + gap)
        y = row * (ch + gap)
        fill = heat_color(temp, low, high)
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="{rx}" fill="{fill}"/>'
        )
        if mini:
            continue
        label = f"{temp:.1f}" if temp is not None else "—"
        cx = x + cw // 2
        parts.append(
            f'<a href="#p{PREF_CODE[name]}">'
            f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="{rx}" fill="transparent"/>'
            f'<text class="mn" x="{cx}" y="{y + 16}">{e(short_pref(name))}</text>'
            f'<text class="mv" x="{cx}" y="{y + 32}">{label}</text>'
            "</a>"
        )

    cls = "jmap mini" if mini else "jmap"
    title = "都道府県別の気温を色で表した図"
    return (f'<svg class="{cls}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{title}"><title>{title}</title>{"".join(parts)}</svg>')


def legend_svg(low, high, steps=9):
    parts = []
    bw = 20
    for i in range(steps):
        t = low + (high - low) * i / (steps - 1)
        parts.append(f'<rect x="{i * bw}" y="0" width="{bw}" height="10" '
                     f'fill="{heat_color(t, low, high)}"/>')
    return (f'<svg class="jkey" viewBox="0 0 {steps * bw} 10" '
            f'role="presentation" aria-hidden="true">{"".join(parts)}</svg>')


# ---------- 検査 ----------

def run_checks(rows, items, age, obs, by_pref, prose):
    problems = []
    if age > MAX_AGE_MINUTES:
        problems.append(f"データが古い（{age:.0f}分前）")
    if len(by_pref) != 47:
        problems.append(f"都道府県が47にならない（{len(by_pref)}）")
    with_temp = sum(1 for e in obs.values() if value_of(e, "temp") is not None)
    if with_temp < 500:
        problems.append(f"気温が取れている地点が少なすぎる（{with_temp}）")
    missing = [r["pref"] for r in rows if r["temp"] is None]
    if len(missing) > 5:
        problems.append(f"気温が取れない県が多い（{len(missing)}）")
    for r in rows:
        if r["temp"] is not None and not (-50 <= r["temp"] <= 50):
            problems.append(f"気温が異常値: {r['pref']} {r['temp']}")
    tile_names = [n for n, _, _ in TILE_MAP]
    if len(tile_names) != len(set(tile_names)):
        problems.append("地図のマス目に同じ都道府県が重複している")
    if set(tile_names) != set(PREF.values()):
        problems.append("地図のマス目と都道府県の一覧が一致しない")
    placed = [(c, r) for _, c, r in TILE_MAP]
    if len(placed) != len(set(placed)):
        problems.append("地図のマス目が同じ位置に重なっている")
    if not items:
        problems.append("商品が0件")
    for it in items:
        if not it["url"] or "//" not in it["url"]:
            problems.append(f"商品リンクが不正: {it['name'][:20]}")
        if not isinstance(it["price"], int) or it["price"] <= 0:
            problems.append(f"価格が不正: {it['name'][:20]}")
    for word in BANNED:
        if word in prose:
            problems.append(f"禁止表現が本文にある: {word}")
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
  @media (max-width:30rem){.wrap{padding:2.25rem 1.25rem} h1{font-size:1.625rem}}
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


# ---------- 組み立て ----------

def prose_text(obs_at, season, rows):
    """毎日変わるのは日時と数字だけ。言い回しは固定。"""
    cfg = SEASONS[season]
    low, high = temp_range(rows)
    got = [r for r in rows if r["temp"] is not None]
    span = ""
    if got:
        span = f"全国の代表地点では{low:.1f}度から{high:.1f}度までの幅がありました。"
    return (
        f"{obs_at:%Y年%-m月%-d日 %H:%M}（日本時間）時点の気象庁の観測をもとに、"
        f"都道府県ごとの気温を{cfg['order_label']}に並べています。{span}"
        "地図の色はこの幅にあわせて自動で割り振っています。"
        "気象庁が天気予報に使う代表地点のみを対象としているため、"
        "その都道府県の最高気温や最低気温とは限りません。"
    )


def render_kion(obs_at, rows, items, prose, season):
    e = html.escape
    cfg = SEASONS[season]
    low, high = temp_range(rows)

    table_html = []
    for r in rows:
        if r["temp"] is None:
            cells = '<td class="t">—</td><td class="s">データなし</td>'
        else:
            mark = ""
            if r["hot"]:
                mark = ' <span class="tag hot">35℃以上</span>'
            elif r["cold"]:
                mark = ' <span class="tag cold">0℃以下</span>'
            cells = (f'<td class="t">{r["temp"]:.1f}℃{mark}</td>'
                     f'<td class="s">{e(r["spot"])}</td>')
        pid = PREF_CODE.get(r["pref"], "")
        table_html.append(
            f'<tr id="p{pid}"><th scope="row">{e(r["pref"])}</th>{cells}</tr>')

    groups = {}
    for it in items:
        groups.setdefault(it["keyword"], []).append(it)

    sections = []
    for kw in cfg["keywords"]:
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

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{GA}
<title>都道府県別の気温｜季節かご</title>
<meta name="description" content="気象庁の観測をもとに、都道府県ごとの代表地点の気温を地図と表でまとめています。">
<link rel="canonical" href="{SITE}/kion/">
<style>{CSS_KION}</style>
</head>
<body>
<div class="wrap">

  <p class="home"><a href="/">季節かご</a></p>

  <h1>都道府県別の気温</h1>
  <p class="stamp">観測 {obs_at:%Y-%m-%d %H:%M} 日本時間／{cfg['order_label']}／{low:.1f}〜{high:.1f}℃</p>

  <p class="lede">{e(prose)}</p>

  <h2>全国の分布</h2>
  <div class="mapwrap">{japan_map_svg(rows, low, high)}</div>
  <p class="mapkey"><span>{low:.1f}℃</span>{legend_svg(low, high)}<span>{high:.1f}℃</span></p>
  <p class="note">都道府県をおおよその位置に並べたもので、実際の面積や形とは異なります。マス目を押すと<a href="#hyou">下の表</a>の該当する行に移動します。</p>

  <h2 id="hyou">都道府県別 代表地点の気温</h2>
  <table>
    <thead><tr><th scope="col">都道府県</th><th scope="col">気温</th><th scope="col">地点</th></tr></thead>
    <tbody>
      {"".join(table_html)}
    </tbody>
  </table>
  <p class="note">気象庁が公開しているアメダスの観測値をもとにしています。10分ごとに更新される値のうち、上に記した時刻のものです。</p>

  <h2>{e(cfg['heading'])}</h2>
  {"".join(sections)}
  <p class="note">楽天市場でレビュー件数の多い商品の中から選んで表示しています。商品名は各店舗が登録したものをそのままにしています。価格・在庫は変動するため、最新の情報は各商品ページでご確認ください。</p>

  <h2>このページの位置づけ</h2>
  <p class="note">当サイトは商品を紹介することを目的としています。気象情報や防災情報を提供するものではなく、健康や安全に関する判断の根拠として使えるものではありません。気象に関する情報や警戒の呼びかけは、下記の公式発表をご確認ください。</p>

{OFFICIAL_LINKS}

  <footer>
    当サイトは楽天アフィリエイトを利用した商品紹介を行っています。<br>
    季節かご / kisetsukago.com
  </footer>

</div>
</body>
</html>
"""


def render_top(obs_at, rows, season):
    cfg = SEASONS[season]
    low, high = temp_range(rows)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{GA}
<title>季節かご｜天気と季節に合わせた買い物のヒント</title>
<meta name="description" content="気象庁の観測をもとに、天気や季節の条件ごとに関連する商品をまとめて紹介しています。">
<link rel="canonical" href="{SITE}/">
<style>{CSS_TOP}</style>
</head>
<body>
<div class="wrap">

  <h1 class="mark">季節かご</h1>
  <p class="romaji">kisetsukago</p>

  <p class="lede">
    暑い日、荒れた日、花粉の多い日。天気や季節の条件ごとに、
    その時期に売れているものを一覧でまとめて置いておくサイトです。
  </p>

  <h2>いま見られるページ</h2>
  <ul class="pages">
    <li>
      <a class="entry" href="/kion/">
        <span class="t">都道府県別の気温</span>
        <span class="d">気象庁の観測をもとに、47都道府県の代表地点の気温を並べています。{cfg['order_label']}。毎日入れ替わります。</span>
        <span class="mapwrap">{japan_map_svg(rows, low, high, mini=True)}</span>
        <span class="mapkey"><span>{low:.1f}℃</span>{legend_svg(low, high)}<span>{high:.1f}℃</span></span>
        <span class="d">観測 {obs_at:%Y-%m-%d %H:%M} 日本時間／押すと都道府県名と地点名の一覧へ</span>
      </a>
    </li>
  </ul>

  <h2>これから増やす予定の条件</h2>
  <ul class="band">
    <li>雨量</li><li>風</li><li>湿度</li><li>台風</li>
    <li>花粉</li><li>黄砂</li><li>紫外線</li><li>雪</li>
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


def write(path, text):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    app_id = os.environ.get("RAKUTEN_APP_ID", "")
    access_key = os.environ.get("RAKUTEN_ACCESS_KEY", "")
    affiliate_id = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
    if not (app_id and access_key and affiliate_id):
        print("楽天の設定が渡されていません")
        sys.exit(1)

    now_jst = datetime.now(JST)
    season = season_of(now_jst.month)
    cfg = SEASONS[season]
    day = now_jst.strftime("%Y-%m-%d")
    print(f"日付(JST): {day} / 季節: {season} / 並び: {cfg['order_label']}")

    obs_at, obs, table, area = fetch_weather()
    age = (now_jst - obs_at).total_seconds() / 60
    by_pref = stations_by_pref(area)
    rows = summarize(by_pref, obs, table, cfg["order"])
    low, high = temp_range(rows)

    items = []
    for i, kw in enumerate(cfg["keywords"]):
        if i:
            time.sleep(RAKUTEN_INTERVAL_SEC)
        pool = fetch_pool(kw, app_id, access_key, affiliate_id)
        chosen = pick_daily(pool, day, kw)
        items.extend(chosen)
        print(f"  「{kw}」: 候補{len(pool)}件から{len(chosen)}件")

    prose = prose_text(obs_at, season, rows)
    problems = run_checks(rows, items, age, obs, by_pref, prose)
    print(f"観測時刻: {obs_at:%Y-%m-%d %H:%M}（{age:.0f}分前）")
    print(f"都道府県: {len(by_pref)} / 商品: {len(items)}件 / 気温の幅: {low:.1f}〜{high:.1f}℃")
    if problems:
        print("--- 検査で問題を検出。公開しません ---")
        for p in problems:
            print(" -", p)
        sys.exit(1)

    write(OUT_KION, render_kion(obs_at, rows, items, prose, season))
    write(OUT_TOP, render_top(obs_at, rows, season))
    print(f"検査: 問題なし / 書き出し: {OUT_KION}, {OUT_TOP}")


if __name__ == "__main__":
    main()
