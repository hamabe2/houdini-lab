"""検証用の Vellum Cloth シーンを template.hip から作る（hython で実行）。

  hython tools/make_vellum_cloth_scene.py

意図的に最小構成にしている。衝突オブジェクトも床も置かず、布を一辺で
固定して垂らすだけ。要素を足すほど破綻の原因が増え、比較したい
パラメータ以外の要因が絵を支配してしまう。

球の上にドレープさせる構成は、曲面で必然的に滑るため「パラメータの差」より
「滑り落ちたかどうか」が絵を決めてしまい、比較として成立しなかった。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import hou

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scenes" / "template.hip"
OUT = ROOT / "scenes" / "vellum_cloth.hip"

CLOTH_W = 2.4    # X 方向の幅
CLOTH_H = 1.6    # Y 方向の高さ
CLOTH_Y = 1.4    # 中心の高さ（上端 2.2 / 下端 0.6）
SWING = 2.5      # 初期速度。布の面に垂直な Z 方向へ振って揺らす

# remesh の目標辺長。小さくすると sim が重くなるうえ、
# **かえってパラメータの差が読み取りにくくなる**ので下げすぎない。
TARGET_SIZE = 0.12

# 比較に効く値は HDA の既定任せにしない。`constrainttype=cloth` を選ぶと
# 指数メニューが暗黙に書き換わる（実測: bendstiffnessexp が -1 になっていた）。
# 入力欄の値と実効値がズレる原因になるので、4つとも明示的に固定する。
#
#   実効値 = 入力欄の値 x 10^exp
#
# tab メニューの "Vellum Configure Cloth" が設定する値。**これが布の出発点。**
#
# 出どころは推測ではなくインストール先のツール定義:
#   $HFS/houdini/toolbar/ExtraTools.shelf の <tool name="geometry_vellumconfigurecloth">
#
# `constrainttype='cloth'` を入れるだけでは足りない。あれはメニュー1つで、
# 質量・厚み・圧縮拘束は既定のまま（bendstiffnessexp も -1 にしかならない）。
# 下の7項目が揃って初めて「布として妥当な状態」になる。
#
# 明示的に書くのは、HDA の既定に依存すると Houdini 側の都合で静かに変わり、
# 比較の基準点がずれるため。
def cloth_preset() -> dict:
    """ExtraTools.shelf の Vellum Configure Cloth と同じ値を組み立てる。

    `bendstiffnessexp` は密度スケールの逆数で決まる（ツール定義のコメント
    いわく "Bend Stiffness varies as inverse of density"）。単位がメートルなら
    scale=1 で -4 になる。ベタ書きせず同じ式で出す。
    """
    scale = hou.scaleFromMKS("kg1m-2")
    bendexp = int(round(-4 - math.log10(scale)))
    return {
        "constrainttype": "cloth",
        "domass": "calcvarying",       # 質量をジオメトリから計算する
        "density": 0.1 * scale,
        "dothickness": "calcuniform",  # 厚みもジオメトリから計算する
        "docompress": True,            # 圧縮拘束を有効にする
        "bendstiffness": 1.0,
        "bendstiffnessexp": bendexp,   # 実効 1 x 10^-4
        "dostretchgrp": True,
        "dobendgrp": True,
    }


def set_verified(node: hou.Node, name: str, value) -> None:
    """設定して、読み返して、違ったら止める。

    **指数メニューは Menu 型なので set(0) がインデックス指定と解釈されうる。**
    その場合 0 番目は '10'（×1e10）で、狙いと桁が10桁ずれる。しかもエラーは
    出ないまま「なぜか伸びない布」が撮れるだけになる。だから読み返す。
    """
    parm = node.parm(name)
    if parm is None:
        raise SystemExit(f"パラメータがありません: {node.path()} / {name}")
    parm.set(value)

    # 文字列トークンのメニュー（'cloth' や 'calcvarying'）は eval() が
    # インデックスの整数を返すので、読み返しは evalAsString() で行う。
    got = parm.evalAsString() if isinstance(value, str) else parm.eval()
    if got != value:
        raise SystemExit(
            f"{node.path()} / {name} に {value!r} を入れたのに {got!r} になりました。\n"
            "  Menu 型でインデックス指定と解釈された可能性があります。"
        )


def build() -> None:
    if not TEMPLATE.exists():
        raise SystemExit(
            f"template.hip がありません: {TEMPLATE}\n"
            "  先に hython tools/make_template_hip.py を実行してください。"
        )

    hou.hipFile.load(str(TEMPLATE), suppress_save_prompt=True)

    subject = hou.node("/obj/SUBJECT")
    for child in subject.children():
        child.destroy()

    # --- 布 -----------------------------------------------------------------
    # 解像度は控えめに。細かいほど sim が重く、破綻もしやすい。
    # 垂直に吊るす。水平に浮かせて垂らす構成は、初期状態で布の裏面
    # （法線が上向き）をカメラが見上げることになり、OpenGL では真っ黒に
    # 落ちてしまう。垂直なら面が常にカメラを向く。
    grid = subject.createNode("grid", "CLOTH")
    grid.parm("sizex").set(CLOTH_W)
    grid.parm("sizey").set(CLOTH_H)
    grid.parm("orient").set(0)      # 0=XY(垂直) / 1=YZ / 2=ZX(水平)
    grid.parm("rows").set(20)
    grid.parm("cols").set(30)
    grid.parmTuple("t").set((0.0, CLOTH_Y, 0.0))

    # --- 三角メッシュ化 ------------------------------------------------------
    # grid が出すのは四角ポリゴン。**布は三角メッシュのほうが sim に向く。**
    # remesh はほぼ正三角形に組み直す（辺の向きに偏りが出ない）。
    #
    # **固定より前に入れる。** 後ろに入れると stopped 属性が補間されて、
    # 「半分だけ固定された点」ができて壊れる。
    # 境界エッジは remesh が必ず保持するので、上端の一辺は残る。
    remesh = subject.createNode("remesh", "REMESH")
    remesh.setInput(0, grid)
    remesh.parm("sizing").set("uniform")
    remesh.parm("targetsize").set(TARGET_SIZE)
    remesh.parm("iterations").set(3)  # 公式ドキュメントいわく有効なのは 3〜4 まで

    # --- 固定と初期速度 ------------------------------------------------------
    # stopped=1 の点は Vellum が動かさない。上端の一辺を固定して吊るす。
    # pintoanimation は「アニメーションするターゲット入力に追従させる」属性で、
    # ターゲットが無いと固定として働かず布はそのまま落下する。
    #
    # 垂直に吊るしただけでは重力と釣り合って動かないので、面に垂直な向きに初速を
    # 与えて振らせる。面内方向（X）に振っても stretch 拘束で伸びず動かない。
    # 曲げ剛性が高いと板のように、低いとひらひらと動く。
    pin = subject.createNode("attribwrangle", "PIN_EDGE")
    pin.setInput(0, remesh)
    pin.parm("class").set(2)  # points
    # 判定幅は目標辺長に連動させる。固定される点の数が TARGET_SIZE の
    # 変更で変わってしまわないように（固定値ベタ書きだと密度次第で
    # 2列目まで巻き込んだり、逆に0個になったりする）。
    pin.parm("snippet").set(
        f"// 上端の一辺を固定し、それ以外に面に垂直な初速を与える\n"
        f"if (@P.y > {CLOTH_Y + CLOTH_H / 2 - TARGET_SIZE / 2:.4f}) {{\n"
        f"    i@stopped = 1;\n"
        f"}} else {{\n"
        f"    v@v = set(0, 0, {SWING});\n"
        f"}}\n"
    )

    # --- Vellum -------------------------------------------------------------
    constraints = subject.createNode("vellumconstraints", "CONSTRAINTS")
    constraints.setInput(0, pin)

    # constrainttype を最初に入れる。これを変えると他のパラメータが
    # プリセットで書き換わるので、残りはその後で上書きする。
    preset = cloth_preset()
    set_verified(constraints, "constrainttype", preset.pop("constrainttype"))
    for name, value in preset.items():
        set_verified(constraints, name, value)

    solver = subject.createNode("vellumsolver", "SOLVER")
    solver.setInput(0, constraints, 0)
    solver.setInput(1, constraints, 1)
    # dosubstep（Use Global Substeps）が制御するのは substep（Global Substeps）で、
    # 下の substeps（vellum のサブステップ）とは別々に作用する。
    # ここで立てているのは前者を使うため。
    solver.parm("dosubstep").set(1)
    solver.parm("substeps").set(3)

    # 色は最後に付ける。ソルバを経由する過程で Cd が落ちても確実に乗る。
    color = subject.createNode("color", "OUT_color")
    color.setInput(0, solver)
    color.parmTuple("color").set((0.88, 0.47, 0.33))

    out = subject.createNode("null", "OUT")
    out.setInput(0, color)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    for node in subject.children():
        node.moveToGoodPosition()

    # **保存の前に検算する。** 壊れた hip を残してから気づくのは遅い。
    # 三角メッシュ化の結果と固定点数を実際に数える。
    geo = pin.geometry()
    pinned = len([p for p in geo.points() if p.attribValue("stopped")])
    prims = len(geo.prims())
    tris = len([p for p in geo.prims() if len(p.vertices()) == 3])

    print(f"  布     : {grid.path()} -> {remesh.path()}  targetsize={TARGET_SIZE}")
    print(f"           {len(geo.points())} 点 / {prims} プリミティブ（三角 {tris}）")
    print(f"  固定辺 : {pin.path()}  ({pinned} 点)")
    print(f"  拘束   : {constraints.path()}  （Vellum Configure Cloth と同じ設定）")
    for name in cloth_preset():
        parm = constraints.parm(name)
        print(f"           {name} = {parm.evalAsString()}")
    exp = constraints.parm("bendstiffnessexp").eval()
    bend = constraints.parm("bendstiffness").eval()
    print(f"           -> bend 実効値 = {bend * 10 ** exp:g}")
    print(f"  ソルバ : {solver.path()}")

    if pinned == 0:
        raise SystemExit("固定された点が0個です。PIN_EDGE の判定を確認してください。")
    if tris != prims:
        raise SystemExit(f"三角以外のプリミティブが {prims - tris} 個あります。")

    hou.hipFile.save(str(OUT))
    print(f"保存しました: {OUT}")


if __name__ == "__main__":
    try:
        build()
    except hou.Error as e:
        print(f"Houdini エラー: {e}", file=sys.stderr)
        raise SystemExit(1)
