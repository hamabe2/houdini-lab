"""シーンの静止画を撮って見た目を確認する。

  python tools/preview.py --hip scenes/vellum_cloth.hip --frame 1 --frame 24 --frame 48

**既定は flipbook 経路（本番と同じ絵）。**床のグリッド・背景・シェーディングが
本番の mp4 と揃う。GUI の houdini.exe が一度だけ立ち上がり、指定した
全フレームを1セッションで撮る。

`--via ogl` にすると hython + OpenGL ROP で速く撮れるが、**床も背景も無い**。
ジオメトリの形だけを見たいとき用で、画角や見え方の判断には使わないこと。

出力は tools/_cache/preview/ に置き、ブラウザで見る:
  http://127.0.0.1:8765/houdini-lab/preview/
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from encode import composite_still  # noqa: E402
from flipbook import DEFAULT_HIDDEN, launch, visible_pattern  # noqa: E402

HOU_PREVIEW = Path(__file__).resolve().parent / "_hou_preview.py"

# 照明の候補。`--look` で選ぶ。複数渡すと1回の起動で見比べられる。
#
# **モード（Headlight / Normal / ...）は 0灯のシーンでは効かない**ので、
# ここで振っているのはヘッドライトの向きと質のほう。既定のヘッドライトは
# カメラと同軸で、真正面から当たるため陰影の勾配が出ない（＝平板に見える）。
# **work light を使うには lighting を Headlight にすること。**
# setWorkLightType の docstring に明記されている。Normal / HighQuality の
# ままだと work light は無視され、平板なヘッドライトのままになる
# （実測: モードだけ変えた4枚がバイト単位で一致した）。
LOOKS: dict[str, dict] = {
    "headlight": {
        "lighting": "Headlight",
        "work_light": "Headlight",   # 肩越しの distant light 1灯
    },
    "threepoint": {
        "lighting": "Headlight",
        "work_light": "ThreePoint",  # 3点照明
    },
    "dome": {
        "lighting": "Headlight",
        "work_light": "Domelight",   # 環境光
    },
    "sky": {
        "lighting": "Headlight",
        "work_light": "PhysicalSky", # 太陽 + 空
    },
}


def out_path(out_dir: Path, name: str, hip: Path, frame: int) -> Path:
    """--out は既定でプレビュー用ディレクトリの中の「名前」として扱う。

    素のパスとして受けると '--out v4-f24' がカレントディレクトリに拡張子
    無しのファイルを作る。しかもブラウザの一覧には出ないので、外からは
    「撮れていない」ようにしか見えない。
    """
    if not name:
        return out_dir / f"{hip.stem}_f{frame}.png"
    given = Path(name)
    path = given if given.parent != Path(".") else out_dir / given.name
    return path if path.suffix else path.with_suffix(".png")


def via_flipbook(args: argparse.Namespace, hip: Path, out_dir: Path) -> list[Path]:
    """ビューポートそのものを撮る（本番と同じ絵）。"""
    work = config.CACHE_DIR / "shots" / "_preview"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    hidden = [n.strip() for n in args.hide.split(",") if n.strip()]
    log_path = work / "preview.log"

    result = launch(
        {
            "mode": "stills",
            "hip": str(hip),
            "frames": args.frame,
            "width": args.width,
            "height": args.height,
            "camera": args.camera,
            "lighting": args.lighting,
            "work_light": args.work_light,
            # --look は見比べ用。指定すると上の既定を上書きして1枚ずつ撮る。
            "looks": [dict(LOOKS[name], name=name) for name in args.look],
            "visible": visible_pattern(hidden),
            "clean": not args.show_guides,
            "aa": args.aa,
            "shading": args.shading,
            "scheme": args.scheme,
            "work": str(work),
            "result": str(work / "result.json"),
            "log": str(log_path),
        },
        job_path=work / "job.json",
        log_path=log_path,
        timeout_s=args.timeout * 60,
        show_window=args.show_window,
    )

    written = []
    for i, shot in enumerate(result["items"]):
        name = args.out[i] if i < len(args.out) else ""
        if not name and shot.get("look"):
            # 見比べるときは、どれがどれか名前で分かるようにする
            name = f"{hip.stem}_f{shot['frame']}_{shot['look']}"
        dst = out_path(out_dir, name, hip, shot["frame"])
        # flipbook の PNG は RGBA。合成しないと本番と違う絵を見ることになる。
        composite_still(Path(shot["path"]), dst, args.width, args.height)
        written.append(dst)
    return written


def via_ogl(args: argparse.Namespace, hip: Path, out_dir: Path) -> list[Path]:
    """OpenGL ROP で撮る。速いが床も背景も無い。"""
    print("注意: --via ogl は床のグリッドも背景も描きません。")
    print("      画角や見え方の判断には使わないでください（本番と別の絵です）。")

    written = []
    for i, frame in enumerate(args.frame):
        dst = out_path(out_dir, args.out[i] if i < len(args.out) else "", hip, frame)
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job.json"
            job.write_text(json.dumps({
                "hip": str(hip),
                "frame": frame,
                "camera": args.camera,
                "rop": args.rop,
                "out": str(dst),
                "width": args.width,
                "height": args.height,
                "bbox": args.bbox,
            }), encoding="utf-8")

            proc = subprocess.run([str(config.HYTHON), str(HOU_PREVIEW), str(job)])
            if proc.returncode != 0:
                raise SystemExit(f"プレビューに失敗しました (exit {proc.returncode})")
        written.append(dst)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="静止画を撮って見た目を確認する")
    ap.add_argument("--hip", required=True)
    ap.add_argument(
        "--frame", type=int, action="append", default=[],
        help="撮るフレーム。複数回指定できる（既定 24）",
    )
    ap.add_argument(
        "--via", choices=("flipbook", "ogl"), default="flipbook",
        help="撮影経路。既定 flipbook（本番と同じ絵）",
    )
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument(
        "--out", action="append", default=[],
        help="出力名（例 v4-f24）。--frame と同じ順で対応する",
    )
    ap.add_argument("--width", type=int, default=config.VIDEO_WIDTH)
    ap.add_argument("--height", type=int, default=config.VIDEO_HEIGHT)
    ap.add_argument("--aa", type=int, default=8, choices=(1, 2, 4, 8, 16, 32))
    ap.add_argument(
        "--shading", default="SmoothWire",
        choices=("SmoothWire", "Smooth", "FlatWire", "Flat", "Wire"),
    )
    ap.add_argument(
        "--scheme", default=config.VIEWPORT_SCHEME, choices=("keep", "Grey", "DarkGrey", "Dark", "Light"),
    )
    ap.add_argument(
        "--lighting", default="Headlight",
        choices=("Off", "Headlight", "Normal", "HighQuality", "HighQualityWithShadows"),
    )
    ap.add_argument(
        "--work-light", default="Headlight",
        choices=("Headlight", "ThreePoint", "Domelight", "PhysicalSky"),
    )
    ap.add_argument(
        "--look", action="append", default=[], choices=tuple(LOOKS),
        help="照明の候補を見比べる。複数指定すると1回の起動で撮る",
    )
    ap.add_argument("--hide", default=",".join(DEFAULT_HIDDEN))
    ap.add_argument("--show-guides", action="store_true")
    ap.add_argument("--show-window", action="store_true")
    ap.add_argument("--timeout", type=float, default=config.FLIPBOOK_TIMEOUT_MIN)
    # --via ogl 用
    ap.add_argument("--rop", default="/out/OGL_flipbook")
    ap.add_argument(
        "--bbox", action="store_true",
        help="対象の bbox とカメラの視野を表示する（--via ogl のみ。"
             "lookatpath を使うカメラでは計算が成り立たない）",
    )
    args = ap.parse_args()

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンがありません: {hip}")
    if not args.frame:
        args.frame = [24]

    out_dir = config.CACHE_DIR / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = via_ogl(args, hip, out_dir) if args.via == "ogl" else via_flipbook(args, hip, out_dir)

    for path in written:
        print(f"出力: {path}")
    print(f"  ブラウザで確認: {config.BASE_URL}/preview/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
