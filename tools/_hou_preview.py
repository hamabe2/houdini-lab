"""preview.py の hython 側。1フレームだけレンダーする。"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import hou


def report_framing(camera: str, frame: int) -> None:
    """対象がカメラの視野に収まっているかを数値で示す。

    「布が見えない」ときに、画角の外なのか真っ暗なのかを切り分けるため。
    """
    cam = hou.node(camera)
    subject = hou.node("/obj/SUBJECT")
    if cam is None or subject is None:
        return

    # **この計算は軸に正対したカメラしか想定していない。**（t からの距離と
    # r の pitch で視野を出している。）lookatpath で向きを決めるカメラでは
    # r が 0 のままなので、答えは意味のない数字になる。黙って出すと
    # 「はみ出している」と誤読させるので、計算せずに知らせる。
    lookat = cam.parm("lookatpath")
    if lookat is not None and lookat.evalAsString().strip():
        print(f"  画角判定は省略します（{camera} は lookatpath で向きを決めています）")
        print("    この計算は正対カメラ専用です。実際の絵で確認してください。")
        return

    hou.setFrame(frame)

    bbox = None
    for child in subject.children():
        if not child.isDisplayFlagSet():
            continue
        try:
            geo = child.geometry()
        except hou.OperationFailed:
            continue
        if geo is None or len(geo.points()) == 0:
            continue
        bbox = geo.boundingBox()
        break

    if bbox is None:
        print("対象のジオメトリが取得できませんでした")
        return

    cam_t = cam.parmTuple("t").eval()
    focal = cam.parm("focal").eval()
    aperture = cam.parm("aperture").eval()
    resx, resy = cam.parm("resx").eval(), cam.parm("resy").eval()

    dist = abs(cam_t[2] - bbox.center()[2])
    h_fov = 2 * math.atan(aperture / (2 * focal))
    h_extent = 2 * dist * math.tan(h_fov / 2)
    v_extent = h_extent * resy / resx

    pitch = math.radians(cam.parmTuple("r").eval()[0])
    center_y = cam_t[1] + dist * math.tan(pitch)

    y_min, y_max = center_y - v_extent / 2, center_y + v_extent / 2
    x_min, x_max = cam_t[0] - h_extent / 2, cam_t[0] + h_extent / 2

    print(f"  frame {frame} の対象 bbox : {bbox}")
    print(f"  カメラが写す範囲          : X [{x_min:.2f}, {x_max:.2f}]  Y [{y_min:.2f}, {y_max:.2f}]")

    b_min, b_max = bbox.minvec(), bbox.maxvec()
    problems = []
    if b_max[1] > y_max:
        problems.append(f"上が {b_max[1] - y_max:.2f} はみ出し")
    if b_min[1] < y_min:
        problems.append(f"下が {y_min - b_min[1]:.2f} はみ出し")
    if b_max[0] > x_max:
        problems.append(f"右が {b_max[0] - x_max:.2f} はみ出し")
    if b_min[0] < x_min:
        problems.append(f"左が {x_min - b_min[0]:.2f} はみ出し")
    print(f"  画角                      : {'OK' if not problems else ' / '.join(problems)}")


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    hou.hipFile.load(job["hip"], suppress_save_prompt=True, ignore_load_warnings=True)

    rop = hou.node(job["rop"])
    if rop is None:
        raise SystemExit(f"ROP がありません: {job['rop']}")

    out = Path(job["out"])
    out.parent.mkdir(parents=True, exist_ok=True)

    rop.parm("camera").set(job["camera"])
    rop.parm("tres").set(True)
    rop.parm("res1").set(job["width"])
    rop.parm("res2").set(job["height"])
    rop.parm("trange").set(0)          # 現在フレームのみ
    rop.parm("picture").set(str(out))

    if job.get("bbox"):
        report_framing(job["camera"], job["frame"])

    hou.setFrame(1)
    hou.setFrame(job["frame"])
    rop.render(verbose=False, output_progress=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
