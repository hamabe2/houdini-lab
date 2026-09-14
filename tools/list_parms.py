"""ノードのパラメータを一覧にする。**振る候補をここから選ぶ。**

  python tools/list_parms.py --hip scenes/vellum_cloth.hip --node /obj/SUBJECT/SOLVER
  python tools/list_parms.py --hip scenes/vellum_cloth.hip --tree /obj/SUBJECT

## なぜ要るか

`--parm niter` のように**内部名**で指定するのに、Houdini の UI に出ているのは
"Constraint Iterations" という**ラベル**で、両者は一致しない。UI を眺めても
内部名は分からないし、内部名だけ見ても何のことか分からない。
そのせいで候補選びが「なんとなく知っているパラメータ」に寄る。

このツールは 内部名 / ラベル / 現在値 / 既定 / 範囲 / 説明 を並べて、
**候補選出に見えるソースを与える**。

  list_parms.py で候補を挙げる
    -> setup_sheet.py で1枚ずつ撮って篩にかける
    -> flipbook.py で本撮り

## 出てくる注記

「振っても効かない」パターンを先に見つけて出す（CLAUDE.md のハマりどころ）:

  dosubstep=**OFF** でないと効かない   トグルがオフだと数値が無視される
  実効値 = 値 x 10^10（stretchstiffnessexp）  隣の指数が効いている
  キーフレームあり。set() が上書きされる
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

HOU_LIST = Path(__file__).resolve().parent / "_hou_list_parms.py"


def run_houdini(job: dict) -> dict:
    if not config.HYTHON.exists():
        raise SystemExit(
            f"hython が見つかりません: {config.HYTHON}\n"
            "  config.py の HOUDINI_ROOT を確認してください。"
        )

    with tempfile.TemporaryDirectory() as tmp:
        job_path = Path(tmp) / "job.json"
        result_path = Path(tmp) / "result.json"
        job["result"] = str(result_path)
        job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")

        env = os.environ.copy()
        env["HLCACHE"] = str(config.CACHE_ROOT)
        config.CACHE_ROOT.mkdir(parents=True, exist_ok=True)

        proc = subprocess.run([str(config.HYTHON), str(HOU_LIST), str(job_path)], env=env)
        if proc.returncode != 0:
            raise SystemExit(f"hython が失敗しました (exit {proc.returncode})")
        if not result_path.exists():
            raise SystemExit("結果が出力されませんでした")
        return json.loads(result_path.read_text(encoding="utf-8"))


# --- 公式ドキュメント --------------------------------------------------------
#
# **組み込みノードの説明文は parmTemplate().help() には入っていない。** 実測で
# vellumsolver の全パラメータが空だった。本文はインストール先の
# houdini/help/nodes.zip に `sop/vellumsolver.txt` として入っていて、
# パラメータごとに `#id: <内部名>` という印が付いている。そこを読む。
#
# ここを Houdini の外（.venv の python）でやるのは、表示の調整のたびに
# hython を起こしたくないため。

HELP_ZIP = config.HOUDINI_ROOT / "houdini" / "help" / "nodes.zip"

_ID_MARK = re.compile(r"^\s*#id:\s*(\S+)\s*$")


def parse_node_help(text: str) -> dict[str, str]:
    """ノードのヘルプ本文を {内部名: 説明} に分解する。

    書式（実物）:

        Constraint Iterations:
            #id: niter

            Within each substep, this number of passes will be taken by
            the constraint enforcement operations. ...

    説明は `#id:` の後ろに続く字下げされた行。字下げの無い行が来たら
    次の項目に移ったと見なす。
    """
    docs: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _ID_MARK.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        body: list[str] = []
        i += 1
        while i < len(lines):
            line = lines[i]
            if line.strip() and not line[0].isspace():
                break  # 字下げが切れたら次の項目
            if _ID_MARK.match(line):
                break
            body.append(line.strip())
            i += 1
        text_ = " ".join(part for part in body if part).strip()
        # 本文が他ファイルへの参照だけのことがある（:include /nodes/dop/x#y/:）。
        # 展開までは追わない。どこを見ればいいかだけ示す。
        ref = re.fullmatch(r":include\s+(\S+?)/:", text_)
        if ref:
            text_ = f"（説明は {ref.group(1)} を参照）"
        docs[name] = text_
    return docs


def load_docs(help_key: str) -> dict[str, str]:
    """help_key（例 'sop/vellumsolver'）の説明を読む。無ければ空。"""
    if not HELP_ZIP.exists():
        return {}
    import zipfile
    try:
        with zipfile.ZipFile(HELP_ZIP) as zf:
            with zf.open(f"{help_key}.txt") as fh:
                return parse_node_help(fh.read().decode("utf-8", "replace"))
    except (KeyError, OSError, zipfile.BadZipFile):
        return {}


# --- 表示 -------------------------------------------------------------------


def fmt_value(value, kind: str = "") -> str:
    if value is None:
        return "-"
    # Toggle は 0/1 で返ってくる。既定は bool なので、揃えないと
    # 「現在 1 / 既定 off」のような読みにくい並びになる。
    if kind == "Toggle":
        return "on" if value else "off"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        # 10000000000.0 より 1e+10 のほうが桁を読み違えない
        if value and (abs(value) >= 1e6 or abs(value) < 1e-3):
            return f"{value:.3g}"
        return f"{value:g}"
    return str(value)


def fmt_range(parm: dict) -> str:
    lo, hi = parm.get("min"), parm.get("max")
    if lo is None and hi is None:
        return ""
    # 強制でない範囲は UI スライダーの端でしかなく、外の値も入れられる。
    # 「破綻する値を片端に入れる」ときにここを超える必要がよくある。
    mark = "" if parm.get("min_strict") or parm.get("max_strict") else " (目安)"
    return f"{fmt_value(lo)}〜{fmt_value(hi)}{mark}"


def print_tree(rows: list[dict]) -> None:
    if not rows:
        print("  （子ノードなし）")
        return
    for row in rows:
        indent = "  " * row["level"]
        note = "  (HDA)" if row.get("locked_hda") else ""
        print(f"{indent}{row['name']:<24} {row['type']:<24} {row['type_label']}{note}")


def print_node(node: dict, args: argparse.Namespace) -> None:
    if node.get("error"):
        print(f"!! {node['path']}: {node['error']}")
        return

    print(f"=== {node['path']}  [{node['type']}] {node['type_label']} ===")

    parms = filter_parms(node["parms"], args)
    if not parms:
        print("  該当なし（--filter を緩めるか --all を付けてください）")
        return

    # フォルダ（UI のタブ）ごとにまとめる。Houdini の UI と同じ並びで
    # 見えるほうが、実際に触るときに探しやすい。
    current = None
    for parm in parms:
        folder = " / ".join(parm["folders"]) or "(タブなし)"
        if folder != current:
            current = folder
            print(f"\n  --- {folder} ---")

        flags = []
        if parm["hidden"]:
            flags.append("hidden")
        if parm["disabled"]:
            flags.append("disabled")
        if parm["value"] != parm["default"] and parm["default"] is not None:
            flags.append("既定から変更済み")

        kind = parm["type"]
        line = (
            f"  {parm['name']:<22} {parm['label']:<28} "
            f"{kind:<7} 現在 {fmt_value(parm['value'], kind):<10}"
            f" 既定 {fmt_value(parm['default'], kind):<10}"
        )
        rng = fmt_range(parm)
        if rng:
            line += f" 範囲 {rng}"
        if flags:
            line += f"  [{', '.join(flags)}]"
        print(line)

        for note in parm["notes"]:
            print(f"      ! {note}")

        if parm["menu"]:
            shown = ", ".join(f"{m['value']}={m['label']}" for m in parm["menu"][:8])
            more = " ..." if len(parm["menu"]) > 8 else ""
            print(f"      選択肢: {shown}{more}")

        if args.doc and parm["help"]:
            for chunk in wrap(parm["help"], args.width - 8):
                print(f"      {chunk}")


def wrap(text: str, width: int) -> list[str]:
    import textwrap
    lines = []
    for para in text.splitlines():
        lines.extend(textwrap.wrap(para, width) or [""])
    return lines


def filter_parms(parms: list[dict], args: argparse.Namespace) -> list[dict]:
    if not args.filter:
        return parms
    try:
        pattern = re.compile(args.filter, re.IGNORECASE)
    except re.error as exc:
        raise SystemExit(f"--filter が正規表現として読めません: {exc}")
    return [
        p for p in parms
        if pattern.search(p["name"]) or pattern.search(p["label"])
        or pattern.search(p["help"])
    ]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ノードのパラメータを一覧にする（振る候補を選ぶため）",
    )
    ap.add_argument("--hip", required=True, help="シーンファイル")
    ap.add_argument(
        "--node", action="append", default=[], metavar="PATH",
        help="対象ノード。複数回指定できる",
    )
    ap.add_argument(
        "--type", action="append", default=[], dest="types", metavar="[文脈:]型名",
        help="まだシーンに無いノード型を調べる（例 remesh / obj:cam）。使い捨てで作るだけ",
    )
    ap.add_argument(
        "--tree", metavar="PATH",
        help="中のノードを並べるだけ（どのノードを見るかを決める下見）",
    )
    ap.add_argument("--depth", type=int, default=2, help="--tree の深さ（既定 2）")
    ap.add_argument(
        "--filter", metavar="REGEX",
        help="内部名・ラベル・説明文にかける絞り込み（大小無視）",
    )
    ap.add_argument(
        "--all", action="store_true",
        help="隠しパラメータと Button/Ramp なども出す（既定は振れる型だけ）",
    )
    ap.add_argument("--doc", action="store_true", help="各パラメータの説明文も出す")
    ap.add_argument("--width", type=int, default=100, help="--doc の折り返し幅")
    ap.add_argument("--json", action="store_true", help="整形せず JSON をそのまま出す")
    args = ap.parse_args()

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンファイルがありません: {hip}")
    if bool(args.tree) == bool(args.node or args.types):
        raise SystemExit("--node / --type か --tree のどちらかを指定してください")

    result = run_houdini({
        "hip": str(hip),
        "nodes": args.node,
        "types": args.types,
        "tree": args.tree,
        "depth": args.depth,
        "all": args.all,
    })

    # **説明の統合は表示より前にやる。** ここを print_node の中でやっていたら
    # --json の出力にだけ説明が入らず、検証リストが空の help を抱えることになった。
    for node in result.get("nodes", []):
        docs = load_docs(node.get("help_key", ""))
        for parm in node.get("parms", []):
            if not parm["help"]:
                parm["help"] = docs.get(parm["name"], "")

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print()
    if args.tree:
        print(f"=== {args.tree} の中身 ===")
        print_tree(result["tree"])
        print()
        print("  見たいノードを  --node <path>  で指定してください。")
        return 0

    for node in result["nodes"]:
        print_node(node, args)
        print()

    print("  候補を決めたら setup_sheet.py で1枚ずつ撮って篩にかけてください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
