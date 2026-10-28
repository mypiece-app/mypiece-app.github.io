#!/usr/bin/env python3
"""配信の JSON 2本を比べる（便403）。**Mac が作った物と Actions が作った物が同じか。**

  python3 tools/compare.py prices.json staging/prices.json
  python3 tools/compare.py weather.json staging/weather.json --max 20

**時刻の鍵（生成時刻・取得）だけの差は「同じ」と出す。**ここは走らせた時刻が入るので、
いつ比べても必ず違う。それを数えると、本物の差が埋もれる。

出る物は 2つの数。**混ぜないこと。**

  形の差 … 片方にしか無い鍵・型が違う・配列の長さが違う。**これが 1件でもあれば作り
            方が変わっている**（アプリが読めなくなりうる）
  値の差 … 鍵も形も同じで、中身の数や字が違う。**相場も予報も時間で動くので、
            取った時刻が違えば出るのが当たり前。**0 でなければ駄目という物ではない

終わりの番号（他の道具から呼べるように）
  0 … 形の差 0・値の差 0（時刻の鍵を除いて 1字も違わない）
  1 … 形の差 0・値の差あり（作り方は同じ。発表が進んだぶん）
  2 … 形の差あり（**作り方が変わっている。見ること**）
  3 … 読めない（ファイルが無い・JSON が壊れている）
"""
import argparse
import json
import sys

# 走らせた時刻が入る鍵。**値が違って当たり前なので数えない**
TIME_KEYS = ("生成時刻", "取得", "状態")  # 状態は取れた日・取れなかった日で形が変わる（便432）


def walk(a, b, path, shape, value, time_keys):
    """a と b を同じ道筋でたどり、差を shape / value に積む。"""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a.keys()) + [k for k in b.keys() if k not in a]:
            p = path + "." + str(k) if path else str(k)
            if k in time_keys:
                continue
            if k not in b:
                shape.append((p, "こちらにしか無い", ""))
            elif k not in a:
                shape.append((p, "", "あちらにしか無い"))
            else:
                walk(a[k], b[k], p, shape, value, time_keys)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            shape.append((path, "%d件" % len(a), "%d件" % len(b)))
            return
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, "%s[%d]" % (path, i), shape, value, time_keys)
        return
    if type(a) is not type(b) and not (isinstance(a, (int, float))
                                       and isinstance(b, (int, float))):
        shape.append((path, type(a).__name__, type(b).__name__))
        return
    if a != b:
        value.append((path, a, b))


def cut(v, n=40):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a", help="Mac が作った物（置き場のルート）")
    ap.add_argument("b", help="Actions が作った物（staging）")
    ap.add_argument("--max", type=int, default=12, help="出す差の件数（既定12）")
    ap.add_argument("--time-keys", nargs="*", default=list(TIME_KEYS),
                    help="走らせた時刻が入る鍵（既定：生成時刻 取得）")
    o = ap.parse_args()

    loaded = []
    for p in (o.a, o.b):
        try:
            with open(p, encoding="utf-8") as f:
                loaded.append(json.load(f))
        except Exception as e:
            print("読めない %s … %s: %s" % (p, type(e).__name__, e), file=sys.stderr)
            return 3

    shape, value = [], []
    walk(loaded[0], loaded[1], "", shape, value, set(o.time_keys))

    print("A %s" % o.a)
    print("B %s" % o.b)
    for k in o.time_keys:
        va, vb = loaded[0].get(k), loaded[1].get(k)
        if va is not None or vb is not None:
            print("  %s（数えない）  A %s  /  B %s" % (k, va, vb))
    print("形の差 %d件 ／ 値の差 %d件" % (len(shape), len(value)))

    for name, rows in (("形の差", shape), ("値の差", value)):
        for p, x, y in rows[:o.max]:
            print("  [%s] %s  A %s  →  B %s" % (name, p or "(根)", cut(x), cut(y)))
        if len(rows) > o.max:
            print("  [%s] … ほか %d件" % (name, len(rows) - o.max))

    if shape:
        print("**形が違う。作り方が変わっている。**")
        return 2
    if value:
        print("形は同じ。値だけが違う（取った時刻の差で出る）。")
        return 1
    print("時刻の鍵を除いて 1字も違わない。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
