"""hython の中でノードのパラメータを読み出す。list_parms.py から呼ばれる。

絵は撮らないので hython（ヘッドレス）で十分。hip を開くだけで sim は走らない。

**ここは「集める」だけで、整形は list_parms.py の仕事。**表の見た目を
Houdini 無しで直せるようにしておきたいため。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import hou

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hou_common import check_disabled  # noqa: E402

# 振る対象になりうる型だけを既定で拾う。Button / Ramp / Separator を並べても
# 候補にはならない。--all で全部出す。
SWEEPABLE = ("Int", "Float", "Toggle", "Menu", "String")


def _template_info(parm: hou.Parm) -> dict:
    """parmTemplate から取れるものを、無い項目は諦めつつ集める。

    型ごとに生えているメソッドが違い、無いものを呼ぶと落ちる。
    ここで落ちるとノード1つぶんの一覧が丸ごと出なくなるので、全部包む。
    """
    tpl = parm.parmTemplate()
    info = {
        "type": tpl.type().name(),
        "label": tpl.label(),
        "help": (tpl.help() or "").strip(),
        "default": None,
        "min": None,
        "max": None,
        "menu": [],
    }

    try:
        defaults = tpl.defaultValue()
    except (AttributeError, hou.OperationFailed):
        defaults = None
    if isinstance(defaults, (tuple, list)):
        idx = parm.componentIndex()
        if idx < len(defaults):
            info["default"] = defaults[idx]
    elif defaults is not None:
        info["default"] = defaults

    for key, getter, strict in (
        ("min", "minValue", "minIsStrict"),
        ("max", "maxValue", "maxIsStrict"),
    ):
        fn = getattr(tpl, getter, None)
        if fn is None:
            continue
        try:
            info[key] = fn()
        except (AttributeError, hou.OperationFailed):
            continue
        # 強制でない範囲は「UI スライダーの端」でしかなく、外の値も入る。
        # 振る値を決めるときに効いてくるので区別する。
        limit = getattr(tpl, strict, None)
        try:
            info[f"{key}_strict"] = bool(limit()) if limit else False
        except (AttributeError, hou.OperationFailed):
            info[f"{key}_strict"] = False

    labels = getattr(tpl, "menuLabels", None)
    items = getattr(tpl, "menuItems", None)
    if labels and items:
        try:
            info["menu"] = [
                {"value": v, "label": l} for v, l in zip(items(), labels())
            ]
        except (AttributeError, hou.OperationFailed):
            pass

    return info


def _notes(parm: hou.Parm, node: hou.Node) -> list[str]:
    """「振っても効かない」を先に見つけて書き出す。

    どれも CLAUDE.md の「ハマりどころ」に実際に載っている失敗そのもの。
    一覧を見た時点で気づけるなら、sim を回してから気づくより遥かに安い。
    """
    notes = []
    name = parm.name()

    # **無効化されているかは Houdini に訊く。** 名前から親トグルを推測すると
    # 取り違える（substeps に対して dosubstep を拾うが、あれが制御するのは
    # Global Substeps のほうで、両者は別々に作用する）。
    reason = check_disabled(parm)
    if reason:
        notes.append(f"今は効かない: {reason}")

    # 「値 x 10^exp」の並び。stretchstiffness を 0.001〜1 で振って
    # 全部同じ絵になった原因がこれ（exp=10 で実効 10^7〜10^10）。
    exp = node.parm(f"{name}exp")
    if exp is not None:
        try:
            notes.append(f"実効値 = 値 x 10^{exp.eval()}（{name}exp）")
        except hou.OperationFailed:
            notes.append(f"実効値 = 値 x 10^({name}exp)")
    if name.endswith("exp") and node.parm(name[:-3]) is not None:
        notes.append(f"{name[:-3]} の指数。振るならこちら側")

    # 式やキーが入っていると parm.set() は上書きされる（＝振っても効かない）。
    #
    # **生の値と eval() の文字列を比べる方法では判定できない。** トグルは
    # raw 'on' / eval 1、float は raw '0' / eval 0.0 のように、式が無くても
    # 食い違う。expression() は式が無いときに例外を投げるので、それで見る。
    try:
        parm.keyframes()
    except hou.OperationFailed:
        pass
    else:
        if parm.keyframes():
            notes.append("キーフレームあり。set() が上書きされる")
    try:
        expr = parm.expression()
    except hou.OperationFailed:
        pass
    else:
        notes.append(f"式が入っている: {expr}")

    return notes


def collect_node(path: str, want_all: bool) -> dict:
    node = hou.node(path)
    if node is None:
        return {"path": path, "error": "ノードが見つかりません"}

    parms = []
    for parm in node.parms():
        info = _template_info(parm)
        if not want_all and info["type"] not in SWEEPABLE:
            continue
        hidden = parm.isHidden()
        if not want_all and hidden:
            continue

        try:
            value = parm.eval()
        except hou.OperationFailed:
            value = None

        parms.append({
            "name": parm.name(),
            "value": value,
            "hidden": hidden,
            "disabled": parm.isDisabled(),
            "folders": list(parm.containingFolders()),
            "notes": _notes(parm, node),
            **info,
        })

    node_type = node.type()
    # 組み込みノードの説明は parmTemplate ではなく、インストール先の
    # help/nodes.zip の中にある（例 sop/vellumsolver.txt）。そこを引くための鍵。
    # 名前空間とバージョンは落とす（hlight::2.0 -> hlight）。
    base = node_type.name().split("::")[0]
    category = node_type.category().name().lower()

    return {
        "path": node.path(),
        "type": node_type.name(),
        "type_label": node_type.description(),
        "help_key": f"{category}/{base}",
        "parms": parms,
    }


def collect_tree(path: str, depth: int) -> list[dict]:
    """ノードの中身を並べる。まずどのノードを見るかを決めるための下見。"""
    root = hou.node(path)
    if root is None:
        raise SystemExit(f"ノードが見つかりません: {path}")

    rows = []

    def walk(node: hou.Node, level: int) -> None:
        for child in node.children():
            # ロックされた HDA の中身は降りない。vellumsolver のような資産は
            # 内部に数十ノードを持っていて、並べても候補選びの役に立たない
            # （振るのは HDA の外に出ているパラメータ）。
            locked = child.isLockedHDA()
            rows.append({
                "path": child.path(),
                "name": child.name(),
                "type": child.type().name(),
                "type_label": child.type().description(),
                "level": level,
                "locked_hda": locked,
            })
            if level < depth and not locked:
                walk(child, level + 1)

    walk(root, 1)
    return rows


def make_probe(type_name: str) -> str:
    """まだシーンに無いノード型を、使い捨てで作って調べられるようにする。

    シーンを組み立てる前に「このノードにはどんなパラメータがあるか」を
    見たいことがある。ドキュメントはラベルしか書いていないことが多く、
    内部名は実物から取るしかない。作るだけで保存はしない。

    'sop:remesh' のように文脈を前置できる。既定は SOP。
    """
    context, _, name = type_name.rpartition(":")
    context = context or "sop"

    if context == "obj":
        parent = hou.node("/obj")
    elif context == "sop":
        parent = hou.node("/obj").createNode("geo", "_probe_geo")
    elif context == "out":
        parent = hou.node("/out")
    else:
        raise SystemExit(f"知らない文脈です: '{context}'（sop / obj / out）")

    try:
        return parent.createNode(name, "_probe").path()
    except hou.OperationFailed as exc:
        raise SystemExit(f"ノード型 '{name}' を作れません（{context}）: {exc}")


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    hou.hipFile.load(job["hip"], suppress_save_prompt=True, ignore_load_warnings=True)

    payload: dict = {}
    if job.get("tree"):
        payload["tree"] = collect_tree(job["tree"], job["depth"])
    else:
        paths = list(job["nodes"])
        paths += [make_probe(t) for t in job.get("types", [])]
        payload["nodes"] = [collect_node(p, job["all"]) for p in paths]

    Path(job["result"]).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
