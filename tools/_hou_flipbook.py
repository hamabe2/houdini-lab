"""GUI の Houdini セッション内で flipbook を撮る本体。

`scripts/456.py` フックから呼ばれる。hython には flipbook が無い
（`hou.SceneViewer.flipbook()` はビューアを要求する）ため、この経路だけは
houdini.exe を GUI で起動して、その中から自分自身を駆動する。

OpenGL ROP 経路（_hou_sweep.py）との違いは「絵の出どころ」だけ:

  OpenGL ROP : ビューポートの見た目をジオメトリで模倣したものを ROP で撮る
  flipbook   : ビューポートそのものを撮る（グリッド・背景・シェーディングが実物）

床・背景・シェーディングはすべてビューポートのものをそのまま使う。
ただし**何も指定しないとその機械の表示設定が出力に出る**ので、
アンチエイリアスとシェーディングモードは setup_quality() で明示的に固定する。

## この経路特有の難しさ

- **stdout が消える。** houdini.exe は GUI サブシステムのアプリで、
  Windows では print が親のコンソールに届かない。だから進捗は必ず
  ログファイルに書く。親（flipbook.py）はそれを読んで表示する。
- **例外で黙って固まる。** 失敗しても GUI は生き続けるので、親からは
  「終わらない」としか見えない。何があっても result.json を書き、
  必ず hou.exit() まで到達させる。
- **456.py は hip を読むたびに走る。** 起動直後の空シーンでも走り、
  こちらが hipFile.load() したときにも走る。STARTED で再入を止める。
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import hou

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hou_common import (  # noqa: E402
    check_enable_toggle,
    check_file_caches,
    clear_sim_caches,
    resolve_parm,
)

# 456.py はモジュールを import するだけなので、この値はセッション内で生き続ける。
# 456.py 自体は exec され直すためグローバルが残らない。再入判定はここに置く。
STARTED = False

# ビューアが出来上がる前に叩くと取得できない。イベントループを数周待つ。
_VIEWER_WAIT_TICKS = 200

_log_file: Path | None = None
_job_path: Path | None = None
_ticks = 0


def log(msg: str) -> None:
    """ログファイルに追記する。GUI の stdout は親に届かないため。"""
    print(msg, flush=True)
    if _log_file is None:
        return
    try:
        with _log_file.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def kick(job_path: str) -> None:
    """456.py から呼ばれる入口。実処理はイベントループに預ける。

    456.py はシーン読み込みの最中に実行されるので、この時点では
    ビューアもデスクトップも完成していない。1周待ってから動き出す。
    """
    global STARTED, _job_path, _log_file
    if STARTED:
        return
    STARTED = True

    _job_path = Path(job_path)
    job = json.loads(_job_path.read_text(encoding="utf-8"))
    _log_file = Path(job["log"])
    _log_file.parent.mkdir(parents=True, exist_ok=True)
    _log_file.write_text("", encoding="utf-8")

    log(f"GUI セッション開始: Houdini {hou.applicationVersionString()}")
    hou.ui.addEventLoopCallback(_tick)


def _tick() -> None:
    global _ticks
    _ticks += 1

    viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if viewer is None:
        if _ticks < _VIEWER_WAIT_TICKS:
            return
        _finish(error="Scene Viewer が見つかりません（デスクトップの構成を確認してください）")
        return

    hou.ui.removeEventLoopCallback(_tick)

    try:
        job = json.loads(_job_path.read_text(encoding="utf-8"))
        segments = _run(job, viewer)
    except Exception as exc:  # noqa: BLE001 - 何が来ても result.json を残す
        log(traceback.format_exc())
        _finish(error=f"{type(exc).__name__}: {exc}")
        return

    _finish(segments=segments)


def _finish(segments: list[dict] | None = None, error: str = "") -> None:
    """result.json を書いて Houdini を終了する。

    親はプロセスの終了コードではなく、このファイルの有無と中身で判断する。
    GUI アプリの終了コードは当てにならない。
    """
    payload: dict = {"segments": segments or []}
    if error:
        payload["error"] = error
        log(f"エラー: {error}")
    else:
        log("撮影完了")

    try:
        job = json.loads(_job_path.read_text(encoding="utf-8"))
        Path(job["result"]).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())

    hou.exit(suppress_save_prompt=True)


# --- flipbook 設定 ----------------------------------------------------------


def _apply(settings: hou.FlipbookSettings, name: str, *args) -> bool:
    """FlipbookSettings のセッターを、無い場合は飛ばして適用する。

    必要な API が 21.0.729 に揃っていることは確認済みだが、ここで落ちると
    GUI セッションごと道連れになる。無い項目は警告に留める。
    """
    fn = getattr(settings, name, None)
    if fn is None:
        log(f"  警告: FlipbookSettings に {name}() がありません（無視します）")
        return False
    try:
        fn(*args)
    except hou.Error as exc:
        log(f"  警告: {name}({args}) に失敗しました: {exc}")
        return False
    return True


def _build_settings(
    viewer: hou.SceneViewer, job: dict, out_pattern: str, f1: int, f2: int
) -> hou.FlipbookSettings:
    settings = viewer.flipbookSettings().stash()

    _apply(settings, "output", out_pattern)
    # MPlay に出されるとディスクに何も残らない。output を入れていても明示する。
    _apply(settings, "outputToMPlay", False)
    _apply(settings, "frameRange", (f1, f2))
    _apply(settings, "frameIncrement", 1)
    _apply(settings, "useResolution", True)
    _apply(settings, "resolution", (job["width"], job["height"]))
    # ハンドル・ガイド・HUD を落として、絵だけにする。
    # beautyPassOnly を True にすると背景そのものが描かれず真っ黒になる
    # （実測確認済み）。ガイド類は clean_viewport() の enableGuide で individually
    # 落としているので、ここは False にして背景を残す。
    _apply(settings, "beautyPassOnly", False)
    # flipbook 自身の AA。ビューポートの sceneAntialias とは別物で、
    # 受け取るのは int ではなく hou.flipbookAntialias の enum
    # （Off / Fast / Good / HighQuality / UseViewportSetting）。
    # 撮影は1回きりなので常に最高品質でよい。速度を落としたければ
    # --aa でビューポート側のサンプル数を下げる。
    log(f"  flipbook antialias: {settings.antialias()} -> HighQuality")
    _apply(settings, "antialias", hou.flipbookAntialias.HighQuality)
    _apply(settings, "visibleObjects", job["visible"])
    # sim を毎回先頭から作り直させる。clear_sim_caches() と二重の保険。
    _apply(settings, "initializeSimulations", True)
    _apply(settings, "useMotionBlur", False)
    return settings


# --- ビューポートの掃除 ------------------------------------------------------

# ビューポートをそのまま撮ると、ジオメトリ以外の描き込みも一緒に写る。
# 名前ラベル・ハンドル・原点ノモン・SOP のガイド類は、比較の絵には要らない。
#
# 構築プレーン（XZPlane など）は切らない。床のグリッドはこの経路の主目的で、
# OpenGL ROP 側が GROUND ジオメトリで模倣していたものの実物にあたる。
#
# **ライトとカメラのギズモはここでは消えない。** 実測で確認済み。
# あれは visibleObjects から外すしかないので、flipbook.py の DEFAULT_HIDDEN が
# 担当する。ここに足しても効かないので探し回らないこと。
_NOISY_GUIDES = (
    "NodeGuides", "NodeHandles", "DisplayNodes", "ObjectNames", "ObjectPaths",
    "ObjectSelection", "FillSelections", "FollowSelection", "SelectableTemplates",
    "TemplateGeometry", "FieldGuide", "GroupList", "IKCriticalZone",
    "OriginGnomon", "FloatingGnomon", "ParticleGnomon", "ViewPivot",
    "SafeArea", "CameraMask", "ShowDrawTime",
)


# シェーディングモードを適用するディスプレイセット。どれに入るかは
# ビューアがオブジェクトレベルかSOPレベルかで変わるので、全部に入れる。
_DISPLAY_SETS = (
    "DisplayModel", "CurrentModel", "SceneObject", "SelectedObject", "TemplateModel",
)


def setup_quality(
    viewport: hou.GeometryViewport, aa: int, shading: str, scheme: str
) -> None:
    """アンチエイリアス・シェーディング・カラースキームを明示する。

    設定しないと、その場の Houdini の表示設定（デスクトップやユーザー設定）が
    そのまま出力に出る。同じコマンドを叩いても機械によって絵が変わるので、
    比較用の素材としては困る。ここで固定する。
    """
    import os
    if os.environ.get("HL_HDR_OFF"):
        viewport.settings().setHdrRendering(False)
        log("  PROBE hdrRendering = False")
    if os.environ.get("HL_LUT_ON"):
        viewport.settings().setUseSceneLUT(True)
        log("  PROBE useSceneLUT = True")

    settings = viewport.settings()
    before = settings.sceneAntialias()
    settings.setSceneAntialias(aa)
    log(f"  scene antialias: {before} -> {settings.sceneAntialias()}")

    # カラースキームが決めるのは **床グリッドの色と濃さ** で、背景色ではない。
    # ビューポートの背景は alpha=0 で書き出されるため出力に届かず、背景色は
    # encode.py が合成する config.VIDEO_BG だけで決まる。実測:
    #
    #   Dark  -> グリッド RGB(197,238,255) alpha 25（青みがかって淡い）
    #   Light -> グリッド RGB(255,255,255) alpha 40（純白で濃い）
    #
    # 固定しないと機械ごとにグリッドの濃さが変わる。"keep" は触らない。
    if scheme == "keep":
        log(f"  color scheme: {settings.colorScheme()} のまま")
    else:
        color = getattr(hou.viewportColorScheme, scheme, None)
        if color is None:
            log(f"  警告: hou.viewportColorScheme.{scheme} がありません（既定のまま）")
        else:
            log(f"  color scheme: {settings.colorScheme()} -> {scheme}")
            settings.setColorScheme(color)

    # smoothwire はスムースシェーディング + ワイヤーフレーム。面だけだと変形が
    # 読み取りにくいが、トポロジが見えると点がどう動いているかが分かる。
    mode = getattr(hou.glShadingType, shading, None)
    if mode is None:
        log(f"  警告: hou.glShadingType.{shading} がありません（既定のまま）")
        return
    applied = []
    for name in _DISPLAY_SETS:
        kind = getattr(hou.displaySetType, name, None)
        if kind is None:
            continue
        try:
            settings.displaySet(kind).setShadedMode(mode)
            applied.append(name)
        except hou.Error as exc:
            log(f"  警告: {name} に {shading} を適用できません: {exc}")
    log(f"  shading: {shading} -> {', '.join(applied) or '(適用先なし)'}")


def clean_viewport(viewport: hou.GeometryViewport) -> None:
    settings = viewport.settings()
    for name in _NOISY_GUIDES:
        guide = getattr(hou.viewportGuide, name, None)
        if guide is None:
            log(f"  警告: hou.viewportGuide.{name} がありません（無視します）")
            continue
        try:
            settings.enableGuide(guide, False)
        except hou.Error as exc:
            log(f"  警告: ガイド {name} を消せませんでした: {exc}")


# --- 本体 -------------------------------------------------------------------


def _run(job: dict, viewer: hou.SceneViewer) -> list[dict]:
    hip = job["hip"]
    f1, f2 = job["f1"], job["f2"]
    camera = job["camera"]
    work = Path(job["work"])

    log(f"シーンを開いています: {hip}")
    hou.hipFile.load(hip, suppress_save_prompt=True, ignore_load_warnings=True)

    stale = check_file_caches()
    if stale:
        log("警告: 読み込みモードの File Cache があります。")
        log("      パラメータを変えても結果が変わらない可能性があります:")
        for path in stale:
            log(f"        {path}")

    parm = resolve_parm(job["node"], job["parm"])
    log(f"対象: {job['node']} / {job['parm']}  （現在値 {parm.eval()}）")

    toggle = check_enable_toggle(parm)
    if toggle:
        raise SystemExit(
            f"'{toggle}' がオフのため、'{job['parm']}' を変えても効果がありません。\n"
            f"  シーン側で {job['node']} の {toggle} を有効にしてください。\n"
            "  （このまま撮ると全ての値で同じ映像になります）"
        )

    cam = hou.node(camera)
    if cam is None:
        raise SystemExit(f"カメラが見つかりません: {camera}")

    viewport = viewer.curViewport()
    viewport.setCamera(cam)
    log(f"ビューポートのカメラ: {camera}")

    setup_quality(viewport, job["aa"], job["shading"], job["scheme"])

    if job.get("clean", True):
        clean_viewport(viewport)
        log("ガイド類を消しました（--show-guides で残せます）")

    hou.playbar.setFrameRange(f1, f2)
    hou.playbar.setPlaybackRange(f1, f2)

    values = job["values"]
    results = []
    for i, value in enumerate(values):
        seg = work / f"seg{i:03d}"
        seg.mkdir(parents=True, exist_ok=True)
        log(f"[{i + 1}/{len(values)}] {job['parm']} = {value}")

        parm.set(value)
        clear_sim_caches()  # パラメータを変えたら必ず sim を作り直す

        settings = _build_settings(viewer, job, str(seg / "frame.$F4.png"), f1, f2)
        hou.setFrame(f1)
        viewer.flipbook(viewport, settings, open_dialog=False)

        n = len(list(seg.glob("*.png")))
        log(f"    -> {n} フレーム")
        if n == 0:
            raise SystemExit(
                f"PNG が1枚も出ませんでした: {seg}\n"
                "  カメラの向き、オブジェクトの表示フラグ、--visible を確認してください。"
            )
        results.append({"value": value, "dir": str(seg), "frames": n})

    return results
