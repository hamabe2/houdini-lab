"""ビューポートそのものを flipbook で撮って比較動画を作る CLI。

  python tools/flipbook.py --hip scenes/vellum_cloth.hip \
      --node /obj/SUBJECT/CONSTRAINTS --parm bendstiffness \
      --values 0,0.1,1,5,10 --frames 1-48 --out vellum-cloth-bend

**複数の動画をまとめて撮るときは --sweep を使う。**Houdini の起動と
シーン読み込みが1回で済む（動画1本ごとに起動し直さない）:

  python tools/flipbook.py --hip scenes/vellum_cloth.hip --frames 1-48 \
      --sweep "/obj/SUBJECT/CONSTRAINTS:niter=5,10,25,50,100;out=vellum-cloth-niter;label=Iterations;default=1" \
      --sweep "/obj/SUBJECT/CONSTRAINTS:veldamping=0,0.1,0.5,1,2;out=vellum-cloth-damp;label=Velocity Damping;default=0"

削れるのは起動時間だけで、sim の時間は変わらない（値ごとに sim を作り直す
必要は消えない）。それでも1本あたり数十秒は効くし、長いバッチを投げて
放っておけるようになる。

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
import re
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

# 既定では何も隠さない（Houdini の flipbook 既定と同じ）。
#
# 床と背景はビューポートが自前で描く。ただしそれらは **alpha 付き** で
# 書き出されるので、encode.py が config.VIDEO_BG に合成しないと絵が壊れる。
#
# なお LIGHT_key を表示するとビューポートがライトをギズモ（線）として描き、
# 対象の手前に重なる。ガイド設定（enableGuide）では消えず visibleObjects から
# 外すしかないので、邪魔なら --hide LIGHT_key を使う。
DEFAULT_HIDDEN: tuple[str, ...] = ()


def visible_pattern(hidden: list[str]) -> str:
    """visibleObjects 用のパターンを作る。'*' から除外を引く。"""
    return " ".join(["*"] + [f"^{name}" for name in hidden if name])


# --- スイープの指定 -----------------------------------------------------------

_SWEEP_HEAD = re.compile(r"\s*([^:]+):([^=]+)=(.+)\s*")
_OUT_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


def parse_sweep(text: str) -> dict:
    """'--sweep' の1件を分解する。

      /obj/SUBJECT/CONSTRAINTS:niter=5,10,25;out=vellum-cloth-niter;label=Iterations;default=1

    先頭は setup_sheet.py の --probe と同じ書式。そこに ';' 区切りで出力側の
    情報（out / label / default）を足したもの。**キー付きにしてあるのは、
    位置で並べると label を書き忘れたときに default が label に入るような
    黙った取り違えが起きるため。**
    """
    parts = [p.strip() for p in text.split(";")]
    m = _SWEEP_HEAD.fullmatch(parts[0])
    if not m:
        raise SystemExit(
            f"--sweep の書式が違います: '{text}'\n"
            "  '/obj/NODE:parm=v1,v2,v3;out=ID;label=表示名;default=2' の形で指定してください"
        )

    sweep = {
        "node": m.group(1).strip(),
        "parm": m.group(2).strip(),
        "values": parse_values(m.group(3)),
        "out": "",
        "label": "",
        "default_index": None,
    }

    for field in parts[1:]:
        if not field:
            continue
        key, sep, value = field.partition("=")
        key, value = key.strip().lower(), value.strip()
        if not sep:
            raise SystemExit(f"--sweep の '{field}' に '=' がありません: '{text}'")
        if key == "out":
            sweep["out"] = value
        elif key == "label":
            sweep["label"] = value
        elif key == "default":
            try:
                sweep["default_index"] = int(value)
            except ValueError:
                raise SystemExit(f"--sweep の default= は整数で指定してください: '{value}'")
        else:
            raise SystemExit(
                f"--sweep に知らないキーがあります: '{key}'\n"
                "  使えるのは out / label / default です"
            )

    if not sweep["out"]:
        raise SystemExit(f"--sweep に out= がありません: '{text}'")
    # out はそのままファイル名（mp4 / json）と作業ディレクトリ名になる。
    if not _OUT_ID.fullmatch(sweep["out"]):
        raise SystemExit(
            f"out= は英小文字・数字・ハイフンだけにしてください: '{sweep['out']}'"
        )

    n = len(sweep["values"])
    idx = sweep["default_index"]
    if idx is not None and not (0 <= idx < n):
        raise SystemExit(
            f"--sweep の default={idx} が範囲外です（{sweep['out']} は {n} 段階）"
        )
    return sweep


# --- ウィンドウを引っ込める ---------------------------------------------------
#
# flipbook にはビューアが要るので GUI 起動そのものは避けられない。しかし画面を
# 占有してフォーカスを奪う必要はない。
#
# **STARTUPINFO.wShowWindow では最小化できない。** あれは「こう表示してほしい」
# という親からのヒントに過ぎず、Houdini は自前で ShowWindow を呼ぶので無視される
# （実測: IsIconic=False のまま前面に出る）。親から明示的に ShowWindow を
# 投げるしかない。ウィンドウが出るまで数秒かかるので、待ちループから繰り返す。


def houdini_pids() -> set[int]:
    """今動いている houdini.exe の PID。

    Houdini は起動時に自分を別プロセスとして起動し直すことがあり、
    Popen が掴んでいる PID とウィンドウの持ち主が一致しない。名前で拾う。
    """
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq houdini.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    pids = set()
    for line in out.stdout.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) > 1:
            try:
                pids.add(int(parts[1].strip('"')))
            except ValueError:
                pass
    return pids


def kill_houdini(before: set[int]) -> int:
    """この呼び出しで起動した houdini.exe を確実に終わらせる。

    **Popen を terminate しても本体が残る。** Houdini は起動時に自分を別
    プロセスとして立て直すので、掴んでいる PID とセッションの持ち主が一致
    しない（`houdini_pids()` のコメントと同じ理由）。

    残ると **Apprentice のライセンスを掴んだままになり、次の起動が失敗する。**
    Ctrl+C で止めたときにここを通らないと、次に回したとき原因の分かりにくい
    失敗になる。
    """
    leftovers = houdini_pids() - before
    for pid in leftovers:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, text=True,
        )
    return len(leftovers)


def minimize_windows(pids: set[int]) -> int:
    """指定 PID のトップレベルウィンドウを最小化し、その数を返す。"""
    if sys.platform != "win32" or not pids:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_MINIMIZE = 6
    count = 0

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        nonlocal count
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            count += 1
        return True

    user32.EnumWindows(visit, 0)
    return count


def launch(
    job: dict,
    job_path: Path,
    log_path: Path,
    timeout_s: float,
    show_window: bool = False,
) -> dict:
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
    # 起動のたびに前面へ出てくるスプラッシュと「Start Here」ページを止める。
    # 撮影中はウィンドウを最小化しているので、これらが出ても操作できない。
    env["HOUDINI_NO_SPLASH"] = "1"
    env["HOUDINI_NO_START_PAGE_SPLASH"] = "1"
    config.CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    # 起動前の PID を控えておく。ここに無い houdini.exe が今回の分。
    # **--show-window でも取る。** 最小化には使わないが、後始末には要る。
    before = houdini_pids()

    where = "" if show_window else "（ウィンドウは最小化します）"
    print(f"houdini.exe を起動します{where}（$HLCACHE = {config.CACHE_ROOT}）")

    # hip は渡さない。フック側で ignore_load_warnings 付きで開く。
    # コマンドラインで開かせると、警告ダイアログが出た時点で誰も操作できず
    # 固まってしまう。
    proc = subprocess.Popen([str(config.HOUDINI_GUI)], env=env)

    try:
        _wait(proc, result_path, log_path, timeout_s, None if show_window else before)
    except KeyboardInterrupt:
        print("\n  中断（Ctrl+C）。Houdini を終わらせます。")
        raise
    finally:
        if proc.poll() is None:
            proc.terminate()
        # **正常終了でもここを通す。** result.json は最後に書かれるので、
        # 見えた時点で PNG は揃っている。残ったセッションを閉じても失うものは無い。
        killed = kill_houdini(before)
        if killed:
            print(f"  houdini.exe を {killed} 個終了しました")

    if not result_path.exists():
        raise SystemExit("撮影結果が出力されませんでした")

    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("error"):
        raise SystemExit(f"Houdini 側で失敗しました: {result['error']}")
    return result


def _wait(
    proc: subprocess.Popen,
    result_path: Path,
    log_path: Path,
    timeout_s: float,
    pids_before: set[int] | None = None,
) -> None:
    """result.json が出るまで待ちつつ、ログの新着行を流す。

    pids_before を渡すと、今回起動した houdini.exe のウィンドウを見つけ次第
    最小化する。ウィンドウは起動から数秒遅れて出るので毎周回試す。
    """
    deadline = time.time() + timeout_s
    shown = 0
    exited_at = None
    tucked = 0

    while True:
        shown = _drain_log(log_path, shown)

        if pids_before is not None:
            new_pids = houdini_pids() - pids_before
            if new_pids:
                tucked += minimize_windows(new_pids)

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
    ap.add_argument("--node", help="対象ノードのパス")
    ap.add_argument("--parm", help="振るパラメータ名")
    ap.add_argument("--values", help="カンマ区切りの値 (例 1e5,1e6,1e7)")
    ap.add_argument("--frames", default="1-48", help="フレーム範囲 (既定 1-48)")
    ap.add_argument("--out", help="出力 ID (例 vellum-cloth-bend)")
    ap.add_argument("--label", default="", help="表示ラベル (既定はパラメータ名)")
    ap.add_argument(
        "--sweep", action="append", default=[],
        metavar="NODE:PARM=v1,v2;out=ID;label=名前;default=N",
        help="動画1本ぶんの指定。複数回書くと1回の起動でまとめて撮る",
    )
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument(
        "--hide", default=",".join(DEFAULT_HIDDEN),
        help="隠す /obj のノード名（カンマ区切り）。既定は何も隠さない",
    )
    ap.add_argument(
        "--default-index", type=int, default=None,
        help="Houdini の既定値が何番目か（0 始まり）。ビューアの初期位置になる",
    )
    ap.add_argument(
        "--aa", type=int, default=8, choices=(1, 2, 4, 8, 16, 32),
        help="アンチエイリアスのサンプル数（既定 8）",
    )
    ap.add_argument(
        "--shading", default="SmoothWire",
        choices=("SmoothWire", "Smooth", "FlatWire", "Flat", "Wire"),
        help="ビューポートのシェーディングモード（既定 SmoothWire）",
    )
    ap.add_argument(
        "--scheme", default=config.VIEWPORT_SCHEME,
        choices=("keep", "Grey", "DarkGrey", "Dark", "Light"),
        help=f"床グリッドの色とエッジの縁（既定 {config.VIEWPORT_SCHEME}）。"
             "背景の面の色は VIDEO_BG が決める",
    )
    ap.add_argument(
        "--lighting", default="Headlight",
        choices=("Off", "Headlight", "Normal", "HighQuality", "HighQualityWithShadows"),
        help="ライティングモード。work light を使うには Headlight が必要（既定）",
    )
    ap.add_argument(
        "--work-light", default="Headlight",
        choices=("Headlight", "ThreePoint", "Domelight", "PhysicalSky"),
        help="ビューポートの work light（既定 Headlight）",
    )
    ap.add_argument(
        "--show-guides", action="store_true",
        help="拘束線などのガイド表示を消さない（既定は消す）",
    )
    ap.add_argument(
        "--show-window", action="store_true",
        help="Houdini のウィンドウを表示する（既定は最小化。デバッグ用）",
    )
    ap.add_argument("--draft", action="store_true", help="低解像度で試し撮りする")
    ap.add_argument("--keep-frames", action="store_true", help="PNG 連番を消さない")
    ap.add_argument(
        "--timeout", type=float, default=config.FLIPBOOK_TIMEOUT_MIN,
        help=f"GUI セッションの時間切れ（分、既定 {config.FLIPBOOK_TIMEOUT_MIN:.0f}）",
    )
    ap.add_argument(
        "--report", metavar="PATH",
        help="どの out が撮れたかを JSON で書き出す（呼び出し側が結果を読むため）",
    )
    args = ap.parse_args()

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンファイルがありません: {hip}")

    sweeps = collect_sweeps(args)
    f1, f2 = parse_frames(args.frames)

    if args.draft:
        for sweep in sweeps:
            sweep["values"] = thin_values(sweep["values"], config.DRAFT_STEPS)
            # 段階を間引いたので、既定値が何番目かの指定は当てにならなくなる。
            sweep["default_index"] = None
        f2 = min(f2, f1 + config.DRAFT_FRAMES - 1)
        width, height = config.DRAFT_WIDTH, config.DRAFT_HEIGHT
        out_dir = config.CACHE_DIR / "draft"
        steps = "/".join(str(len(s["values"])) for s in sweeps)
        print(f"draft モード: {steps} 段階 x {f2 - f1 + 1} フレーム / {width}x{height}")
    else:
        width, height = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
        out_dir = config.MEDIA_DIR

    check_disk_space()

    # 1回の起動でまとめて撮るときは、job / result / ログの置き場が動画1本と
    # 1対1にならない。セッション用のディレクトリを別に作る。
    session = sweeps[0]["out"] if len(sweeps) == 1 else "_batch"
    work = config.CACHE_DIR / "shots" / session
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "flipbook.log"

    hidden = [name.strip() for name in args.hide.split(",") if name.strip()]
    if hidden:
        print(f"隠すノード: {', '.join(hidden)}")

    if len(sweeps) > 1:
        print(f"{len(sweeps)} 本を1回の起動で撮ります:")
        for sweep in sweeps:
            vals = ", ".join(str(v) for v in sweep["values"])
            print(f"  {sweep['out']}: {sweep['node']} / {sweep['parm']} = {vals}")

    started = time.time()
    result = launch(
        {
            "hip": str(hip),
            "sweeps": sweeps,
            "f1": f1,
            "f2": f2,
            "width": width,
            "height": height,
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
            "log": str(log_path),
        },
        job_path=work / "job.json",
        log_path=log_path,
        timeout_s=args.timeout * 60,
        show_window=args.show_window,
    )
    print(f"撮影に {(time.time() - started) / 60:.1f} 分かかりました")

    # 失敗したスイープがあっても、撮れた分は動画にする。1本の失敗で
    # 数十分かけて撮った他の PNG を捨てるのは惜しい。
    shot = {item["out"]: item for item in result["items"]}
    failed = []
    done = []
    for sweep in sweeps:
        item = shot.get(sweep["out"])
        if item is None or item.get("error"):
            reason = "撮影されませんでした" if item is None else item["error"]
            failed.append((sweep["out"], reason))
            continue
        # **mp4 にするところで落ちても、他の本は出す。** 撮影と同じ理屈で、
        # 1本の encode 失敗のために数十分かけて撮った PNG を捨てるのは惜しい。
        try:
            encode_sweep(sweep, item, out_dir, width, height, args)
        except Exception as exc:
            print(f"  {sweep['out']}: mp4 にできませんでした: {exc}")
            failed.append((sweep["out"], f"{type(exc).__name__}: {exc}"))
            continue
        done.append(sweep["out"])

    if args.report:
        # **呼び出し側は stdout を読まずに済ませる。** shoot.py はこの結果を
        # 見て検証リストを shot に進めるので、成否を取り違えると「撮ってある
        # のに撮り直す」「撮れていないのに撮影済みになる」ことになる。
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(
                {
                    "draft": bool(args.draft),
                    "out_dir": str(out_dir),
                    "ok": done,
                    "failed": [{"out": o, "reason": r} for o, r in failed],
                },
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    if failed:
        print()
        print(f"失敗した {len(failed)} 本:")
        for out, reason in failed:
            print(f"  {out}: {reason}")
        print(f"  ログ: {log_path}")
        return 1

    if args.draft:
        print("draft です。差が出たパラメータだけ --draft を外して撮り直してください。")
    return 0


def collect_sweeps(args: argparse.Namespace) -> list[dict]:
    """--sweep か、単体の --node/--parm/--values/--out から撮る対象を組み立てる。"""
    single = (args.node, args.parm, args.values, args.out)
    if args.sweep and any(single):
        raise SystemExit("--sweep と --node/--parm/--values/--out は混ぜられません")

    if args.sweep:
        sweeps = [parse_sweep(text) for text in args.sweep]
        outs = [s["out"] for s in sweeps]
        dupes = sorted({o for o in outs if outs.count(o) > 1})
        if dupes:
            raise SystemExit(f"out= が重複しています: {', '.join(dupes)}")
        return sweeps

    missing = [
        name for name, value in
        zip(("--node", "--parm", "--values", "--out"), single) if not value
    ]
    if missing:
        raise SystemExit(
            f"{', '.join(missing)} がありません。\n"
            "  1本だけなら --node/--parm/--values/--out、"
            "複数本なら --sweep を使ってください。"
        )
    return [{
        "node": args.node,
        "parm": args.parm,
        "values": parse_values(args.values),
        "out": args.out,
        "label": args.label,
        "default_index": args.default_index,
    }]


def encode_sweep(
    sweep: dict, item: dict, out_dir: Path, width: int, height: int,
    args: argparse.Namespace,
) -> None:
    """撮れた PNG 連番1本ぶんを mp4 + JSON にする。"""
    seg_dirs = [Path(s["dir"]) for s in item["segments"]]
    video = out_dir / f"{sweep['out']}.mp4"
    fps = config.VIDEO_FPS

    frames_per_segment = encode(seg_dirs, video, fps=fps, width=width, height=height)
    verify_keyframes(video, frames_per_segment, len(sweep["values"]), fps)
    print(f"キーフレーム検証: OK ({frames_per_segment} frames/segment)")

    # サイトに出すのは Houdini の入力欄の値ではなく、隣の「× 10^N」を
    # 掛けた後の実効値。Houdini 側が計算して返してくる。
    display = item.get("display_values") or sweep["values"]
    multiplier = item.get("multiplier") or None
    if multiplier:
        print(f"  表記は実効値: {display}  （{multiplier['source']}）")

    write_meta(
        out_dir / f"{sweep['out']}.json",
        video_name=video.name,
        id_=sweep["out"],
        parm=sweep["parm"],
        label=sweep["label"] or sweep["parm"],
        values=display,
        raw_values=sweep["values"],
        multiplier=multiplier,
        fps=fps,
        frames_per_segment=frames_per_segment,
        width=width,
        height=height,
        default_index=sweep["default_index"],
        camera=args.camera.rsplit("/", 1)[-1],
        node=sweep["node"],
    )

    if not args.keep_frames:
        # ログと job/result は残す。flipbook は GUI 越しなので、後から
        # 何が起きたかを見られるようにしておく価値がある。
        for seg in seg_dirs:
            shutil.rmtree(seg, ignore_errors=True)

    size_mb = video.stat().st_size / (1024 * 1024)
    print(f"完了: {video}  ({size_mb:.2f} MB)")
    if not args.draft:
        print(f"  記事に  :::compare {sweep['out']}  と書けば埋め込まれます。")


if __name__ == "__main__":
    raise SystemExit(main())
