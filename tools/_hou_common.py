"""hython / GUI Houdini の両方から使う共通処理。

OpenGL ROP 経路（_hou_sweep.py）と flipbook 経路（_hou_flipbook.py）は
「絵の撮り方」だけが違い、その手前のパラメータ解決とキャッシュ破棄は
同じでなければならない。特に clear_sim_caches() は、片方にしか入って
いないと「パラメータを振ったのに全部同じ映像」という無言の破損を
片方の経路だけで再発させることになる。だから1か所に置く。
"""

from __future__ import annotations

import hou


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
