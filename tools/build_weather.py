#!/usr/bin/env python3
"""気象庁の予報を取って app/weather.json を作る。

**相場と同じ作り。**アプリが気象庁を直接叩かない。Mac 側で1日数回取って
JSON にし、画面はそれだけを読む。

**これは `app/build_weather.py` の写し**（便403）。GitHub Actions から回すために
`--out` を足しただけで、**元は書き換えていない。**予報の読み方は 1行も変えていない
（写した時点：便402 の HEAD）。

  python3 app/build_weather.py               # 全予報区（58）
  python3 app/build_weather.py --areas 130000 270000   # 絞って取る

3点確認は済んでいる（docs/3段目調査.md ②）。
  ① 取れる（予報 JSON が 200。天気と気温が実際に抜ける）
  ② robots.txt は 404（＝拒んでいる記述が無い）
  ③ 公共データ利用規約（第1.0版）。商用可。**出典の記載と、加工した旨の記載が要る**

**独自の予報はしない。**気象業務法17条1項（予報業務の許可）と23条（警報の制限）が
気象庁の利用規約から名指しされている。**気象庁が発表した文字列をそのまま持ち回る。**
天気記号を自前の言葉に言い換えることもしない。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

AREA_URL = "https://www.jma.go.jp/bosai/common/const/area.json"
FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/%s.json"
PAGE_URL = "https://www.jma.go.jp/bosai/forecast/#area_type=offices&area_code=%s"

# **出典は値と一緒に持ち回る。**あとから「どこの何か」を思い出せない形にしない
SOURCE = {
    "名前": "気象庁ホームページ",
    "URL": "https://www.jma.go.jp/bosai/forecast/",
    "利用条件": "公共データ利用規約（第1.0版）",
    "利用条件URL": "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0",
    "出典表記": "出典：気象庁ホームページ",
    "加工": "気象庁の発表から、今日と明日の天気・気温・降水確率だけを抜き出して作成",
    "断り": "気象庁の発表をそのまま表示しています。このアプリは予報をしていません。",
}

UA = "jibun-no-hoseki/0.1 (prototype; contact via app developer)"


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def offices():
    """府県予報区の一覧（コードと名前）。area.json から作る。"""
    a = get(AREA_URL)
    out = []
    for code, o in (a.get("offices") or {}).items():
        name = o.get("name") or ""
        if not name:
            continue
        out.append({"code": code, "name": name, "親": (o.get("parent") or "")})
    out.sort(key=lambda x: x["code"])
    return out


#  十勝地方(014030)・奄美地方(460040) は area.json に「予報区」として載っているが、
#  予報JSON自体は無い（実物のGETで404を確認済み・便332）。測候所が廃止され、
#  いまの予報は隣の予報区のJSONに同居している。親の area_code をそのまま出典ページの
#  URL に使わない——人が見るページ（JMA側のSPA）はそのままの code で 200 が返るため、
#  ページURLだけは元の code を使う。
MERGED_INTO = {
    "014030": "014100",  # 十勝地方 → 釧路・根室地方 のJSONに同居
    "460040": "460100",  # 奄美地方 → 鹿児島県（奄美地方除く）のJSONに同居
}


def pick_series(day0, want_code=None):
    """日別の天気・気温・降水確率を、系列の中から探して返す。

    **timeSeries の並び順を決め打ちしない。**中身を見て選ぶ。
    並びは予報区によって違うことがあり、決め打つと静かにずれる（型12）。

    want_code を渡すと、areas[0] ではなく area.code が一致する要素を選ぶ
    （MERGED_INTO で同居先から自分の区だけを抜き出すため）。
    """
    weather, pops, temps = None, None, None
    wa, pa, ta = None, None, None
    for s in day0.get("timeSeries") or []:
        areas = s.get("areas") or []
        if not areas:
            continue
        if want_code is not None:
            a0 = next((a for a in areas
                       if (a.get("area") or {}).get("code") == want_code), None)
            if a0 is None:
                continue
        else:
            a0 = areas[0]
        if weather is None and "weathers" in a0:
            weather, wa = s, a0
        elif pops is None and "pops" in a0:
            pops, pa = s, a0
        elif temps is None and "temps" in a0:
            temps, ta = s, a0
    return weather, pops, temps, wa, pa, ta


def build_area(code, name):
    host = MERGED_INTO.get(code, code)
    d = get(FORECAST_URL % host)
    if not isinstance(d, list) or not d:
        raise ValueError("形が違う")
    day0 = d[0]
    weather, pops, temps, wa, pa, ta = pick_series(day0, want_code=code if host != code else None)
    if weather is None:
        raise ValueError("weathers を持つ系列が無い")

    times = weather.get("timeDefines") or []
    days = []
    for i, t in enumerate(times[:2]):          # 今日と明日だけ
        days.append({
            "日時": t,
            # **気象庁の文字列をそのまま。**言い換えない
            "天気": (wa.get("weathers") or [None] * (i + 1))[i],
            "天気コード": (wa.get("weatherCodes") or [None] * (i + 1))[i],
        })

    if pops is not None:
        pt = pops.get("timeDefines") or []
        for day in days:
            # 同じ日の時間帯ぶんを集める（06-12, 12-18 …）
            vals = [pa["pops"][j] for j, tt in enumerate(pt)
                    if tt[:10] == day["日時"][:10] and j < len(pa.get("pops") or [])]
            vals = [v for v in vals if v not in ("", None)]
            day["降水確率"] = max((int(v) for v in vals), default=None)

    if temps is not None:
        tt = temps.get("timeDefines") or []
        vals = ta.get("temps") or []
        for day in days:
            same = [vals[j] for j, x in enumerate(tt)
                    if x[:10] == day["日時"][:10] and j < len(vals)]
            same = [v for v in same if v not in ("", None)]
            if same:
                nums = [int(v) for v in same]
                day["最低気温"] = min(nums) if len(nums) > 1 else None
                day["最高気温"] = max(nums)

    return {
        "コード": code,
        "名前": name,
        "地域名": wa.get("area", {}).get("name") or name,
        "発表": day0.get("reportDatetime"),
        "発表元": day0.get("publishingOffice"),
        "ページ": PAGE_URL % code,
        "日別": days,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--areas", nargs="*", help="予報区コード（省略すると全部）")
    ap.add_argument("--wait", type=float, default=1.5,
                    help="1件ごとの間隔（秒）。**続けざまに叩かない**")
    # 元は隣の weather.json へ固定で書く。Actions では staging/weather.json へ
    # 書きたいので、**ここだけ**口を開けた（引数 → 環境変数 → 隣の既定）
    ap.add_argument("--out", default=(os.environ.get("WEATHER_OUT")
                                      or os.path.join(HERE, "weather.json")),
                    help="書き出し先（既定は隣の weather.json）")
    a = ap.parse_args()

    try:
        offs = offices()
    except Exception as e:
        print("地点表が取れない: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 2
    if a.areas:
        want = set(a.areas)
        offs = [o for o in offs if o["code"] in want]
        missing = want - {o["code"] for o in offs}
        if missing:
            print("知らない予報区コード: %s" % ", ".join(sorted(missing)), file=sys.stderr)
    print("予報区 %d件" % len(offs), file=sys.stderr)

    areas, errs = [], []
    for i, o in enumerate(offs):
        try:
            areas.append(build_area(o["code"], o["name"]))
        except Exception as e:
            # **1件落ちても全部を捨てない。**落ちたことは記録して残す
            errs.append({"コード": o["code"], "名前": o["name"],
                         "理由": "%s: %s" % (type(e).__name__, e)})
            print("  NG %s %s … %s" % (o["code"], o["name"], e), file=sys.stderr)
        if i + 1 < len(offs):
            time.sleep(a.wait)

    if not areas:
        print("1件も取れなかった。weather.json は書き換えない。", file=sys.stderr)
        return 1

    out = {
        "出典": SOURCE,
        "取得": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        "件数": len(areas),
        "取れなかったもの": errs,
        "予報区": areas,
    }
    # **1件も取れなかった日に古い JSON を壊さない**（上の return 1 で止めてある）。
    # 一時ファイルへ書いて、書き切れたときだけ差し替える
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    print("書いた: %s（%d件／取れなかったもの %d件）" % (a.out, len(areas), len(errs)),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
