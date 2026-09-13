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
    check_disabled,
    check_file_caches,
    clear_sim_caches,
    effective_values,
    geometry_report,
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
        items = _run(job, viewer)
    # **BaseException で受ける。** ここのコードは失敗を `raise SystemExit` で
    # 伝えるが、SystemExit は Exception のサブクラスではない。`except Exception`
    # だとすり抜けて result.json が書かれず、親からは「終わらない」としか
    # 見えなくなる（--timeout で打ち切られるまで待たされる）。
    except BaseException as exc:  # noqa: BLE001 - 何が来ても result.json を残す
        log(traceback.format_exc())
        _finish(error=f"{type(exc).__name__}: {exc}")
        return

    _finish(items=items)


def _finish(items: list[dict] | None = None, error: str = "") -> None:
    """result.json を書いて Houdini を終了する。

    親はプロセスの終了コードではなく、このファイルの有無と中身で判断する。
    GUI アプリの終了コードは当てにならない。

    items の中身はモードで変わる（sheet は1枚ずつのセル、通常は動画1本ぶん）。
    """
    payload: dict = {"items": items or []}
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


def apply_look(viewport: hou.GeometryViewport, look: dict) -> None:
    """ビューポートの照明を設定する。

    **ライトが0灯のとき、モード（Headlight / Normal / HighQuality /
    HighQualityWithShadows）は出力に一切効かない。** 実測で4モードの
    出力がバイト単位で一致した。Houdini は 0灯なら同じヘッドライトに
    落とすらしく、Normal を入れても読み返すと HighQuality に戻る。

    絵を変えられるのはヘッドライトそのものの設定のほう:

      direction  カメラ軸から振ると陰影に勾配が出る（同軸だと平板になる）
      specular   ハイライト
      ao         折り目や接触部が締まる
    """
    settings = viewport.settings()

    # **work light を使うには lighting を Headlight にする必要がある。**
    # setWorkLightType の docstring に明記されている
    # （"Does not change the lighting mode to Headlight; this must be done
    # separately."）。Normal / HighQuality のままだと work light は無視される。
    work_light = look.get("work_light")
    if work_light:
        value = getattr(hou.viewportWorkLight, work_light, None)
        if value is None:
            log(f"  警告: hou.viewportWorkLight.{work_light} がありません")
        else:
            settings.setWorkLightType(value)
            log(f"  work light: {settings.workLightType()}")

    mode = look.get("lighting")
    if mode:
        value = getattr(hou.viewportLighting, mode, None)
        if value is None:
            log(f"  警告: hou.viewportLighting.{mode} がありません（既定のまま）")
        else:
            settings.setLighting(value)
            log(f"  lighting: {mode} -> 実際は {settings.lighting()}")

    direction = look.get("headlight_dir")
    if direction:
        settings.setHeadlightDirection(tuple(direction))
        log(f"  headlight direction: {settings.headlightDirection()}")

    if "specular" in look:
        settings.setHeadlightSpecular(bool(look["specular"]))
        log(f"  headlight specular: {settings.headlightSpecular()}")

    if "intensity" in look:
        settings.setHeadlightIntensity(float(look["intensity"]))
        log(f"  headlight intensity: {settings.headlightIntensity()}")

    if "ao" in look:
        settings.setAmbientOcclusion(bool(look["ao"]))
        log(f"  ambient occlusion: {settings.ambientOcclusion()}")


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


def _open_and_setup(job: dict, viewer: hou.SceneViewer) -> hou.GeometryViewport:
    """シーンを開き、ビューポートを撮影できる状態にする（両モード共通）。"""
    hip = job["hip"]
    camera = job["camera"]

    log(f"シーンを開いています: {hip}")
    hou.hipFile.load(hip, suppress_save_prompt=True, ignore_load_warnings=True)

    stale = check_file_caches()
    if stale:
        log("警告: 読み込みモードの File Cache があります。")
        log("      パラメータを変えても結果が変わらない可能性があります:")
        for path in stale:
            log(f"        {path}")

    cam = hou.node(camera)
    if cam is None:
        raise SystemExit(f"カメラが見つかりません: {camera}")

    viewport = viewer.curViewport()
    viewport.setCamera(cam)
    log(f"ビューポートのカメラ: {camera}")

    setup_quality(viewport, job["aa"], job["shading"], job["scheme"])

    # 照明もここで固定する。シーンにライトは置いておらず、絵の明暗は
    # ビューポートの work light だけで決まる（＝固定しないと機械ごとに変わる）。
    apply_look(viewport, {
        "lighting": job.get("lighting"),
        "work_light": job.get("work_light"),
    })

    if job.get("clean", True):
        clean_viewport(viewport)
        log("ガイド類を消しました（--show-guides で残せます）")

    return viewport


def _prepare_parm(node_path: str, parm_name: str) -> hou.Parm:
    parm = resolve_parm(node_path, parm_name)
    log(f"対象: {node_path} / {parm_name}  （現在値 {parm.eval()}）")

    reason = check_disabled(parm)
    if reason:
        raise SystemExit(
            f"'{parm_name}' を変えても効果がありません: {reason}\n"
            f"  シーン側で {node_path} の設定を見直してください。\n"
            "  （このまま撮ると全ての値で同じ映像になります）"
        )
    return parm


def _run_sheet(job: dict, viewer: hou.SceneViewer) -> list[dict]:
    """複数の別パラメータを1枚ずつ撮る（セットアップ確認用）。

    sim を全部回す前に「そもそも見た目に差が出るのか」「画角と見せ方は
    これでいいのか」を判断するためのもの。**パラメータごとに元の値へ
    戻す**のが肝で、戻さないと前のパラメータの影響が残り、何を見ているのか
    分からない絵になる。
    """
    viewport = _open_and_setup(job, viewer)
    frame = job["frame"]
    work = Path(job["work"])

    hou.playbar.setFrameRange(1, frame)
    hou.playbar.setPlaybackRange(1, frame)

    cells = []
    total = sum(len(p["values"]) for p in job["probes"])
    done = 0

    for probe in job["probes"]:
        parm = _prepare_parm(probe["node"], probe["parm"])
        original = parm.eval()
        # 「× 10^N」メニューが隣にあるなら、表記に使うのは掛けた後の値。
        # propose_values.py が実効値で刻みを提案するのに要る。
        _, mult = effective_values(parm, probe["values"])

        for value in probe["values"]:
            done += 1
            cell = work / f"cell{done:03d}"
            cell.mkdir(parents=True, exist_ok=True)
            log(f"[{done}/{total}] {probe['parm']} = {value}")

            parm.set(value)
            clear_sim_caches()

            settings = _build_settings(viewer, job, str(cell / "img.$F4.png"), frame, frame)
            # 先頭に戻してから目的のフレームへ。sim を初期状態から走らせる。
            hou.setFrame(1)
            hou.setFrame(frame)
            viewer.flipbook(viewport, settings, open_dialog=False)

            pngs = sorted(cell.glob("*.png"))
            if not pngs:
                raise SystemExit(f"PNG が出ませんでした: {cell}")
            # 絵と一緒に、sim が壊れていないかの数値も残す。
            report = geometry_report(job.get("geo", "/obj/SUBJECT/OUT"))
            if report.get("nan_P") or report.get("nan_v"):
                log(f"    警告: NaN/inf があります {report}")

            cells.append({
                "node": probe["node"],
                "parm": probe["parm"],
                "value": value,
                "default": original,
                "path": str(pngs[0]),
                "geo": report,
                "multiplier": mult,
            })

        # 次のパラメータを単独で見るため、必ず元の値へ戻す
        parm.set(original)
        log(f"    {probe['parm']} を既定値 {original} に戻しました")

    return cells


def _shoot_sweep(
    job: dict, viewer: hou.SceneViewer, viewport: hou.GeometryViewport,
    sweep: dict, parm: hou.Parm,
) -> list[dict]:
    """1本ぶん（＝動画1本ぶん）の全段階を撮る。"""
    f1, f2 = job["f1"], job["f2"]
    work = Path(job["work"]) / sweep["out"]
    values = sweep["values"]

    results = []
    for i, value in enumerate(values):
        seg = work / f"seg{i:03d}"
        seg.mkdir(parents=True, exist_ok=True)
        log(f"[{i + 1}/{len(values)}] {sweep['parm']} = {value}")

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

        # 最終フレームの状態を数値で残す。爆発や NaN を絵に頼らず検出する。
        report = geometry_report(job.get("geo", "/obj/SUBJECT/OUT"))
        if report.get("nan_P") or report.get("nan_v"):
            log(f"    警告: NaN/inf があります {report}")
        if report.get("speed_max"):
            log(f"    速度 max {report['speed_max']} / p95 {report['speed_p95']}")

        results.append({"value": value, "dir": str(seg), "frames": n, "geo": report})

    return results


def _run_stills(job: dict, viewer: hou.SceneViewer) -> list[dict]:
    """パラメータを触らず、指定フレームを1枚ずつ撮る（見た目の確認用）。

    **確認する絵は、実際に出す絵と同じ経路で作らなければ意味がない。**
    OpenGL ROP 経路には床のグリッドも背景も無いので、あちらで画角や
    見え方を判断すると、本番と別物を見て決めることになる。
    """
    viewport = _open_and_setup(job, viewer)
    work = Path(job["work"])
    frames = job["frames"]
    # 照明の候補を複数渡すと、同じフレームを各案で撮って見比べられる。
    looks = job.get("looks") or [{}]

    shots = []
    n = 0
    for look in looks:
        name = look.get("name", "")
        if look:
            apply_look(viewport, look)
        for frame in frames:
            n += 1
            cell = work / f"still{n:03d}"
            cell.mkdir(parents=True, exist_ok=True)
            log(f"[{n}/{len(looks) * len(frames)}] frame {frame}"
                + (f" / look {name}" if name else ""))

            settings = _build_settings(viewer, job, str(cell / "img.$F4.png"), frame, frame)
            # 先頭から目的のフレームへ。sim を初期状態から走らせる。
            hou.setFrame(1)
            hou.setFrame(frame)
            viewer.flipbook(viewport, settings, open_dialog=False)

            pngs = sorted(cell.glob("*.png"))
            if not pngs:
                raise SystemExit(f"PNG が出ませんでした: {cell}")
            shots.append({"frame": frame, "look": name, "path": str(pngs[0])})

    return shots


def _run(job: dict, viewer: hou.SceneViewer) -> list[dict]:
    """全スイープを1つのセッションで撮る。

    シーンを開き直さないのがこのモードの目的。動画1本ごとに Houdini を
    起動し直すと、その都度 起動 + hip 読み込みを払うことになる。
    """
    mode = job.get("mode")
    if mode == "sheet":
        return _run_sheet(job, viewer)
    if mode == "stills":
        return _run_stills(job, viewer)

    viewport = _open_and_setup(job, viewer)
    sweeps = job["sweeps"]

    # **1枚も撮る前に、全スイープの対象を解決しておく。** 1本目を数十分かけて
    # 撮ってから3本目のパラメータ名の打ち間違いに気づくのは目も当てられない。
    parms = [_prepare_parm(s["node"], s["parm"]) for s in sweeps]

    hou.playbar.setPlaybackRange(job["f1"], job["f2"])

    items = []
    for i, (sweep, parm) in enumerate(zip(sweeps, parms)):
        log(f"=== [{i + 1}/{len(sweeps)}] {sweep['out']} ===")
        original = parm.eval()
        try:
            segments = _shoot_sweep(job, viewer, viewport, sweep, parm)
        except BaseException as exc:  # noqa: BLE001 - SystemExit も拾う（_tick 参照）
            # 1本の失敗で残りを捨てない。スイープどうしは独立している。
            log(traceback.format_exc())
            items.append({"out": sweep["out"], "error": f"{type(exc).__name__}: {exc}"})
        else:
            # 「× 10^N」メニューが隣にあるなら、サイトに出すのは掛けた後の値。
            display, note = effective_values(parm, sweep["values"])
            if note:
                log(f"    表記は実効値にします（{note['source']}）: {display}")
            items.append({
                "out": sweep["out"],
                "segments": segments,
                "display_values": display,
                "multiplier": note,
            })
        finally:
            # 次のスイープを単独で見るため、必ず元の値へ戻す（_run_sheet と同じ理由）
            parm.set(original)
            log(f"    {sweep['parm']} を既定値 {original} に戻しました")

    return items
