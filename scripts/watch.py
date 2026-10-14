"""見張り：本番のトップページの「観測」の日付が古くなっていたら、失敗にして知らせる。

毎日の自動実行（daily.yml）が失敗したときは GitHub から失敗のメールが届くが、
次の2つの日はメールが来ないまま、ページが古いまま残る。
  ・GitHub が定時の実行そのものを動かさなかった日
  ・日付が変わった直後（日本時間6時より前）に動いて、build.py が何も書かずに終わった日
そこで、daily とは別の時刻に本番のページを読み、2日続けて新しくなっていなければ失敗にする
（.github/workflows/watch.yml から動かす。失敗すると「Run failed: watch - main」のメールが届く）。

手元で試すとき：python3 scripts/watch.py
「いま」を変えて試すとき：WATCH_NOW=2026-10-17T09:00 python3 scripts/watch.py（日本時間）
"""
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://kisetsukago.com/"
JST = timezone(timedelta(hours=9))
UA = "kisetsukago-watch (+https://kisetsukago.com/)"
# daily.yml の予定時刻（3:00 UTC＝日本時間の正午）。これより前は、その日の更新はまだ来ていなくて当然なので前日を基準にする
DAILY_HOUR_JST = 12
# 基準日からこの日数以上古ければ失敗（＝2日続けて更新されていない）。1日の遅れは GitHub の定時実行の遅れでも起きる
MAX_DAYS = 2


def now_jst():
    if os.environ.get("WATCH_NOW"):
        return datetime.fromisoformat(os.environ["WATCH_NOW"]).replace(tzinfo=JST)
    return datetime.now(JST)


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        if i:
            time.sleep(20)
        try:
            # 配信の途中の保存（キャッシュ）ではなく、いまのページを読む
            req = urllib.request.Request(f"{url}?watch={int(time.time())}",
                                         headers={"User-Agent": UA, "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception as ex:  # noqa: BLE001  通信の失敗はどれも同じに扱い、やり直す
            last = ex
    raise RuntimeError(f"{url} を読めなかった（{tries}回）: {last}")


def main():
    now = now_jst()
    base = now.date() if now.hour >= DAILY_HOUR_JST else now.date() - timedelta(days=1)
    try:
        html = fetch(URL)
    except RuntimeError as ex:
        print(f"::error::{ex}")
        return 1
    found = re.findall(r"観測 (\d{4}-\d{2}-\d{2})", html)
    if not found:
        print("::error::トップページに「観測 YYYY-MM-DD」が見つからない（ページの作りが変わったか、組み立てに失敗している）")
        return 1
    newest = max(datetime.strptime(s, "%Y-%m-%d").date() for s in found)
    age = (base - newest).days
    print(f"いま {now:%Y-%m-%d %H:%M}（日本時間）・基準日 {base}・トップページの観測の日付 {newest}（基準日の{age}日前）")
    if age >= MAX_DAYS:
        print(f"::error::トップページの観測の日付が {newest} のまま（基準日 {base} の{age}日前）。"
              "毎日の自動更新（daily）が2日以上止まっている。GitHub の Actions の画面で daily の実行の記録を見る")
        return 1
    print("OK（毎日の自動更新は動いている）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
