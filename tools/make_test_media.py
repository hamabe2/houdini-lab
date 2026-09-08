"""Houdini なしでパイプラインを検証するためのテスト素材を作る。

各セグメントは「値」を背景色で、「時刻」を横に動く四角で表す。
ビューアでスライダーを動かしたとき、背景色だけが変わって四角の位置が
動かなければ「パラメータを変えても再生時刻が保たれる」が成立している。
逆に四角が飛んだら、シークかキーフレームが壊れている。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from encode import encode, verify_keyframes, write_meta  # noqa: E402

FONT = "C\\:/Windows/Fonts/consola.ttf"
COLORS = ["0x14304f", "0x1a5a7a", "0x1f8a70", "0xb08114", "0xa63a2b"]
VALUES = [1e5, 1e6, 1e7, 1e8, 1e9]


def make_segment(out_dir: Path, index: int, value: float, frames: int, w: int, h: int, fps: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    color = COLORS[index % len(COLORS)]

    box_w = max(48, w // 10)
    fontsize = max(20, h // 18)

    # 時刻を表す四角は overlay で動かす。drawbox は使えない —
    # drawbox の変数 t は「タイムスタンプ」ではなく「thickness」なので、
    # t=fill と併用すると座標式が巨大な値になって画面外へ飛ぶ。
    # overlay は n（フレーム番号）と t（秒）を正しく持つ。
    filt = (
        f"[0][1]overlay=x='(W-w)*n/{frames - 1}':y='(H-h)/2'[bg];"
        f"[bg]drawtext=fontfile='{FONT}':text='value {value:.0e}':"
        f"x=40:y=40:fontsize={fontsize}:fontcolor=white,"
        f"drawtext=fontfile='{FONT}':text='frame %{{eif\\:n\\:d\\:3}}':"
        f"x=40:y=H-{fontsize}-40:fontsize={fontsize}:fontcolor=white@0.85[out]"
    )

    cmd = [
        config.FFMPEG, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}",
        "-f", "lavfi", "-i", f"color=c=white:s={box_w}x{box_w}:r={fps}",
        "-filter_complex", filt,
        "-map", "[out]",
        "-frames:v", str(frames),
        str(out_dir / "frame.%04d.png"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗:\n{proc.stderr[-2000:]}")


def main() -> int:
    frames = 48
    w, h = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    fps = config.VIDEO_FPS

    work = config.CACHE_DIR / "selftest"
    if work.exists():
        shutil.rmtree(work)

    seg_dirs = []
    for i, value in enumerate(VALUES):
        seg = work / f"seg{i:03d}"
        make_segment(seg, i, value, frames, w, h, fps)
        seg_dirs.append(seg)
    print(f"生成: {len(seg_dirs)} セグメント x {frames} フレーム")

    out_video = config.MEDIA_DIR / "selftest-pipeline.mp4"
    fps_out = fps
    n = encode(seg_dirs, out_video, fps=fps_out, width=w, height=h)
    print(f"エンコード: {out_video.name} ({n} frames/segment)")

    verify_keyframes(out_video, n, len(VALUES), fps_out)
    print("キーフレーム検証: OK — 全セグメント境界にキーフレームあり")

    write_meta(
        config.MEDIA_DIR / "selftest-pipeline.json",
        video_name=out_video.name,
        id_="selftest-pipeline",
        parm="selftest",
        label="Self Test",
        values=VALUES,
        fps=fps_out,
        frames_per_segment=n,
        width=w,
        height=h,
        default_index=2,
        node="(test)",
    )
    size_kb = out_video.stat().st_size / 1024
    print(f"完了: {size_kb:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
