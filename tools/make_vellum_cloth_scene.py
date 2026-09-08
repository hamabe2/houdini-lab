"""検証用の Vellum Cloth シーンを template.hip から作る（hython で実行）。

  hython tools/make_vellum_cloth_scene.py

意図的に最小構成にしている。衝突オブジェクトも床も置かず、布を一辺で
固定して垂らすだけ。要素を足すほど破綻の原因が増え、比較したい
パラメータ以外の要因が絵を支配してしまう。

球の上にドレープさせる構成は、曲面で必然的に滑るため「パラメータの差」より
「滑り落ちたかどうか」が絵を決めてしまい、比較として成立しなかった。
"""

from __future__ import annotations

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
    grid.parm("rows").set(30)
    grid.parm("cols").set(40)
    grid.parmTuple("t").set((0.0, CLOTH_Y, 0.0))

    # --- 固定と初期速度 ------------------------------------------------------
    # stopped=1 の点は Vellum が動かさない。上端の一辺を固定して吊るす。
    # pintoanimation は「アニメーションするターゲット入力に追従させる」属性で、
    # ターゲットが無いと固定として働かず布はそのまま落下する。
    #
    # 垂直に吊るしただけでは重力と釣り合って動かないので、面に垂直な向きに初速を
    # 与えて振らせる。面内方向（X）に振っても stretch 拘束で伸びず動かない。
    # 曲げ剛性が高いと板のように、低いとひらひらと動く。
    pin = subject.createNode("attribwrangle", "PIN_EDGE")
    pin.setInput(0, grid)
    pin.parm("class").set(2)  # points
    pin.parm("snippet").set(
        f"// 上端の一辺を固定し、それ以外に面に垂直な初速を与える\n"
        f"if (@P.y > {CLOTH_Y + CLOTH_H / 2 - 0.03:.4f}) {{\n"
        f"    i@stopped = 1;\n"
        f"}} else {{\n"
        f"    v@v = set(0, 0, {SWING});\n"
        f"}}\n"
    )

    # --- Vellum -------------------------------------------------------------
    constraints = subject.createNode("vellumconstraints", "CONSTRAINTS")
    constraints.parm("constrainttype").set("cloth")
    constraints.setInput(0, pin)

    solver = subject.createNode("vellumsolver", "SOLVER")
    solver.setInput(0, constraints, 0)
    solver.setInput(1, constraints, 1)
    # 注意: dosubstep を立てないと substeps の値は無視される。
    # Houdini はこの「トグルがオフだと数値が効かない」構造が多い。
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

    hou.hipFile.save(str(OUT))

    print(f"保存しました: {OUT}")
    print(f"  布     : {grid.path()}  ({grid.parm('rows').eval()}x{grid.parm('cols').eval()})")
    print(f"  固定辺 : {pin.path()}")
    print(f"  拘束   : {constraints.path()}")
    print(f"  ソルバ : {solver.path()}")


if __name__ == "__main__":
    try:
        build()
    except hou.Error as e:
        print(f"Houdini エラー: {e}", file=sys.stderr)
        raise SystemExit(1)
