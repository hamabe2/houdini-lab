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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hou_common import (  # noqa: E402
    check_disabled,
    check_file_caches,
    clear_sim_caches,
    effective_values,
    resolve_parm,
)


def log(msg: str) -> None:
    print(msg, flush=True)


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

    reason = check_disabled(parm)
    if reason:
        raise SystemExit(
            f"'{parm_name}' を変えても効果がありません: {reason}\n"
            f"  シーン側で {node_path} の設定を見直してください。\n"
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

    # 「× 10^N」メニューが隣にあるなら、サイトに出すのは掛けた後の値。
    display, note = effective_values(parm, values)
    if note:
        log(f"表記は実効値にします（{note['source']}）: {display}")

    Path(job["result"]).write_text(
        json.dumps(
            {"segments": results, "display_values": display, "multiplier": note},
            indent=2,
        ),
        encoding="utf-8",
    )
    log("撮影完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
