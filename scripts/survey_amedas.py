#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
調査専用スクリプト。

気象庁アメダスの観測データに、
  気温 / 雨量 / 風 / 湿度 / 積雪
が47都道府県すべてでそろっているかを数えるだけ。

・サイトのファイルは一切書き換えない
・公開処理は一切しない
・結果は実行画面(Summary)にそのまま表示される
"""

import json
import os
import sys
import urllib.request
from collections import defaultdict

BASE = "https://www.jma.go.jp/bosai"
UA = {"User-Agent": "kisetsukago-survey/1.0 (+https://kisetsukago.com/)"}

PREF = {
    "01": "北海道", "02": "青森", "03": "岩手", "04": "宮城", "05": "秋田",
    "06": "山形", "07": "福島", "08": "茨城", "09": "栃木", "10": "群馬",
    "11": "埼玉", "12": "千葉", "13": "東京", "14": "神奈川", "15": "新潟",
    "16": "富山", "17": "石川", "18": "福井", "19": "山梨", "20": "長野",
    "21": "岐阜", "22": "静岡", "23": "愛知", "24": "三重", "25": "滋賀",
    "26": "京都", "27": "大阪", "28": "兵庫", "29": "奈良", "30": "和歌山",
    "31": "鳥取", "32": "島根", "33": "岡山", "34": "広島", "35": "山口",
    "36": "徳島", "37": "香川", "38": "愛媛", "39": "高知", "40": "福岡",
    "41": "佐賀", "42": "長崎", "43": "熊本", "44": "大分", "45": "宮崎",
    "46": "鹿児島", "47": "沖縄",
}

# (表示名, データ内のキー名)
TARGETS = [
    ("気温", "temp"),
    ("雨量1h", "precipitation1h"),
    ("雨量24h", "precipitation24h"),
    ("風速", "wind"),
    ("風向", "windDirection"),
    ("湿度", "humidity"),
    ("積雪", "snow"),
    ("降雪24h", "snow24h"),
]

MAIN_FIVE = ["temp", "precipitation1h", "wind", "humidity", "snow"]

lines = []


def say(text=""):
    print(text)
    lines.append(text)


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def get_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8").strip()


def collect_amedas(node, bucket):
    """入れ子の中から amedas の観測所コードを全部拾う。"""
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "amedas" and isinstance(val, list):
                for code in val:
                    if isinstance(code, str):
                        bucket.add(code)
            else:
                collect_amedas(val, bucket)
    elif isinstance(node, list):
        for val in node:
            collect_amedas(val, bucket)


def is_usable(raw):
    """観測値として使える形かどうか。"""
    if raw is None:
        return False
    if isinstance(raw, list):
        if len(raw) == 0 or raw[0] is None:
            return False
        if len(raw) >= 2 and isinstance(raw[1], int) and raw[1] != 0:
            return False
        return True
    return True


def main():
    say("# アメダス調査結果")
    say()

    # ---- 1. 観測所と都道府県の対応 ----
    try:
        area = get_json(BASE + "/forecast/const/forecast_area.json")
    except Exception as exc:
        say("forecast_area.json が取れませんでした: %s" % exc)
        finish()
        return 1

    pref_stations = defaultdict(set)
    for office_code, body in area.items():
        pref_code = str(office_code)[:2]
        if pref_code not in PREF:
            continue
        bucket = set()
        collect_amedas(body, bucket)
        pref_stations[pref_code] |= bucket

    listed = sorted(PREF)
    missing_map = [PREF[p] for p in listed if not pref_stations.get(p)]
    total_listed = sum(len(pref_stations.get(p, ())) for p in listed)

    say("対応表に載っている観測所: %d か所" % total_listed)
    if missing_map:
        say("対応表に観測所が1つも無い都道府県: " + " / ".join(missing_map))
    else:
        say("47都道府県すべてに観測所の登録あり")
    say()

    # ---- 2. 最新の観測データ ----
    try:
        latest = get_text(BASE + "/amedas/data/latest_time.txt")
        stamp = latest[:19].replace("-", "").replace("T", "").replace(":", "")
        obs = get_json(BASE + "/amedas/data/map/%s.json" % stamp)
    except Exception as exc:
        say("観測データが取れませんでした: %s" % exc)
        finish()
        return 1

    say("観測時刻: %s" % latest)
    say("データに含まれる観測所: %d か所" % len(obs))
    say()

    # ---- 3. データに入っている項目の一覧 ----
    key_count = defaultdict(int)
    for rec in obs.values():
        if isinstance(rec, dict):
            for key in rec:
                key_count[key] += 1

    say("## データに入っている項目（全国の観測所数）")
    say()
    say("| 項目名 | 観測所数 |")
    say("| --- | ---: |")
    for key in sorted(key_count, key=lambda k: -key_count[k]):
        say("| `%s` | %d |" % (key, key_count[key]))
    say()

    # ---- 4. 都道府県ごとの充足状況 ----
    header = "| 都道府県 | " + " | ".join(n for n, _ in TARGETS) + " |"
    divider = "| --- |" + " ---: |" * len(TARGETS)
    say("## 都道府県ごとの観測所数")
    say()
    say(header)
    say(divider)

    covered = {k: 0 for _, k in TARGETS}
    zero_pref = defaultdict(list)
    matched_total = 0

    for pref_code in listed:
        counts = {k: 0 for _, k in TARGETS}
        for station in pref_stations.get(pref_code, ()):
            rec = obs.get(station)
            if not isinstance(rec, dict):
                continue
            matched_total += 1
            for _, key in TARGETS:
                if is_usable(rec.get(key)):
                    counts[key] += 1
        for _, key in TARGETS:
            if counts[key] > 0:
                covered[key] += 1
            else:
                zero_pref[key].append(PREF[pref_code])
        row = "| %s | " % PREF[pref_code]
        row += " | ".join(str(counts[k]) for _, k in TARGETS) + " |"
        say(row)

    say()
    say("対応表の観測所のうち、実データと突き合わせできたもの: %d 件" % matched_total)
    say()

    # ---- 5. まとめ ----
    say("## まとめ（47都道府県のうち何県で取れたか）")
    say()
    say("| 項目 | 取れた県数 | 判定 |")
    say("| --- | ---: | --- |")
    for name, key in TARGETS:
        num = covered[key]
        if num == 47:
            verdict = "そのまま地図にできる"
        elif num >= 40:
            verdict = "ほぼ可。空白県の扱いを決める必要あり"
        elif num > 0:
            verdict = "地図には不足"
        else:
            verdict = "データなし"
        say("| %s | %d | %s |" % (name, num, verdict))
    say()

    for name, key in TARGETS:
        if key in MAIN_FIVE and 0 < covered[key] < 47:
            say("%s が無い県: %s" % (name, " / ".join(zero_pref[key])))
    say()

    month = latest[5:7] if len(latest) >= 7 else "??"
    if month not in ("11", "12", "01", "02", "03", "04"):
        say("注意: いまは %s月です。積雪と降雪は冬にしか観測されないため、"
            "この結果だけでは可否を判断できません。冬に再確認が必要です。" % month)

    finish()
    return 0


def finish():
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
