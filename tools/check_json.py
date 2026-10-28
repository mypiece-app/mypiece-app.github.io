#!/usr/bin/env python3
"""配信の JSON が、公開してよい形かを見る（便434）。

  python3 tools/check_json.py prices.json weather.json

0バイト・JSON として読めない・辞書でない・空の辞書、のどれかなら、そのファイル名と理由を
出して終了コード 1。**そういう物は絶対にコミットしない**（2026-10-28、空の prices.json が公開された）。
全部よければ 0。Python 標準だけ。
"""
import json
import os
import sys


def problem(path):
    """問題の字（無ければ None）。"""
    try:
        if os.path.getsize(path) == 0:
            return "0バイト"
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except OSError as e:
        return "読めない（%s）" % e
    except ValueError:
        return "JSON として読めない"
    if not isinstance(d, dict) or not d:
        return "辞書でない、または空"
    return None


def main(argv):
    bad = 0
    for p in argv:
        why = problem(p)
        if why:
            print("NG %s: %s" % (p, why))
            bad += 1
        else:
            print("ok %s" % p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
