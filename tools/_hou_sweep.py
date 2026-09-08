"""hython プロセス内で実行される撮影本体。

sweep.py から JSON の指示ファイルを受け取り、パラメータを1つずつ振って
PNG 連番を書き出す。

  hython tools/_hou_sweep.py <job.json>

最大の落とし穴は DOP のシミュレーションキャッシュ。パラメータを変えても
キャッシュが残っていると前の結果がそのまま使われ、「全部同じ映像」という
最悪の壊れ方をする（しかもエラーは出ない）。値ごとに必ず破棄する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import hou


def log(msg: str) -> None:
    print(msg, flush=True)


def resolve_parm(node_path: str, parm_name: str) -> hou.Parm:
    node = hou.node(node_path)
    if node is None:
        raise SystemExit(f"ノードが見つかりません: {node_path}")
    parm = node.parm(parm_name)
    if parm is None:
        available = sorted(p.name() for p in node.parms())
        shown = ", ".join(available[:40])
        raise SystemExit(
            f"パラメータが見つかりません: {node_path} の '{parm_name}'\n"
            f"  候補: {shown}{' ...' if len(available) > 40 else ''}"
        )
    return parm


def check_enable_toggle(parm: hou.Parm) -> str | None:
    """振ろうとしているパラメータが、無効化トグルで殺されていないか調べる。

    Houdini には `substeps` に対する `dosubstep` のように、トグルがオフだと
    数値を設定しても無視される構造が多い。これに気づかないと「値を振ったのに
    全部同じ映像」になり、しかもエラーは出ない。
    """
    node = parm.node()
    name = parm.name()
    candidates = [f"do{name}", f"enable{name}", f"use{name}"]
    # substeps -> dosubstep のように末尾の s が落ちる例もある
    if name.endswith("s"):
        candidates.append(f"do{name[:-1]}")

    for cand in candidates:
        toggle = node.parm(cand)
        if toggle is None:
            continue
        try:
            if not toggle.eval():
                return cand
        except hou.OperationFailed:
            continue
    return None


def clear_sim_caches() -> None:
    """シミュレーションのキャッシュを全て破棄する。

    これをやらないと、パラメータを変えても前回の sim 結果が再利用され、
    全ての値で同じ映像が出る。エラーにならないぶん最も気づきにくい。
    """
    hou.hscript("dopcache -c")

    for node in hou.node("/obj").allSubChildren():
        try:
            type_name = node.type().name()
        except hou.ObjectWasDeleted:
            continue

        # DOP Network 本体（Vellum/RBD/Pyro の SOP はこれを内部に持つ）
        if type_name == "dopnet":
            parm = node.parm("resimulate")
            if parm is not None:
                try:
                    parm.pressButton()
                except hou.OperationFailed:
                    pass

        # Vellum Solver / RBD Solver などの SOP が持つ再シミュレーションボタン
        for name in ("resimulate", "resim"):
            parm = node.parm(name)
            if parm is not None:
                try:
                    parm.pressButton()
                except hou.OperationFailed:
                    pass


def check_file_caches() -> list[str]:
    """有効な File Cache SOP を洗い出して返す。

    File Cache が「読み込み」モードだと sim を回さずディスクの内容を
    返してしまい、パラメータを振っても結果が変わらない。
    黙って壊れるより、見つけたら知らせる。
    """
    found = []
    for node in hou.node("/obj").allSubChildren():
        try:
            if node.type().name() not in ("filecache", "filecache::2.0", "rop_geometry"):
                continue
            parm = node.parm("loadfromdisk")
            if parm is not None and parm.eval():
                found.append(node.path())
        except (hou.ObjectWasDeleted, hou.OperationFailed):
            continue
    return found


def render_segment(rop: hou.Node, out_dir: Path, f1: int, f2: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rop.parm("trange").set(1)
    rop.parm("f1").set(f1)
    rop.parm("f2").set(f2)
    rop.parm("f3").set(1)
    rop.parm("picture").set(str(out_dir / "frame.$F4.png"))
    # 先頭フレームに戻してから回す。sim が正しい初期状態から走るようにする。
    hou.setFrame(f1)
    rop.render(verbose=False, output_progress=False)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("使い方: hython _hou_sweep.py <job.json>")

    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    hip = job["hip"]
    node_path = job["node"]
    parm_name = job["parm"]
    values = job["values"]
    f1, f2 = job["f1"], job["f2"]
    width, height = job["width"], job["height"]
    camera = job["camera"]
    rop_path = job.get("rop", "/out/OGL_flipbook")
    work = Path(job["work"])

    log(f"シーンを開いています: {hip}")
    hou.hipFile.load(hip, suppress_save_prompt=True, ignore_load_warnings=True)

    stale = check_file_caches()
    if stale:
        log("警告: 読み込みモードの File Cache があります。")
        log("      パラメータを変えても結果が変わらない可能性があります:")
        for path in stale:
            log(f"        {path}")

    parm = resolve_parm(node_path, parm_name)
    log(f"対象: {node_path} / {parm_name}  （現在値 {parm.eval()}）")

    toggle = check_enable_toggle(parm)
    if toggle:
        raise SystemExit(
            f"'{toggle}' がオフのため、'{parm_name}' を変えても効果がありません。\n"
            f"  シーン側で {node_path} の {toggle} を有効にしてください。\n"
            "  （このまま撮ると全ての値で同じ映像になります）"
        )

    rop = hou.node(rop_path)
    if rop is None:
        raise SystemExit(
            f"OpenGL ROP が見つかりません: {rop_path}\n"
            "  scenes/template.hip から派生したシーンを使ってください。"
        )
    if hou.node(camera) is None:
        raise SystemExit(f"カメラが見つかりません: {camera}")

    rop.parm("camera").set(camera)
    rop.parm("tres").set(True)
    rop.parm("res1").set(width)
    rop.parm("res2").set(height)

    hou.playbar.setFrameRange(f1, f2)
    hou.playbar.setPlaybackRange(f1, f2)

    results = []
    for i, value in enumerate(values):
        seg = work / f"seg{i:03d}"
        log(f"[{i + 1}/{len(values)}] {parm_name} = {value}")

        parm.set(value)
        clear_sim_caches()  # パラメータを変えたら必ず sim を作り直す
        render_segment(rop, seg, f1, f2)

        n = len(list(seg.glob("*.png")))
        log(f"    -> {n} フレーム")
        if n == 0:
            raise SystemExit(
                f"PNG が1枚も出ませんでした: {seg}\n"
                "  カメラの向き、オブジェクトの表示フラグ、ROP の設定を確認してください。"
            )
        results.append({"value": value, "dir": str(seg), "frames": n})

    Path(job["result"]).write_text(
        json.dumps({"segments": results}, indent=2), encoding="utf-8"
    )
    log("撮影完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
