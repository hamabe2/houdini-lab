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


def find_enable_toggle(parm: hou.Parm) -> tuple[str, bool] | None:
    """同じノードにある「いかにも有効化トグルらしい」名前を探す。

    **これは目安であって根拠ではない。** 名前から当てにいく方式は取り違える:
    `substeps`（vellum のサブステップ）に対して `dosubstep` を拾ってしまうが、
    `dosubstep` が制御するのは `substep`（Global Substeps）のほうで、
    両者は別々に作用する。だから末尾の s を落とす推測はしない。

    有効/無効の判定そのものは check_disabled() が Houdini に訊く。
    こちらはメッセージに名前を添えるためだけに使う。
    """
    node = parm.node()
    name = parm.name()

    for cand in (f"do{name}", f"enable{name}", f"use{name}"):
        toggle = node.parm(cand)
        if toggle is None:
            continue
        try:
            return cand, bool(toggle.eval())
        except hou.OperationFailed:
            continue
    return None


def check_disabled(parm: hou.Parm) -> str | None:
    """振ろうとしているパラメータが今 無効化されていないか調べる。

    無効なら理由の文字列、問題なければ None。

    Houdini には「トグルがオフだと数値が無視される」構造が多く、これに
    気づかないと「値を振ったのに全部同じ映像」になる。しかもエラーは出ない。

    **判定は Houdini 自身の disable 条件（isDisabled）に任せる。**
    パラメータ名から親トグルを推測すると、実際に無効化しているものとは
    別のトグルを指してしまい、正当な撮影を誤って止める。
    """
    try:
        if not parm.isDisabled():
            return None
    except hou.OperationFailed:
        return None

    found = find_enable_toggle(parm)
    if found and not found[1]:
        return f"'{found[0]}' がオフのため無効化されています"
    return "ノード側の条件で無効化されています（グレーアウト状態）"


def effective_values(parm: hou.Parm, values: list[float]) -> tuple[list[float], dict]:
    """UI の「×」メニュー（`<parm>exp`）を掛けた、実際に効いている値を返す。

    Vellum の stiffness 系は「数値の入力欄」と「× 10^N のメニュー」が並んでいて、
    **効いているのは両者の積**。入力欄の値だけをサイトに出すと嘘になる。

      bendstiffness = 10, bendstiffnessexp = 1  ->  表記は 100

    振る対象が指数側（`...exp`）のときは、隣の入力欄の値に 10^値 を掛ける。

    戻り値は (表記に使う値, 由来のメモ)。
    """
    node = parm.node()
    name = parm.name()

    def scaled(factor: float) -> list[float]:
        # 0.1 を掛けると 0.010000000000000002 のような桁が出る。表記に使う値なので丸める。
        return [float(f"{v * factor:.12g}") for v in values]

    exp_parm = node.parm(f"{name}exp")
    if exp_parm is not None:
        try:
            exp = exp_parm.eval()
        except hou.OperationFailed:
            return values, {}
        return scaled(10.0 ** exp), {
            "multiplier": float(f"{10.0 ** exp:.12g}"),
            "source": f"{name}exp = {exp}",
        }

    if name.endswith("exp"):
        base_parm = node.parm(name[:-3])
        if base_parm is not None:
            try:
                base = base_parm.eval()
            except hou.OperationFailed:
                return values, {}
            # こちらは指数を振っているので、各値が別々の倍率になる
            display = [float(f"{base * (10.0 ** v):.12g}") for v in values]
            return display, {"base": base, "source": f"{name[:-3]} = {base}"}

    return values, {}


def geometry_report(node_path: str) -> dict:
    """sim の結果を数値で見て、破綻していないかを判断できる形で返す。

    **絵を見る前にアトリビュートで異常を捕まえる。** 画像の比較だけだと
    「爆発したのか、そういうパラメータなのか」が分からない。速度と座標を
    直接見れば、少なくとも次のような明確な壊れ方は数値で落ちる:

      - NaN / inf が出た（ソルバが発散した）
      - 速度が桁違いに大きい（爆発）
      - bbox が異常に膨らんだ / 潰れた

    万能ではない（ゆっくり破綻する場合は捕まらない）ので、閾値で自動棄却
    するより「値を記録して並べる」ことに重心を置く。判断は screen.py の仕事。
    """
    node = hou.node(node_path)
    if node is None:
        return {"error": f"ノードが見つかりません: {node_path}"}

    try:
        geo = node.geometry()
    except hou.OperationFailed as exc:
        return {"error": f"ジオメトリを取得できません: {exc}"}
    if geo is None:
        return {"error": "ジオメトリが空です"}

    import math

    positions = geo.pointFloatAttribValues("P")
    bad_p = sum(1 for v in positions if math.isnan(v) or math.isinf(v))

    speeds: list[float] = []
    bad_v = 0
    if geo.findPointAttrib("v") is not None:
        vel = geo.pointFloatAttribValues("v")
        for i in range(0, len(vel) - 2, 3):
            x, y, z = vel[i], vel[i + 1], vel[i + 2]
            if any(math.isnan(c) or math.isinf(c) for c in (x, y, z)):
                bad_v += 1
                continue
            speeds.append(math.sqrt(x * x + y * y + z * z))

    bbox = geo.boundingBox()
    size = bbox.sizevec()

    report = {
        "points": len(geo.points()),
        "prims": len(geo.prims()),
        "bbox_size": [round(size[0], 4), round(size[1], 4), round(size[2], 4)],
        "bbox_center": [round(c, 4) for c in bbox.center()],
        "nan_P": bad_p,
        "nan_v": bad_v,
    }
    if speeds:
        speeds.sort()
        report["speed_max"] = round(speeds[-1], 4)
        report["speed_mean"] = round(sum(speeds) / len(speeds), 4)
        # 1点だけ飛んでいるのか全体が速いのかを区別する
        report["speed_p95"] = round(speeds[int(len(speeds) * 0.95)], 4)
    return report


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
