#!/usr/bin/env python3
"""相場を1日1回取ってきて prices.json を作る。アプリはこの JSON だけを読む。

**アプリが田中貴金属を直接叩かない。**各ユーザーの端末から取りに行く形は規約上も
運用上も弱いので、Mac 側で1回だけ取って配る。

**これは `app/build_prices.py` の写し**（便403）。GitHub Actions から回すために、
書き出し先だけ引数・環境変数で変えられるようにしてある。**元は書き換えていない。**
値の作り方は 1行も変えていない（写した時点：便402 の HEAD）。

取るもの（3つ。定義が違うので混ぜない。失敗帳 型12）:

  ① RE:TANAKA 品位別リサイクル価格 …「今いくらで買い取ってもらえるか」の実額。
     鑑定料・手数料込みと田中が明記しているので、そのまま受け取れる額。
     **履歴は公表されていない。今日の値だけ。**
  ② 店頭小売価格の日次 … 直近12ヶ月（約229営業日）。9:30公表。グラフの「形」に使う
  ③ 店頭買取価格の日次 … 直近30営業日だけ。参考

**なぜ①が要るか。**純度を掛けた計算だと過大に出る。実測（2026-09-10）:
K18 は純度計算 17,780 に対し実際 15,831 で **-11.0%**、K10 は **-20.4%**。
「最低でもこれだけ」を名乗るなら、純度計算は使えない。
"""
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def out_path():
    """書き出し先を決める。**引数 → 環境変数 PRICES_OUT → 隣の prices.json** の順。

    元（app/build_prices.py）は隣に固定で書く。Actions では staging/prices.json へ
    書きたいので、**ここだけ**口を開けた。
    """
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a
    return os.environ.get("PRICES_OUT") or os.path.join(HERE, "prices.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SOUBA = "https://gold.tanaka.co.jp/commodity/souba/"
RETANAKA = "https://gold.tanaka.co.jp/retanaka/price/"

# 田中のページ上の呼び名 -> このアプリでの呼び名
METALS = [("gold", "金", "d-gold.php"),
          ("platinum", "プラチナ", "d-platinum.php"),
          ("silver", "銀", "d-silver.php")]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 保守ページの字で「止まっている」と「壊れた」を分けるため、本文を持ち出す
        try:
            e.body = e.read().decode("utf-8", "replace")
        except Exception:
            e.body = ""
        raise


def cells(row):
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]


def tables(html):
    for tb in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        yield [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S)]


def yen(s):
    s = s.replace("&yen;", "").replace("¥", "").replace(",", "").strip()
    return float(s) if re.match(r"^\d+(\.\d+)?$", s) else None


# ---------------------------------------------------------------- ①
def recycle_prices():
    """RE:TANAKA の品位別リサイクル価格。金・プラチナ・銀の3表が順に並ぶ。"""
    h = get(RETANAKA)
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", h, flags=re.S)))
    m = re.search(r"リサイクル価格\s*(\d{4})年(\d{2})月(\d{2})日\s*([\d:]+)発表", t)
    asof = "%s-%s-%s %s" % (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None

    # 「品位 / リサイクル価格/g」の見出しを持つ表を、出てくる順に 金→プラチナ→銀 と読む
    found = []
    for grid in tables(h):
        head = grid[0] if grid else []
        if len(head) >= 2 and head[0].strip() == "品位":
            d = {}
            for r in grid[1:]:
                if len(r) >= 2 and yen(r[1]) is not None:
                    d[r[0].strip()] = yen(r[1])
            found.append(d)
    names = ["金", "プラチナ", "銀"]
    out = {}
    for i, d in enumerate(found[:3]):
        out[names[i]] = d
    # 田中自身が載せている買取例で、読み方が合っているかを確かめる。
    # 生の HTML はタグが挟まるので、タグを外した t の側で拾う。
    examples = []
    for mm in re.finditer(r"品名：\s*(\S+)\s*品種：\s*(\S+)\s*品位：\s*(\S+)\s*重量：\s*(\d+)g\s*&yen;([\d,]+)", t):
        examples.append({"品名": mm.group(1), "品種": mm.group(2), "品位": mm.group(3),
                         "重量g": int(mm.group(4)), "田中の答え": float(mm.group(5).replace(",", ""))})
    return asof, out, examples


# ---------------------------------------------------------------- ②
def retail_daily(page):
    """直近12ヶ月の日次（店頭小売価格・税込・円/g・9:30公表）。"""
    h = get(SOUBA + page)
    for grid in tables(h):
        days = [g for g in grid if g and re.match(r"^\d{1,2}日$", g[0])]
        if len(days) < 20:
            continue
        head = next((g for g in grid if any("月" in c for c in g)
                     and not re.match(r"^\d{1,2}日$", (g[0] if g else ""))), [])
        ym = []
        for c in head[1:]:
            mm = re.match(r"^(\d{4})年\s*(\d{1,2})月$", c.strip())
            ym.append((int(mm.group(1)), int(mm.group(2))) if mm else None)
        rows = []
        for g in days:
            day = int(g[0][:-1])
            for col, v in enumerate(g[1:]):
                if col < len(ym) and ym[col] and yen(v) is not None:
                    rows.append(["%04d-%02d-%02d" % (ym[col][0], ym[col][1], day), yen(v)])
        rows.sort()
        return rows
    return []


# ---------------------------------------------------------------- ③
def buy_daily(page, newest_date):
    """直近30営業日の店頭買取価格。表は「月日」しか持たないので年を補う。"""
    h = get(SOUBA + page.replace(".php", "_recent.php"))
    seen = {}
    for grid in tables(h):
        for r in grid:
            if len(r) >= 4 and re.match(r"^\d{2}\.\d{2}$", r[0]) and yen(r[3]) is not None:
                seen[r[0]] = yen(r[3])
    # 新しい順に並べ、月が増えたら年を1つ戻す
    y, prev_m = int(newest_date[:4]), None
    rows = []
    for md in sorted(seen, reverse=True):
        mth, day = int(md[:2]), int(md[3:])
        if prev_m is not None and mth > prev_m:
            y -= 1
        prev_m = mth
        rows.append(["%04d-%02d-%02d" % (y, mth, day), seen[md]])
    rows.sort()
    return rows

# ---------------------------------------------------------------- 取れなかった日
class FormatChanged(Exception):
    """読めたが、表が見つからない・空だった（ページの形が変わった）。"""


MAINTENANCE_WORDS = ("一時停止", "メンテナンス", "保守", "セキュリティ")


def classify(e):
    """例外 → (理由コード, 理由)。鍵と文言はアプリ（②席）が読む。変えない。"""
    if isinstance(e, urllib.error.HTTPError):
        if e.code >= 500:
            body = getattr(e, "body", "") or ""
            if any(w in body for w in MAINTENANCE_WORDS):
                return "maintenance", "田中貴金属のサイトが一時停止中"
            return "server_error", "田中貴金属のサイトが応答していません"
        if e.code in (404, 410):
            return "format_changed", "田中貴金属のページの形が変わり、読めません"
        if e.code >= 400:
            return "blocked", "田中貴金属のサイトから取得できていません"
        return "unknown", "公表元から取得できていません"
    if isinstance(e, FormatChanged):
        return "format_changed", "田中貴金属のページの形が変わり、読めません"
    if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)):
        return "unreachable", "田中貴金属のサイトに繋がりません"
    return "unknown", "公表元から取得できていません"


def write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def write_failure(path, now, code, reason):
    """既存の JSON の「状態」だけを差し替える。値・生成時刻は 1字も触らない。
    既存が無い／読めないときは何も書かない。"""
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if not isinstance(old, dict):
            raise ValueError("object ではない")
    except Exception as e:
        print("既存の %s を読めないので、何も書かない: %s" % (path, e))
        return False
    prev = old.get("状態")
    if isinstance(prev, dict) and prev.get("最新") is False and prev.get("最後に取れた公表"):
        last = prev["最後に取れた公表"]
    else:
        last = (old.get("リサイクル価格") or {}).get("発表")
    status = {"最新": False, "最後に取れた公表": last, "確認時刻": now,
              "理由コード": code, "理由": reason}
    new = {}
    for k, v in old.items():
        if k == "状態":
            continue
        new[k] = v
        if k == "生成時刻":
            new["状態"] = status
    if "状態" not in new:
        new["状態"] = status
    write_atomic(path, new)
    return True


def fetch():
    asof, recycle, examples = recycle_prices()
    if not recycle:
        raise FormatChanged("リサイクル価格の表が見つからない")
    retail, buy = {}, {}
    for key, jp, page in METALS:
        retail[jp] = retail_daily(page)
        if not retail[jp]:
            raise FormatChanged("%s の小売日次が空" % jp)
        buy[jp] = buy_daily(page, retail[jp][-1][0])
    return asof, recycle, examples, retail, buy


def main():
    out = out_path()
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        asof, recycle, examples, retail, buy = fetch()
    except Exception as e:
        code, reason = classify(e)
        print("取れなかった: %s（%s）%s: %s" % (code, reason, type(e).__name__, e))
        if write_failure(out, now, code, reason):
            print("状態だけ差し替えた:", out)
        return 1

    data = {
        "生成時刻": now,
        "状態": {"最新": True},
        "注意": "3つの系列は定義が違う。1本の線に繋がないこと。",
        "リサイクル価格": {
            "定義": ("田中貴金属 RE:TANAKA の品位別リサイクル価格（税込・円/グラム）。"
                     "鑑定料・手数料を含むと田中が明記しており、これがそのまま受け取れる額。"
                     "9:30と14:00に公表され、同日内に変更されることがある。履歴は公表されていない。"),
            "出典": RETANAKA,
            "発表": asof,
            "価格": recycle,
            "田中が載せている買取例": examples,
            "銀について": ("田中の銀の買取はメダル・小判・大判・盃に限られ、1件10g以上が対象。"
                           "銀のジュエリーは買取対象外なので、品位別の価格も 1000(999) 以外は公表されていない。"),
        },
        "地金小売日次": {
            "定義": "田中貴金属 店頭小売価格（税込・円/グラム）。土日祝を除く9:30公表。直近12ヶ月",
            # ★ 便391：旧「d-{gold,platinum,silver}.php」は作業用の書き方で、本物の URL では
            # なかった（秘書の検収・便387 の絵の下端で見つかった）。金のページを本物の 1本として渡す
            # ——プラチナ・銀はアプリの設定 › 数字の出どころ に固定の頁を出す（App/AboutApp.swift）
            "出典": SOUBA + "d-gold.php",
            "系列": retail,
        },
        "地金買取日次": {
            "定義": "田中貴金属 店頭買取価格（税込・円/グラム）。直近30営業日のみ公表",
            "出典": SOUBA + "d-gold_recent.php",
            "系列": buy,
        },
    }
    # **取れなかった日に古い JSON を壊さない。**上まで来ていれば 3系列とも取れている。
    # 一時ファイルへ書いて、書き切れたときだけ差し替える（途中で落ちても元が残る）
    write_atomic(out, data)

    print("書いた:", out)
    print("  リサイクル価格 発表:", asof)
    for jp in ("金", "プラチナ", "銀"):
        print("    %-6s %d品位  %s" % (jp, len(recycle.get(jp, {})),
                                       list(recycle.get(jp, {}).items())[:3]))
    for jp in ("金", "プラチナ", "銀"):
        print("  %-6s 小売日次 %3d件 (%s〜%s)  買取日次 %2d件 (%s〜%s)"
              % (jp, len(retail[jp]), retail[jp][0][0], retail[jp][-1][0],
                 len(buy[jp]), buy[jp][0][0] if buy[jp] else "-",
                 buy[jp][-1][0] if buy[jp] else "-"))
    print("  田中の買取例:", len(examples), "件（selftest.py で再現を確かめる）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
