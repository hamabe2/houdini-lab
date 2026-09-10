"""パラメータを振って比較動画を作る CLI。

  python tools/sweep.py --hip scenes/vellum_cloth.hip \
      --node /obj/cloth/vellumconstraints1 \
      --parm stretchstiffness \
      --values 1e5,1e6,1e7,1e8,1e9 \
      --frames 1-48 \
      --out vellum-cloth-stretch

--draft を付けると解像度・フレーム数・段階数を落として高速に試し撮りする。
「振ってみたけど見た目が変わらなかった」という無駄な sim を、本番前に
切り捨てるためのもの。出力は tools/_cache/ に置き、media/ は汚さない。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from encode import encode, verify_keyframes, write_meta  # noqa: E402

HOU_SWEEP = Path(__file__).resolve().parent / "_hou_sweep.py"


def parse_values(text: str) -> list[float]:
    values = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.append(float(chunk))
        except ValueError:
            raise SystemExit(f"数値として読めません: '{chunk}'")
    if len(values) < 2:
        raise SystemExit("--values には2つ以上の値が必要です")
    return values


def parse_frames(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", text)
    if not m:
        raise SystemExit(f"--frames は '1-48' の形式で指定してください: '{text}'")
    f1, f2 = int(m.group(1)), int(m.group(2))
    if f2 < f1:
        raise SystemExit(f"--frames の範囲が逆です: {text}")
    return f1, f2


def thin_values(values: list[float], keep: int) -> list[float]:
    """draft 用に先頭・中央・末尾へ間引く。"""
    if len(values) <= keep:
        return values
    idx = sorted({round(i * (len(values) - 1) / (keep - 1)) for i in range(keep)})
    return [values[i] for i in idx]


def check_disk_space() -> None:
    try:
        usage = shutil.disk_usage(config.CACHE_ROOT.drive + "\\")
    except (OSError, ValueError):
        return
    free_gb = usage.free / (1024 ** 3)
    if free_gb < config.CACHE_FREE_WARN_GB:
        print(
            f"警告: {config.CACHE_ROOT.drive} の空きが {free_gb:.0f}GB しかありません。"
            f"（{config.CACHE_ROOT} にキャッシュを置く設定です）",
            file=sys.stderr,
        )


def run_houdini(job: dict) -> dict:
    if not config.HYTHON.exists():
        raise SystemExit(
            f"hython が見つかりません: {config.HYTHON}\n"
            "  config.py の HOUDINI_ROOT を確認してください。"
        )

    with tempfile.TemporaryDirectory() as tmp:
        job_path = Path(tmp) / "job.json"
        result_path = Path(tmp) / "result.json"
        job["result"] = str(result_path)
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

        env_note = f"$HLCACHE = {config.CACHE_ROOT}"
        print(f"hython を起動します（{env_note}）")

        import os

        env = os.environ.copy()
        env["HLCACHE"] = str(config.CACHE_ROOT)
        config.CACHE_ROOT.mkdir(parents=True, exist_ok=True)

        proc = subprocess.run(
            [str(config.HYTHON), str(HOU_SWEEP), str(job_path)],
            env=env,
        )
        if proc.returncode != 0:
            raise SystemExit(f"Houdini での撮影が失敗しました (exit {proc.returncode})")
        if not result_path.exists():
            raise SystemExit("撮影結果が出力されませんでした")
        return json.loads(result_path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Houdini のパラメータを振って比較動画を作る",
    )
    ap.add_argument("--hip", required=True, help="シーンファイル")
    ap.add_argument("--node", required=True, help="対象ノードのパス")
    ap.add_argument("--parm", required=True, help="振るパラメータ名")
    ap.add_argument("--values", required=True, help="カンマ区切りの値 (例 1e5,1e6,1e7)")
    ap.add_argument("--frames", default="1-48", help="フレーム範囲 (既定 1-48)")
    ap.add_argument("--out", required=True, help="出力 ID (例 vellum-cloth-stretch)")
    ap.add_argument("--label", default="", help="表示ラベル (既定はパラメータ名)")
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument("--rop", default="/out/OGL_flipbook")
    ap.add_argument(
        "--default-index", type=int, default=None,
        help="Houdini の既定値が何番目か（0 始まり）。ビューアの初期位置になる",
    )
    ap.add_argument("--draft", action="store_true", help="低解像度で試し撮りする")
    ap.add_argument("--keep-frames", action="store_true", help="PNG 連番を消さない")
    args = ap.parse_args()

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンファイルがありません: {hip}")

    values = parse_values(args.values)
    f1, f2 = parse_frames(args.frames)

    if args.draft:
        values = thin_values(values, config.DRAFT_STEPS)
        f2 = min(f2, f1 + config.DRAFT_FRAMES - 1)
        width, height = config.DRAFT_WIDTH, config.DRAFT_HEIGHT
        out_dir = config.CACHE_DIR / "draft"
        print(f"draft モード: {len(values)} 段階 x {f2 - f1 + 1} フレーム / {width}x{height}")
    else:
        width, height = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
        out_dir = config.MEDIA_DIR

    check_disk_space()

    work = config.CACHE_DIR / "shots" / args.out
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    started = time.time()
    result = run_houdini({
        "hip": str(hip),
        "node": args.node,
        "parm": args.parm,
        "values": values,
        "f1": f1,
        "f2": f2,
        "width": width,
        "height": height,
        "camera": args.camera,
        "rop": args.rop,
        "work": str(work),
    })
    sim_min = (time.time() - started) / 60
    print(f"撮影に {sim_min:.1f} 分かかりました")

    seg_dirs = [Path(s["dir"]) for s in result["segments"]]
    video = out_dir / f"{args.out}.mp4"
    fps = config.VIDEO_FPS

    frames_per_segment = encode(seg_dirs, video, fps=fps, width=width, height=height)
    verify_keyframes(video, frames_per_segment, len(values), fps)
    print(f"キーフレーム検証: OK ({frames_per_segment} frames/segment)")

    # サイトに出すのは Houdini の入力欄の値ではなく、隣の「× 10^N」を
    # 掛けた後の実効値。Houdini 側が計算して返してくる。
    display = result.get("display_values") or values
    multiplier = result.get("multiplier") or None
    if multiplier:
        print(f"表記は実効値: {display}  （{multiplier['source']}）")

    camera_name = args.camera.rsplit("/", 1)[-1]
    write_meta(
        out_dir / f"{args.out}.json",
        video_name=video.name,
        id_=args.out,
        parm=args.parm,
        label=args.label or args.parm,
        values=display,
        raw_values=values,
        multiplier=multiplier,
        fps=fps,
        frames_per_segment=frames_per_segment,
        width=width,
        height=height,
        default_index=args.default_index,
        camera=camera_name,
        node=args.node,
    )

    if not args.keep_frames:
        shutil.rmtree(work, ignore_errors=True)

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"完了: {video}  ({size_mb:.2f} MB)")
    if args.draft:
        print("draft です。差が出たパラメータだけ --draft を外して撮り直してください。")
    else:
        print(f"記事に  :::compare {args.out}  と書けば埋め込まれます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
