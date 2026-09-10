"""**sim を全部回す前に**、複数パラメータの効きを1枚ずつ撮って一覧にする。

  python tools/setup_sheet.py --hip scenes/vellum_cloth.hip --frame 24 \
      --probe "/obj/SUBJECT/CONSTRAINTS:bendstiffness=0,1,10" \
      --probe "/obj/SUBJECT/SOLVER:substeps=1,3,10"

ここでいう「複数パラメータ」は**別の効果を持つパラメータ**のこと
（bendstiffness と substeps など）。同じパラメータの段階を並べるのは
flipbook.py の仕事で、こちらではない。

## なぜ要るか

比較動画を1本撮るには 5段階 x 48フレームの sim が要る。それを回し切ってから
「そもそも差が出ない」「画角が悪い」と分かるのは時間の無駄になる。
このツールは各パラメータを1フレームだけ撮るので、**候補をまとめて数分で
篩にかけられる。**

出力はブラウザで見る:  http://127.0.0.1:8765/houdini-lab/setup/

**パラメータごとに元の値へ戻して撮る。**戻さないと前のパラメータの影響が
残り、何を見ているのか分からない絵になる。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from encode import composite_still  # noqa: E402
from flipbook import DEFAULT_HIDDEN, launch, visible_pattern  # noqa: E402
from sweep import parse_values  # noqa: E402

SHEET_DIR = config.CACHE_DIR / "setup"


def parse_probe(text: str) -> dict:
    """'/obj/NODE:parm=1,2,3' を分解する。"""
    m = re.fullmatch(r"\s*([^:]+):([^=]+)=(.+)\s*", text)
    if not m:
        raise SystemExit(
            f"--probe の書式が違います: '{text}'\n"
            "  '/obj/SUBJECT/CONSTRAINTS:bendstiffness=0,1,10' の形で指定してください"
        )
    node, parm, values = m.group(1).strip(), m.group(2).strip(), m.group(3)
    return {"node": node, "parm": parm, "values": parse_values(values)}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")


def write_sheet(cells: list[dict], frame: int, hip: Path) -> Path:
    """撮った画像を集めて、パラメータごとに並べた HTML を書く。"""
    if SHEET_DIR.exists():
        shutil.rmtree(SHEET_DIR, ignore_errors=True)
    SHEET_DIR.mkdir(parents=True, exist_ok=True)

    groups: dict[str, list[dict]] = {}
    for cell in cells:
        name = f"{slug(cell['parm'])}_{slug(str(cell['value']))}.png"
        # **合成してから置く。** 生の PNG は RGBA で、透明部分にも RGB が
        # 入っている。そのまま PSNR に掛けると見えない画素まで計算に入り、
        # 同じ絵でも 15dB のような数字になる（実測）。ブラウザ表示でも
        # ページの地色が透けて本番と違う絵になる。
        composite_still(Path(cell["path"]), SHEET_DIR / name)
        cell["img"] = name
        groups.setdefault(cell["parm"], []).append(cell)

    meta = {
        "hip": str(hip),
        "frame": frame,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "groups": [
            {
                "parm": parm,
                "node": items[0]["node"],
                "default": items[0]["default"],
                "cells": [
                    {"value": c["value"], "img": c["img"], "geo": c.get("geo", {})}
                    for c in items
                ],
            }
            for parm, items in groups.items()
        ],
    }
    (SHEET_DIR / "sheet.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return SHEET_DIR / "sheet.json"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="複数パラメータの効きを1枚ずつ撮って一覧にする（sim 前の確認用）",
    )
    ap.add_argument("--hip", required=True)
    ap.add_argument(
        "--probe", action="append", required=True, metavar="NODE:PARM=v1,v2,...",
        help="撮るパラメータ。複数回指定できる",
    )
    ap.add_argument("--frame", type=int, default=24, help="撮るフレーム（既定 24）")
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument("--width", type=int, default=config.VIDEO_WIDTH)
    ap.add_argument("--height", type=int, default=config.VIDEO_HEIGHT)
    ap.add_argument("--aa", type=int, default=8, choices=(1, 2, 4, 8, 16, 32))
    ap.add_argument(
        "--shading", default="SmoothWire",
        choices=("SmoothWire", "Smooth", "FlatWire", "Flat", "Wire"),
    )
    ap.add_argument(
        "--scheme", default="Light", choices=("keep", "Grey", "DarkGrey", "Dark", "Light"),
    )
    ap.add_argument(
        "--lighting", default="Headlight",
        choices=("Off", "Headlight", "Normal", "HighQuality", "HighQualityWithShadows"),
    )
    ap.add_argument(
        "--work-light", default="Headlight",
        choices=("Headlight", "ThreePoint", "Domelight", "PhysicalSky"),
    )
    ap.add_argument("--hide", default=",".join(DEFAULT_HIDDEN))
    ap.add_argument("--show-guides", action="store_true")
    ap.add_argument(
        "--show-window", action="store_true",
        help="Houdini のウィンドウを表示する（既定は最小化。デバッグ用）",
    )
    ap.add_argument("--timeout", type=float, default=config.FLIPBOOK_TIMEOUT_MIN)
    args = ap.parse_args()

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンファイルがありません: {hip}")

    probes = [parse_probe(p) for p in args.probe]
    total = sum(len(p["values"]) for p in probes)
    print(f"{len(probes)} パラメータ / 計 {total} 枚 を frame {args.frame} で撮ります")
    for p in probes:
        print(f"  {p['node']} / {p['parm']} = {', '.join(str(v) for v in p['values'])}")

    work = config.CACHE_DIR / "shots" / "_setup"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    hidden = [n.strip() for n in args.hide.split(",") if n.strip()]

    started = time.time()
    result = launch(
        {
            "mode": "sheet",
            "hip": str(hip),
            "probes": probes,
            "frame": args.frame,
            "width": args.width,
            "height": args.height,
            "camera": args.camera,
            "visible": visible_pattern(hidden),
            "clean": not args.show_guides,
            "aa": args.aa,
            "shading": args.shading,
            "scheme": args.scheme,
            "lighting": args.lighting,
            "work_light": args.work_light,
            "work": str(work),
            "result": str(work / "result.json"),
            "log": str(work / "setup.log"),
        },
        job_path=work / "job.json",
        log_path=work / "setup.log",
        timeout_s=args.timeout * 60,
        show_window=args.show_window,
    )
    print(f"撮影に {(time.time() - started) / 60:.1f} 分かかりました")

    write_sheet(result["items"], args.frame, hip)
    print(f"完了: {total} 枚")
    print(f"  ブラウザで確認: {config.BASE_URL}/setup/  （serve.py を起動しておくこと）")
    print("  良さそうなパラメータだけ flipbook.py で本撮りしてください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
