#!/usr/bin/env python3
"""猛暑ページを組み立てる。

気象庁アメダス（都道府県ごとの代表地点の気温）と
楽天商品検索APIの結果を、あらかじめ用意した固定文に差し込んで
HTML を書き出す。公開前の検査に1つでも引っかかったら何も書かずに終了する。
"""

import html
import json
import os
import sys
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

HOT = 35.0
MAX_AGE_MINUTES = 180
OUT_PATH = "moushou/index.html"

KEYWORDS = ["ハンディファン"]
ITEMS_PER_KEYWORD = 3

# 私が書いた文章にだけ適用する禁止表現。
# 商品名は店舗が付けたものなので対象外にする。
BANNED = [
    "防げます", "防げる", "予防できます", "防止できます", "防止します",
    "安全です", "危険はありません", "問題ありません",
    "効果があります", "効きます", "改善します", "治ります", "治せます",
    "熱中症を防", "熱中症対策になります",
    "避難してください", "外出は控えてください", "水分を取ってください",
    "しましょう",
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


# ---------- 取得 ----------

def get(url, as_json=True, headers=None):
    h = {"User-Agent": "kisetsukago/0.1"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if as_json else raw.strip()


def value_of(entry, key):
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


def fetch_items(keyword, app_id, access_key, affiliate_id):
    params = {
        "applicationId": app_id,
        "accessKey": access_key,
        "affiliateId": affiliate_id,
        "keyword": keyword,
        "hits": str(ITEMS_PER_KEYWORD),
        "formatVersion": "2",
        "imageFlag": "1",
        "availability": "1",
        "elements": "itemName,itemPrice,affiliateUrl,mediumImageUrls,shopName",
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
            "image": images[0] if images else "",
            "keyword": keyword,
        })
    return out


# ---------- 集計 ----------

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


def summarize(by_pref, obs, table):
    rows = []
    for pref, codes in by_pref.items():
        best = None
        for code in codes:
            temp = value_of(obs.get(code, {}), "temp")
            if temp is None:
                continue
            if best is None or temp > best["temp"]:
                best = {"temp": temp, "spot": table.get(code, {}).get("kjName") or code}
        rows.append({
            "pref": pref,
            "temp": best["temp"] if best else None,
            "spot": best["spot"] if best else None,
            "hot": bool(best and best["temp"] >= HOT),
        })
    rows.sort(key=lambda r: (r["temp"] is None, -(r["temp"] or 0), r["pref"]))
    return rows


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


# ---------- 組み立て ----------

def prose_text(obs_at, hot_count):
    """毎日変わるのは日時と件数だけ。言い回しは固定。"""
    return (
        f"{obs_at:%Y年%-m月%-d日 %H:%M}（日本時間）時点の気象庁の観測では、"
        f"各都道府県の代表地点のうち{HOT:.0f}度以上を記録したのは{hot_count}都道府県でした。"
        "下の表は、都道府県ごとに代表地点の中で最も気温の高かった地点を並べたものです。"
        "気象庁が天気予報に使う代表地点のみを対象としているため、"
        "その都道府県の最高気温とは限りません。"
    )


def render(obs_at, rows, items, prose):
    e = html.escape
    hot_rows = [r for r in rows if r["hot"]]

    table_html = []
    for r in rows:
        if r["temp"] is None:
            cells = '<td class="t">—</td><td class="s">データなし</td>'
        else:
            mark = ' <span class="hot">猛暑日</span>' if r["hot"] else ""
            cells = (f'<td class="t">{r["temp"]:.1f}℃{mark}</td>'
                     f'<td class="s">{e(r["spot"])}</td>')
        table_html.append(f'<tr><th scope="row">{e(r["pref"])}</th>{cells}</tr>')

    cards = []
    for it in items:
        price = f"{it['price']:,}円" if isinstance(it["price"], int) else ""
        img = (f'<img src="{e(it["image"])}" alt="" loading="lazy" width="128" height="128">'
               if it["image"] else "")
        cards.append(
            '<li class="card">'
            f'<a href="{e(it["url"])}" rel="nofollow sponsored noopener" target="_blank">'
            f'{img}<span class="pname">{e(it["name"])}</span></a>'
            f'<span class="price">{price}</span>'
            f'<span class="shop">{e(it["shop"])}</span>'
            "</li>"
        )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>猛暑・暑い日｜季節かご</title>
<meta name="description" content="気象庁の観測をもとに、都道府県ごとの代表地点の気温をまとめ、暑い時期に売れているものを紹介します。">
<link rel="canonical" href="{SITE}/moushou/">
<style>
  :root{{
    --paper:#FBFAF6; --ink:#1C2A33; --ink-soft:#5A6670;
    --ai:#2F4E7C; --koke:#6B7F5B; --hi:#B4472B; --rule:#E2DFD5;
    --mincho:"Hiragino Mincho ProN","Yu Mincho","YuMincho","MS PMincho",serif;
    --gothic:"Hiragino Kaku Gothic ProN","Yu Gothic","YuGothic",Meiryo,system-ui,sans-serif;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--paper);color:var(--ink);
       font-family:var(--gothic);font-size:16px;line-height:1.9}}
  .wrap{{max-width:44rem;margin:0 auto;padding:3rem 1.5rem 3rem}}
  a{{color:var(--ai);text-underline-offset:.2em}}
  a:focus-visible{{outline:2px solid var(--ai);outline-offset:3px}}
  .home{{font-size:.8125rem;color:var(--ink-soft);margin:0 0 2rem}}
  h1{{font-family:var(--mincho);font-size:2rem;font-weight:400;
      letter-spacing:.1em;margin:0 0 .4rem}}
  .stamp{{font-size:.75rem;letter-spacing:.16em;color:var(--ink-soft);margin:0 0 2.5rem}}
  .lede{{font-family:var(--mincho);font-size:1.0625rem;margin:0 0 2.5rem}}
  h2{{font-size:.75rem;letter-spacing:.2em;color:var(--ink-soft);font-weight:600;
      margin:0 0 1rem;padding-top:2.25rem;border-top:1px solid var(--rule)}}
  table{{width:100%;border-collapse:collapse;font-size:.9375rem}}
  th,td{{text-align:left;padding:.45rem .5rem;border-bottom:1px solid var(--rule);
         vertical-align:baseline}}
  th[scope=row]{{font-weight:400;width:38%}}
  td.t{{width:34%;font-variant-numeric:tabular-nums}}
  td.s{{color:var(--ink-soft);font-size:.875rem}}
  .hot{{color:var(--hi);font-size:.75rem;letter-spacing:.08em;margin-left:.3rem}}
  ul.grid{{list-style:none;margin:0;padding:0;display:grid;gap:1.5rem 1rem;
           grid-template-columns:repeat(auto-fill,minmax(9rem,1fr))}}
  .card a{{display:block;text-decoration:none;color:var(--ink)}}
  .card img{{width:100%;height:auto;aspect-ratio:1;object-fit:contain;
             background:#fff;border:1px solid var(--rule)}}
  .pname{{display:block;font-size:.8125rem;line-height:1.6;margin-top:.5rem;
          display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;
          overflow:hidden}}
  .price{{display:block;font-size:.875rem;margin-top:.25rem}}
  .shop{{display:block;font-size:.75rem;color:var(--ink-soft)}}
  .note{{font-size:.875rem;color:var(--ink-soft)}}
  ul.links{{list-style:none;margin:0;padding:0;font-size:.9375rem}}
  ul.links li{{margin-bottom:.35rem}}
  footer{{margin-top:3rem;padding-top:1.25rem;border-top:1px solid var(--rule);
          font-size:.8125rem;color:var(--ink-soft)}}
  @media (max-width:30rem){{.wrap{{padding:2.25rem 1.25rem}} h1{{font-size:1.625rem}}}}
</style>
</head>
<body>
<div class="wrap">

  <p class="home"><a href="/">季節かご</a></p>

  <h1>猛暑・暑い日</h1>
  <p class="stamp">観測時刻 {obs_at:%Y-%m-%d %H:%M} 日本時間／{len(hot_rows)}都道府県で35℃以上</p>

  <p class="lede">{html.escape(prose)}</p>

  <h2>都道府県別 代表地点の気温</h2>
  <table>
    <thead><tr><th scope="col">都道府県</th><th scope="col">気温</th><th scope="col">地点</th></tr></thead>
    <tbody>
      {"".join(table_html)}
    </tbody>
  </table>
  <p class="note">気象庁が公開しているアメダスの観測値をもとにしています。10分ごとに更新される値のうち、上に記した時刻のものです。</p>

  <h2>暑い時期に売れているもの</h2>
  <ul class="grid">
    {"".join(cards)}
  </ul>
  <p class="note">楽天市場の商品検索結果です。商品名は各店舗が登録したものをそのまま表示しています。価格・在庫は変動するため、最新の情報は各商品ページでご確認ください。</p>

  <h2>このページの位置づけ</h2>
  <p class="note">当サイトは商品を紹介することを目的としています。気象情報や防災情報を提供するものではなく、健康や安全に関する判断の根拠として使えるものではありません。暑さに関する情報や警戒の呼びかけは、下記の公式発表をご確認ください。</p>

  <h2>公式情報</h2>
  <ul class="links">
    <li><a href="https://www.jma.go.jp/bosai/warning/" rel="noopener">気象庁 警報・注意報</a></li>
    <li><a href="https://www.jma.go.jp/bosai/map.html" rel="noopener">気象庁 天気予報</a></li>
    <li><a href="https://www.wbgt.env.go.jp/" rel="noopener">環境省 熱中症予防情報サイト</a></li>
    <li><a href="https://www.bousai.go.jp/" rel="noopener">内閣府 防災情報のページ</a></li>
  </ul>
  <p class="note">お住まいの自治体が出す情報もあわせてご確認ください。</p>

  <footer>
    当サイトは楽天アフィリエイトを利用した商品紹介を行っています。<br>
    季節かご / kisetsukago.com
  </footer>

</div>
</body>
</html>
"""


def main():
    app_id = os.environ.get("RAKUTEN_APP_ID", "")
    access_key = os.environ.get("RAKUTEN_ACCESS_KEY", "")
    affiliate_id = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
    if not (app_id and access_key and affiliate_id):
        print("楽天の設定が渡されていません")
        sys.exit(1)

    obs_at, obs, table, area = fetch_weather()
    age = (datetime.now(JST) - obs_at).total_seconds() / 60
    by_pref = stations_by_pref(area)
    rows = summarize(by_pref, obs, table)

    items = []
    for kw in KEYWORDS:
        items.extend(fetch_items(kw, app_id, access_key, affiliate_id))

    hot_count = sum(1 for r in rows if r["hot"])
    prose = prose_text(obs_at, hot_count)

    problems = run_checks(rows, items, age, obs, by_pref, prose)
    print(f"観測時刻: {obs_at:%Y-%m-%d %H:%M}（{age:.0f}分前）")
    print(f"都道府県: {len(by_pref)} / 35度以上: {hot_count} / 商品: {len(items)}件")
    if problems:
        print("--- 検査で問題を検出。公開しません ---")
        for p in problems:
            print(" -", p)
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(render(obs_at, rows, items, prose))
    print(f"検査: 問題なし / 書き出し: {OUT_PATH}")


if __name__ == "__main__":
    main()
