"""hython 環境の下調べ。sweep.py を書く前に、前提が成り立つかを確認する。

  hython tools/probe_houdini.py
"""

import hou

print("Houdini      :", hou.applicationVersionString())
print("UI available :", hou.isUIAvailable())

# hython には flipbook が無い（hou.SceneViewer.flipbook はビューアを要求する）。
# 代わりに OpenGL ROP を使えることを確認する。
try:
    rop = hou.node("/out").createNode("opengl", "probe_ogl")
    print("opengl ROP   : OK")
    names = {p.name() for p in rop.parms()}
    wanted = [
        "camera", "res1", "res2", "picture", "trange", "f1", "f2", "f3",
        "tres", "aamode", "usehdr", "colorcorrect", "gamma",
        "scenepath", "shadesmooth", "hqlighting", "backgroundcolor",
    ]
    print("  使えるパラメータ:")
    for key in wanted:
        print(f"    {key:18s} {'yes' if key in names else 'NO'}")
except Exception as e:
    print("opengl ROP   : FAILED", type(e).__name__, e)

# Karma / mantra の有無も一応見ておく（今回は使わない方針）
for kind in ("karma", "ifd"):
    try:
        hou.node("/out").createNode(kind, f"probe_{kind}")
        print(f"{kind:13s}: あり")
    except Exception:
        print(f"{kind:13s}: なし")
