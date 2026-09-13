"""PNG 連番（パラメータ値ごと）を1本の mp4 に連結し、メタ JSON を出力する。

ビューアは currentTime = (p * frames_per_segment + t) / fps でシークして
パラメータを切り替える。そのため各セグメントの先頭には必ずキーフレームが
無ければならない。キーフレームが無いと currentTime 指定が直前のキーフレームに
丸められ、「スライダーを動かすと別の値の映像が出る」という無言の破損になる。

このモジュールは -force_key_frames でそれを打ち込み、さらに ffprobe で
実際に入ったことを検証してから完了する。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


# overlay の合成を行う色空間。
#
# **既定の `yuv420` を使わないこと。** overlay は混ぜる前に両入力をこの形式へ
# 変換するため、既定のままだと前景のクロマを半分に落としてから合成することに
# なり、1〜2px のエッジで色が消える。実測（布のシルエット、暗い背景に合成）:
#
#   yuv420  (125,123,124)  ← 無彩色に潰れている
#   rgb     (135,121,115)  ← 手計算したストレート alpha の over と完全一致
#
# yuv420p への変換は最後の1回（-pix_fmt）だけにする。
OVERLAY_FORMAT = "rgb"


class EncodeError(RuntimeError):
    pass


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise EncodeError(
            f"コマンドが失敗しました (exit {proc.returncode}):\n"
            f"  {' '.join(cmd[:6])} ...\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


def count_frames(seg_dir: Path) -> int:
    return len(sorted(seg_dir.glob("*.png")))


def verify_segments(seg_dirs: list[Path]) -> int:
    """全セグメントのフレーム数が揃っていることを確認し、その値を返す。

    揃っていないと currentTime の計算が全部ずれるので、ここで止める。
    """
    if not seg_dirs:
        raise EncodeError("セグメントが1つもありません")

    counts = {d.name: count_frames(d) for d in seg_dirs}
    empty = [name for name, n in counts.items() if n == 0]
    if empty:
        raise EncodeError(f"PNG が空のセグメントがあります: {', '.join(empty)}")

    uniq = set(counts.values())
    if len(uniq) != 1:
        detail = "\n".join(f"    {name}: {n} frames" for name, n in counts.items())
        raise EncodeError(
            "セグメントごとにフレーム数が違います。全セグメントは同じ長さである\n"
            "必要があります（ビューアのシーク計算が崩れるため）:\n" + detail
        )

    return uniq.pop()


def encode(
    seg_dirs: list[Path],
    out_path: Path,
    fps: int = config.VIDEO_FPS,
    crf: int = config.VIDEO_CRF,
    width: int = config.VIDEO_WIDTH,
    height: int = config.VIDEO_HEIGHT,
    bg: str = config.VIDEO_BG,
) -> int:
    """セグメントを連結して mp4 を書き出し、frames_per_segment を返す。

    **PNG は RGBA なので、必ず不透明な背景に合成してから yuv420p に落とす。**
    Houdini の flipbook はビューポートの背景を alpha=0、床のグリッドを
    「白 + alpha 16%」のように半透明で書き出す。合成せずに変換すると ffmpeg は
    alpha を捨てて RGB をそのまま使うため、背景は純黒、グリッドは純白になり、
    ジオメトリのエッジもアンチエイリアスが消えてジャギーになる。
    """
    frames_per_segment = verify_segments(seg_dirs)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [config.FFMPEG, "-y"]
    for seg in seg_dirs:
        first = sorted(seg.glob("*.png"))[0]
        pattern = _glob_to_pattern(first)
        cmd += ["-framerate", str(fps), "-start_number", _start_number(first), "-i", str(pattern)]

    n = len(seg_dirs)
    streams = "".join(f"[{i}:v]" for i in range(n))
    # scale はセグメント間で解像度がずれていた場合の保険。通常は素通り。
    filt = (
        f"{streams}concat=n={n}:v=1:a=0[cat];"
        f"[cat]scale={width}:{height}[fg];"
        f"color=c={bg}:s={width}x{height}:r={fps}[bg];"
        f"[bg][fg]overlay=shortest=1:format={OVERLAY_FORMAT}[out]"
    )

    cmd += [
        "-filter_complex", filt,
        "-map", "[out]",
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        # ここが本質。セグメント境界に必ずキーフレームを打つ。
        "-force_key_frames", f"expr:eq(mod(n,{frames_per_segment}),0)",
        "-movflags", "+faststart",
        "-an",
        str(out_path),
    ]
    _run(cmd)
    return frames_per_segment


def composite_still(
    src: Path,
    dst: Path,
    width: int = config.VIDEO_WIDTH,
    height: int = config.VIDEO_HEIGHT,
    bg: str = config.VIDEO_BG,
) -> None:
    """RGBA の PNG 1枚を不透明な背景に合成する。

    **確認用の1枚にも合成が要る。** flipbook の PNG は背景が alpha=0、
    床のグリッドが半透明で、そのままブラウザに出すとページの地の色が
    透けて本番の mp4 と違う絵になる。判断材料としては使えない。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        config.FFMPEG, "-y", "-i", str(src),
        "-filter_complex",
        f"color=c={bg}:s={width}x{height}[bg];"
        f"[bg][0:v]overlay=shortest=1:format={OVERLAY_FORMAT}",
        "-frames:v", "1",
        str(dst),
    ])


def psnr(a: Path, b: Path) -> float:
    """2枚の画像の PSNR を dB で返す。同一なら inf。

    **必ず合成済みの画像を渡すこと。** flipbook の PNG は RGBA で、背景の
    alpha=0 の部分にも RGB が入っている。生のまま比べるとその見えない画素まで
    計算に入り、同じ絵でも 15dB のような数字になる（実測）。

    「振っても差が出ないパラメータ」を主観ではなく数値で落とすための物差し。
    """
    out = subprocess.run(
        [
            config.FFMPEG, "-hide_banner",
            "-i", str(a), "-i", str(b),
            "-filter_complex", "psnr", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    # psnr フィルタは stderr に "PSNR y:.. u:.. v:.. average:.. min:.. max:.." を出す
    for line in reversed(out.stderr.splitlines()):
        if "average:" in line:
            for token in line.split():
                if token.startswith("average:"):
                    value = token.split(":", 1)[1]
                    return float("inf") if value == "inf" else float(value)
    raise EncodeError(
        f"PSNR を取得できませんでした: {a.name} vs {b.name}\n{out.stderr[-800:]}"
    )


def _glob_to_pattern(first_png: Path) -> Path:
    """frame.0001.png -> frame.%04d.png"""
    stem = first_png.stem
    head, _, digits = stem.rpartition(".")
    if not head or not digits.isdigit():
        raise EncodeError(
            f"PNG のファイル名が想定と違います: {first_png.name}\n"
            "  'name.0001.png' の形式である必要があります"
        )
    return first_png.with_name(f"{head}.%0{len(digits)}d.png")


def _start_number(first_png: Path) -> str:
    return str(int(first_png.stem.rpartition(".")[2]))


def keyframe_positions(video: Path) -> list[int]:
    """キーフレームのフレーム番号を返す。"""
    out = _run([
        config.FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-skip_frame", "nokey",
        "-show_entries", "frame=pts_time",
        "-of", "csv=p=0",
        str(video),
    ])
    # csv=p=0 でも項目区切りのカンマが行末に残ることがある
    return [float(line.strip().rstrip(",")) for line in out.split() if line.strip()]


def verify_keyframes(video: Path, frames_per_segment: int, n_values: int, fps: int) -> None:
    """各セグメント先頭にキーフレームが実在することを確認する。

    ここを通さないと、ビューアは「動くが違う値を表示する」という
    気づきにくい壊れ方をする。
    """
    times = keyframe_positions(video)
    keyframes = {round(t * fps) for t in times}

    expected = [p * frames_per_segment for p in range(n_values)]
    # エンコーダのタイムスタンプ丸めを考慮して ±1 フレームを許容する
    missing = [f for f in expected if not (keyframes & {f - 1, f, f + 1})]

    if missing:
        raise EncodeError(
            "セグメント境界にキーフレームがありません: "
            f"frame {missing}\n"
            f"  実際のキーフレーム: {sorted(keyframes)}\n"
            "  この状態ではビューアのシークが別の値にずれます。"
        )


def write_meta(
    out_json: Path,
    *,
    video_name: str,
    id_: str,
    parm: str,
    label: str,
    values: list[float],
    fps: int,
    frames_per_segment: int,
    width: int,
    height: int,
    default_index: int | None = None,
    camera: str = config.DEFAULT_CAMERA,
    node: str = "",
    raw_values: list[float] | None = None,
    multiplier: dict | None = None,
) -> None:
    # `values` はサイトに表記する値＝実効値。Houdini の入力欄に入れた値とは
    # 別物になることがある（隣の「× 10^N」メニューが掛かるため）。
    # 何を入れたのかは raw_values / multiplier に残す。
    meta = {
        "id": id_,
        "src": video_name,
        "parm": parm,
        "label": label,
        "node": node,
        "values": values,
        "raw_values": raw_values if raw_values != values else None,
        "multiplier": multiplier or None,
        "default_index": default_index,
        "fps": fps,
        "frames_per_segment": frames_per_segment,
        "width": width,
        "height": height,
        "camera": camera,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(meta, indent=2, ensure_ascii=False)

    # 開発サーバーが media/ を監視して再ビルド中だと、コピーの最中に
    # ファイルを掴まれて書き込みが弾かれることがある。数回待って再試行する。
    for attempt in range(6):
        try:
            out_json.write_text(text, encoding="utf-8")
            return
        except PermissionError:
            if attempt == 5:
                raise EncodeError(
                    f"{out_json} に書き込めません。\n"
                    "  開発サーバー（serve.py）や他のプロセスが掴んでいる可能性があります。"
                )
            time.sleep(0.5)
