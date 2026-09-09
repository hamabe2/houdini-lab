"""共通ルックの template.hip を作る（hython で実行）。

  hython tools/make_template_hip.py

意図的に最小構成にしている。カメラ2台とライト1灯だけ。

**床も背景も置かない。** かつては OpenGL ROP のために `GROUND`（ワイヤーの
グリッド）と `BACKDROP`（暗い板）を置いていた。ROP はビューポートの参照
グリッドも背景も描かないので、それをジオメトリで模倣したものだった。
flipbook 経路（ビューポートをそのまま撮る）に移ったことで代用品は不要になり、
むしろビューポート本来のグリッドと二重になるため外した。

要素を増やすほど破綻の原因が増え、「パラメータの差」より「たまたま壊れたか」
が絵を支配する。必要になってから足す。

**`sweep.py`（OpenGL ROP 経路）はこの変更で背景と床を失う。** あちらを使う
なら ROP 側で用意し直すこと。
"""

import sys
from pathlib import Path

import hou

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scenes" / "template.hip"

CAMERA = "CAM_main"
WIDTH, HEIGHT = 960, 540


def build() -> None:
    hou.hipFile.clear(suppress_save_prompt=True)
    obj = hou.node("/obj")

    # --- カメラ -------------------------------------------------------------
    # 正面から水平に見る。距離 7.0 で横 5.8 / 縦 3.3 単位が写る。
    # 対象の周りに余白を残す。画面いっぱいだと動きの幅が読み取れない。
    cam = obj.createNode("cam", CAMERA)
    # わずかに見下ろす。完全な水平視線だと床のグリッドが地平線に潰れて
    # 空間が読めない。
    cam.parmTuple("t").set((0.0, 1.3, 7.0))
    cam.parmTuple("r").set((-5.0, 0.0, 0.0))
    cam.parm("resx").set(WIDTH)
    cam.parm("resy").set(HEIGHT)
    cam.parm("focal").set(50)
    cam.parm("aperture").set(41.4214)

    # スケールが大きく変わる題材用の引き画。使った場合は JSON の camera に
    # 記録され、ページに「画角を変えている」注記が自動で出る。
    wide = obj.createNode("cam", "CAM_wide")
    wide.parmTuple("t").set((0.0, 1.2, 10.0))
    wide.parmTuple("r").set((0.0, 0.0, 0.0))
    wide.parm("resx").set(WIDTH)
    wide.parm("resy").set(HEIGHT)
    wide.parm("focal").set(50)
    wide.parm("aperture").set(41.4214)

    # 斜め 40 度から見る 3/4 視点。
    #
    # **面に垂直な方向へ動く題材はこちらで撮る。** 布を正対で撮ると、揺れが
    # 奥行き方向の動きになってしまい、折れているのか手前に来ているのかが
    # 読み取れない。40 度振ると Z 方向の動きが横移動として見えるようになり、
    # 振れ幅と折れ方の両方が分かる。床が広く写るのも奥行きの手がかりになる。
    #
    #   x = 7 * sin(40) = 4.50 / z = 7 * cos(40) = 5.36（距離は CAM_main と同じ）
    #
    # **向きは r ではなく lookatpath で決める。** 斜めから狙うと必要な回転が
    # 回転順の解釈に依存し、手計算した角度では対象が画面の端に寄ってしまう。
    # 注視点のノードを置いて向かせれば、そこは考えなくてよくなる。
    aim = obj.createNode("null", "AIM")
    aim.parmTuple("t").set((0.0, 1.3, 0.0))
    aim.setDisplayFlag(False)  # ギズモを写さない

    angle = obj.createNode("cam", "CAM_angle")
    angle.parmTuple("t").set((4.50, 1.9, 5.36))
    angle.parm("lookatpath").set(aim.path())
    angle.parm("resx").set(WIDTH)
    angle.parm("resy").set(HEIGHT)
    angle.parm("focal").set(50)
    angle.parm("aperture").set(41.4214)

    # --- ライト -------------------------------------------------------------
    # 1灯だけ。distant light は位置を持たず r の向きだけで決まり、
    # r=(0,0,0) がカメラと同じ -Z 方向、つまり真正面から照らす状態。
    # 少しだけ振って立体感を出すが、正面成分を残して面の向きが変わっても
    # 真っ黒に落ちないようにする。多灯にすると、向きによって明暗が大きく
    # 変わり「パラメータの差」より「光の当たり方」が絵を支配してしまう。
    key = obj.createNode("hlight", "LIGHT_key")
    key.parm("light_type").set(7)  # distant
    key.parmTuple("r").set((-15.0, 12.0, 0.0))
    key.parm("light_intensity").set(1.0)
    if key.parm("shadow_type") is not None:
        key.parm("shadow_type").set(0)  # 影なし

    # display フラグを落とす。ビューポートはライトを「ギズモ」として線で描き、
    # それが撮影対象の手前に重なる。display を切ると **ギズモだけが消えて
    # 照明は残る。** visibleObjects から外す方法もあるが、あちらは光そのものが
    # 消えてヘッドライトに切り替わってしまう。
    key.setDisplayFlag(False)

    # --- 撮影対象を入れる場所 ------------------------------------------------
    subject = obj.createNode("geo", "SUBJECT")
    subject.createNode("null", "OUT")

    # --- OpenGL ROP ---------------------------------------------------------
    out = hou.node("/out")
    rop = out.createNode("opengl", "OGL_flipbook")
    rop.parm("camera").set(cam.path())
    rop.parm("tres").set(True)
    rop.parm("res1").set(WIDTH)
    rop.parm("res2").set(HEIGHT)
    rop.parm("aamode").set(2)
    rop.parm("trange").set(1)
    # smoothwire = スムースシェーディング + ワイヤーフレーム。
    # 面だけだと変形が読み取りにくいが、トポロジが見えると点がどう動いて
    # いるかが分かる。ビューポートの表示モードと同じ選択肢。
    if rop.parm("shadingmode") is not None:
        rop.parm("shadingmode").set("smoothwire")
    if rop.parm("wirewidth") is not None:
        rop.parm("wirewidth").set(1.0)
    if rop.parm("wireblend") is not None:
        rop.parm("wireblend").set(0.45)  # ワイヤーを控えめに混ぜる
    # 影は使わない。OpenGL の影は品質が低く、破綻の原因が増えるだけ。
    if rop.parm("shadows") is not None:
        rop.parm("shadows").set(False)
    if rop.parm("usegeocolor") is not None:
        rop.parm("usegeocolor").set(True)

    for node in obj.children():
        node.moveToGoodPosition()
    for node in out.children():
        node.moveToGoodPosition()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    hou.hipFile.save(str(OUT))
    print(f"保存しました: {OUT}")


if __name__ == "__main__":
    try:
        build()
    except hou.Error as e:
        print(f"Houdini エラー: {e}", file=sys.stderr)
        raise SystemExit(1)
