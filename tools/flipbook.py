"""ビューポートそのものを flipbook で撮って比較動画を作る CLI。

  python tools/flipbook.py --hip scenes/vellum_cloth.hip \
      --node /obj/SUBJECT/CONSTRAINTS --parm bendstiffness \
      --values 0,0.1,1,5,10 --frames 1-48 --out vellum-cloth-bend

sweep.py と入出力は同じ（同じ mp4 と JSON が出る）。違うのは絵の作り方だけ。

  sweep.py    OpenGL ROP。hython で回るのでヘッドレス。ただし背景・床は
              ジオメトリで模倣したもので、ビューポートの見た目とは別物。
  flipbook.py ビューポートをそのまま撮る。GUI の houdini.exe が必要。

hython に flipbook は無い（`hou.SceneViewer.flipbook()` はビューアを要求する）
ため、この経路は houdini.exe を GUI で起動し、HOUDINI_PATH に差し込んだ
`scripts/456.py` フック経由でセッションの中に入り込む。

そのため、撮影中は Houdini のウィンドウが開き、勝手に操作される。
終わると自分で閉じる。触らずに待つこと。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from encode import encode, verify_keyframes, write_meta  # noqa: E402
from sweep import check_disk_space, parse_frames, parse_values, thin_values  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent
HOOK_DIR = TOOLS_DIR / "flipbook_hooks"

# 既定で隠す /obj のノード。
#
# BACKDROP / GROUND : ビューポートは背景も床のグリッドも自前で描く。この2つは
#   それを持たない OpenGL ROP のための代用品なので、残すと実物と二重に写る。
# LIGHT_key : ビューポートはライトを「ギズモ」として線で描き、それが対象の
#   手前に重なる。ビューポートのガイド設定（enableGuide）では消えず、
#   visibleObjects から外すしかない。外すとビューポートはヘッドライトに
#   切り替わるが、template.hip がそもそも「常にカメラ方向から均一に当たる
#   ヘッドライトの方が比較に適している」という理由で1灯しか置いていないので、
#   狙いは変わらない（実測での差は布の領域で PSNR 45.9dB＝ごく僅か）。
#   ライトの効きを見たい題材では --hide で外すこと。
DEFAULT_HIDDEN = ("BACKDROP", "GROUND", "LIGHT_key")


def visible_pattern(hidden: list[str]) -> str:
    """visibleObjects 用のパターンを作る。'*' から除外を引く。"""
    return " ".join(["*"] + [f"^{name}" for name in hidden if name])


def launch(job: dict, job_path: Path, log_path: Path, timeout_s: float) -> dict:
    """GUI の houdini.exe を起動し、result.json が出るまで待つ。

    GUI アプリの終了コードは当てにならないので、判定は result.json で行う。
    進捗も stdout ではなくログファイルから拾う（houdini.exe は GUI サブ
    システムのアプリで、Windows では print が親のコンソールに届かない）。
    """
    if not config.HOUDINI_GUI.exists():
        raise SystemExit(
            f"houdini.exe が見つかりません: {config.HOUDINI_GUI}\n"
            "  config.py の HOUDINI_ROOT を確認してください。"
        )

    result_path = Path(job["result"])
    result_path.unlink(missing_ok=True)
    log_path.unlink(missing_ok=True)
    job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")

    env = os.environ.copy()
    # 末尾の '&' は「既定の HOUDINI_PATH もそのまま使う」の意味。
    # これを落とすと Houdini は自分のツール類を見失って起動に失敗する。
    existing = env.get("HOUDINI_PATH", "&")
    env["HOUDINI_PATH"] = f"{HOOK_DIR};{existing}"
    env["HOUDINI_LAB_FLIPBOOK_JOB"] = str(job_path)
    env["HOUDINI_LAB_TOOLS"] = str(TOOLS_DIR)
    env["HLCACHE"] = str(config.CACHE_ROOT)
    config.CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    print(f"houdini.exe を起動します（$HLCACHE = {config.CACHE_ROOT}）")
    print("  Houdini のウィンドウが開きます。終わるまで触らないでください。")

    # hip は渡さない。フック側で ignore_load_warnings 付きで開く。
    # コマンドラインで開かせると、警告ダイアログが出た時点で誰も操作できず
    # 固まってしまう。
    proc = subprocess.Popen([str(config.HOUDINI_GUI)], env=env)

    try:
        _wait(proc, result_path, log_path, timeout_s)
    finally:
        if proc.poll() is None:
            proc.terminate()

    if not result_path.exists():
        raise SystemExit("撮影結果が出力されませんでした")

    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("error"):
        raise SystemExit(f"Houdini 側で失敗しました: {result['error']}")
    return result


def _wait(proc: subprocess.Popen, result_path: Path, log_path: Path, timeout_s: float) -> None:
    """result.json が出るまで待ちつつ、ログの新着行を流す。"""
    deadline = time.time() + timeout_s
    shown = 0
    exited_at = None

    while True:
        shown = _drain_log(log_path, shown)

        if result_path.exists():
            _drain_log(log_path, shown)
            return

        if proc.poll() is not None:
            # プロセスは終わったのに result.json が無い。書き込みとプロセス
            # 終了の競合を考えて少しだけ猶予を与えてから諦める。
            if exited_at is None:
                exited_at = time.time()
            elif time.time() - exited_at > 5:
                _drain_log(log_path, shown)
                raise SystemExit(
                    f"Houdini が結果を残さずに終了しました (exit {proc.returncode})\n"
                    f"  ログ: {log_path}"
                )

        if time.time() > deadline:
            raise SystemExit(
                f"時間切れです（{timeout_s / 60:.0f} 分）。\n"
                "  Houdini がダイアログを出して止まっている可能性があります。\n"
                f"  ログ: {log_path}"
            )

        time.sleep(1.0)


def _drain_log(log_path: Path, shown: int) -> int:
    """ログの未表示行を出力し、表示済みの行数を返す。"""
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return shown
    for line in lines[shown:]:
        print(f"  | {line}")
    return len(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ビューポートを flipbook で撮って比較動画を作る",
    )
    ap.add_argument("--hip", required=True, help="シーンファイル")
    ap.add_argument("--node", required=True, help="対象ノードのパス")
    ap.add_argument("--parm", required=True, help="振るパラメータ名")
    ap.add_argument("--values", required=True, help="カンマ区切りの値 (例 1e5,1e6,1e7)")
    ap.add_argument("--frames", default="1-48", help="フレーム範囲 (既定 1-48)")
    ap.add_argument("--out", required=True, help="出力 ID (例 vellum-cloth-bend)")
    ap.add_argument("--label", default="", help="表示ラベル (既定はパラメータ名)")
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument(
        "--hide", default=",".join(DEFAULT_HIDDEN),
        help="隠す /obj のノード名（カンマ区切り）。空文字で何も隠さない",
    )
    ap.add_argument(
        "--default-index", type=int, default=None,
        help="Houdini の既定値が何番目か（0 始まり）。ビューアの初期位置になる",
    )
    ap.add_argument(
        "--show-guides", action="store_true",
        help="拘束線などのガイド表示を消さない（既定は消す）",
    )
    ap.add_argument("--draft", action="store_true", help="低解像度で試し撮りする")
    ap.add_argument("--keep-frames", action="store_true", help="PNG 連番を消さない")
    ap.add_argument(
        "--timeout", type=float, default=config.FLIPBOOK_TIMEOUT_MIN,
        help=f"GUI セッションの時間切れ（分、既定 {config.FLIPBOOK_TIMEOUT_MIN:.0f}）",
    )
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

    hidden = [name.strip() for name in args.hide.split(",") if name.strip()]
    if hidden:
        print(f"隠すノード: {', '.join(hidden)}")

    started = time.time()
    result = launch(
        {
            "hip": str(hip),
            "node": args.node,
            "parm": args.parm,
            "values": values,
            "f1": f1,
            "f2": f2,
            "width": width,
            "height": height,
            "camera": args.camera,
            "visible": visible_pattern(hidden),
            "clean": not args.show_guides,
            "work": str(work),
            "result": str(work / "result.json"),
            "log": str(work / "flipbook.log"),
        },
        job_path=work / "job.json",
        log_path=work / "flipbook.log",
        timeout_s=args.timeout * 60,
    )
    print(f"撮影に {(time.time() - started) / 60:.1f} 分かかりました")

    seg_dirs = [Path(s["dir"]) for s in result["segments"]]
    video = out_dir / f"{args.out}.mp4"
    fps = config.VIDEO_FPS

    frames_per_segment = encode(seg_dirs, video, fps=fps, width=width, height=height)
    verify_keyframes(video, frames_per_segment, len(values), fps)
    print(f"キーフレーム検証: OK ({frames_per_segment} frames/segment)")

    write_meta(
        out_dir / f"{args.out}.json",
        video_name=video.name,
        id_=args.out,
        parm=args.parm,
        label=args.label or args.parm,
        values=values,
        fps=fps,
        frames_per_segment=frames_per_segment,
        width=width,
        height=height,
        default_index=args.default_index,
        camera=args.camera.rsplit("/", 1)[-1],
        node=args.node,
    )

    if not args.keep_frames:
        # ログと job/result は残す。flipbook は GUI 越しなので、後から
        # 何が起きたかを見られるようにしておく価値がある。
        for seg in seg_dirs:
            shutil.rmtree(seg, ignore_errors=True)

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"完了: {video}  ({size_mb:.2f} MB)")
    if args.draft:
        print("draft です。差が出たパラメータだけ --draft を外して撮り直してください。")
    else:
        print(f"記事に  :::compare {args.out}  と書けば埋め込まれます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
