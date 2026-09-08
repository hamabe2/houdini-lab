"""シーンの1フレームだけを素早くレンダーして確認する。

  python tools/preview.py --hip scenes/vellum_cloth.hip --frame 48

sweep を丸ごと回すと数分かかるので、カメラやライティングの調整には
これを使う。出力は tools/_cache/preview/ に置く。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

HOU_PREVIEW = Path(__file__).resolve().parent / "_hou_preview.py"


def main() -> int:
    ap = argparse.ArgumentParser(description="1フレームだけレンダーして確認する")
    ap.add_argument("--hip", required=True)
    ap.add_argument("--frame", type=int, default=48)
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument("--rop", default="/out/OGL_flipbook")
    ap.add_argument("--out", default="")
    ap.add_argument("--width", type=int, default=config.VIDEO_WIDTH)
    ap.add_argument("--height", type=int, default=config.VIDEO_HEIGHT)
    ap.add_argument(
        "--bbox", action="store_true",
        help="対象のバウンディングボックスとカメラの視野を表示する",
    )
    args = ap.parse_args()

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンがありません: {hip}")

    out_dir = config.CACHE_DIR / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_png = Path(args.out) if args.out else out_dir / f"{hip.stem}_f{args.frame}.png"

    with tempfile.TemporaryDirectory() as tmp:
        job = Path(tmp) / "job.json"
        job.write_text(json.dumps({
            "hip": str(hip),
            "frame": args.frame,
            "camera": args.camera,
            "rop": args.rop,
            "out": str(out_png),
            "width": args.width,
            "height": args.height,
            "bbox": args.bbox,
        }), encoding="utf-8")

        proc = subprocess.run([str(config.HYTHON), str(HOU_PREVIEW), str(job)])
        if proc.returncode != 0:
            raise SystemExit(f"プレビューに失敗しました (exit {proc.returncode})")

    print(f"出力: {out_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
