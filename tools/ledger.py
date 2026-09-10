"""検証の台帳。**何を調べ、何を落とし、なぜかを1か所に残す。**

  python tools/ledger.py add --hip scenes/vellum_cloth.hip --node /obj/SUBJECT/SOLVER
  python tools/ledger.py list
  python tools/ledger.py next

台帳は `screening.json`（リポジトリ直下、コミットする）。

## なぜ要るか

同じ調査を繰り返さないため。`stretchstiffness` は一度「差が無い」と誤って
不採用にし、原因（隣の指数）に気づくまで戻れなかった。CLAUDE.md には散文で
書いてあるが、機械が読めないので次の判断に使えない。

## 状態

  pending   候補に挙げただけ。まだ撮っていない
  screened  setup シートを撮って screen.py が判定した（採用 / 差なし / 破綻）
  published 本撮りして公開した

`screen.py` が判定するたびに、その結果をここへ書き戻す。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "screening.json"
LIST_PARMS = Path(__file__).resolve().parent / "list_parms.py"

# 候補にしないパラメータ。振っても比較にならないもの。
SKIP_TYPES = ("String",)


def key_of(node: str, parm: str) -> str:
    return f"{node}:{parm}"


def load() -> dict:
    if not LEDGER.exists():
        return {"entries": {}}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    LEDGER.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def merge_screen(screen_path: Path) -> int:
    """screen.py の判定結果を台帳に書き戻す。

    **上書きする。** 再測定したなら新しい結果が正しい。
    ただし published の out は消さない（公開済みという事実は判定と別）。
    """
    if not screen_path.exists():
        return 0

    record = json.loads(screen_path.read_text(encoding="utf-8"))
    data = load()
    entries = data.setdefault("entries", {})
    n = 0

    for result in record.get("results", []):
        key = key_of(result["node"], result["parm"])
        entry = entries.setdefault(key, {})
        published_out = entry.get("out")

        entry.update({
            "node": result["node"],
            "parm": result["parm"],
            "status": "published" if published_out else "screened",
            "verdict": result["verdict"],
            "reason": result["reason"],
            "values": result["values"],
            "default": result.get("default"),
            "vs_default_db": result.get("vs_default_db"),
            "neighbour_db": result.get("neighbour_db"),
            "broken": result.get("broken") or {},
            "hip": record.get("hip"),
            "frame": record.get("frame"),
            "same_db": record.get("same_db"),
            "screened_at": record.get("generated"),
        })
        if published_out:
            entry["out"] = published_out
        n += 1

    save(data)
    return n


def add_from_node(hip: Path, node: str, only: str | None) -> int:
    """ノードの振れるパラメータを、未登録のものだけ候補として積む。

    **既に判定済みのものは触らない。** 一度落としたものを毎回また
    候補に戻すと、ループが同じ検証を繰り返すだけになる。
    """
    cmd = [
        str(config.VENV_PYTHON) if hasattr(config, "VENV_PYTHON") else sys.executable,
        str(LIST_PARMS), "--hip", str(hip), "--node", node, "--json",
    ]
    if only:
        cmd += ["--filter", only]

    # **encoding を明示する。** text=True だけだと locale（日本語 Windows では
    # cp932）で読もうとして、UTF-8 の出力で UnicodeDecodeError になる。
    # しかもそれは読み取りスレッドで起きるので stdout が None になって
    # 分かりにくい落ち方をする。
    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise SystemExit(f"list_parms.py が失敗しました:\n{(out.stderr or '')[-1500:]}")

    payload = json.loads(out.stdout.lstrip("﻿"))
    data = load()
    entries = data.setdefault("entries", {})
    added = 0

    for node_info in payload["nodes"]:
        if node_info.get("error"):
            raise SystemExit(f"{node_info['path']}: {node_info['error']}")
        for parm in node_info["parms"]:
            if parm["type"] in SKIP_TYPES:
                continue
            key = key_of(node_info["path"], parm["name"])
            if key in entries:
                continue

            # **グレーアウトしているものは候補にしない。** Houdini 自身が
            # 「この構成では効かない」と言っているものを撮っても意味がない
            # （cloth の拘束で adhesion や attachframe を振る類）。
            # 消さずに残すのは、構成を変えれば候補に戻るため。
            disabled = parm.get("disabled")
            entries[key] = {
                "node": node_info["path"],
                "parm": parm["name"],
                "label": parm["label"],
                "type": parm["type"],
                "folders": parm.get("folders") or [],
                "default": parm["default"],
                "min": parm.get("min"),
                "max": parm.get("max"),
                "help": (parm.get("help") or "")[:400],
                "notes": parm.get("notes") or [],
                "status": "無効" if disabled else "pending",
                "reason": "この構成ではグレーアウトしている（効かない）" if disabled else "",
                "hip": str(hip),
            }
            added += 1

    save(data)
    return added


def cmd_list(args: argparse.Namespace) -> int:
    data = load()
    entries = data.get("entries", {})
    if not entries:
        print("台帳は空です。`ledger.py add` で候補を積んでください。")
        return 0

    rows = [e for e in entries.values() if not args.status or e.get("status") == args.status]
    rows.sort(key=lambda e: (e.get("status", ""), e.get("node", ""), e.get("parm", "")))

    by_status: dict[str, int] = {}
    for entry in entries.values():
        by_status[entry.get("status", "?")] = by_status.get(entry.get("status", "?"), 0) + 1

    print(f"台帳: {LEDGER}  （更新 {data.get('updated', '?')}）")
    print("  " + " / ".join(f"{k} {v}" for k, v in sorted(by_status.items())))
    print()

    for entry in rows:
        status = entry.get("status", "?")
        verdict = entry.get("verdict", "")
        head = f"  [{status:<9}] {entry['parm']:<26} {entry.get('label', ''):<28}"
        print(head + (f" {verdict}" if verdict else ""))
        if entry.get("reason"):
            print(f"      {entry['reason']}")
        for note in entry.get("notes", []):
            print(f"      ! {note}")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """次に調べるべき候補を出す。ループの入口。"""
    data = load()
    pending = [e for e in data.get("entries", {}).values() if e.get("status") == "pending"]
    if not pending:
        print("未評価の候補はありません。")
        return 0

    # 振れる型を優先し、説明があるもの（＝公式に意味が書かれているもの）を先に。
    # UI のタブが他の題材向け（流体・粒・毛など）のものは後ろへ回す。
    # グレーアウトほど確実な信号ではないので、除外ではなく順位で下げる。
    off_topic = ("Fluid", "Grain", "Hair", "Muscle", "Wind", "Plasticity", "Pressure")

    def rank(entry: dict) -> tuple:
        folders = " ".join(entry.get("folders") or [])
        return (
            1 if any(word in folders for word in off_topic) else 0,
            0 if entry.get("type") in ("Float", "Int") else 1,
            0 if entry.get("help") else 1,
            entry.get("parm", ""),
        )

    pending.sort(key=rank)
    picked = pending[: args.count]

    print(f"未評価 {len(pending)} 件のうち {len(picked)} 件:")
    probes = []
    for entry in picked:
        folders = " / ".join(entry.get("folders") or []) or "(タブなし)"
        print(f"  {entry['node']} / {entry['parm']}"
              f"  （{entry.get('label', '')} — {folders}）")
        if entry.get("help"):
            print(f"      {entry['help'][:150]}")
        probes.append(f'--probe "{entry["node"]}:{entry["parm"]}=<値をここに>"')

    print()
    print("値を決めて setup_sheet.py に渡してください:")
    print("  .venv\\Scripts\\python.exe tools\\setup_sheet.py --hip <hip> \\")
    for probe in probes:
        print(f"    {probe} \\")
    print("  そのあと tools\\screen.py で判定 → 台帳に自動で書き戻ります。")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンがありません: {hip}")
    total = 0
    for node in args.node:
        n = add_from_node(hip, node, args.filter)
        print(f"{node}: {n} 件を候補に追加しました")
        total += n
    if total == 0:
        print("（すべて登録済みでした）")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """公開したことを記録する。"""
    data = load()
    key = key_of(args.node, args.parm)
    entry = data.get("entries", {}).get(key)
    if entry is None:
        raise SystemExit(f"台帳にありません: {key}")
    entry["status"] = "published"
    entry["out"] = args.out
    save(data)
    print(f"{key} を published にしました（{args.out}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="検証の台帳")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="ノードのパラメータを候補として積む")
    p_add.add_argument("--hip", required=True)
    p_add.add_argument("--node", action="append", required=True)
    p_add.add_argument("--filter", help="内部名・ラベル・説明への絞り込み（正規表現）")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="台帳の中身を表示する")
    p_list.add_argument("--status", choices=("pending", "screened", "published"))
    p_list.set_defaults(func=cmd_list)

    p_next = sub.add_parser("next", help="次に調べる候補を出す")
    p_next.add_argument("--count", type=int, default=3)
    p_next.set_defaults(func=cmd_next)

    p_pub = sub.add_parser("publish", help="公開したことを記録する")
    p_pub.add_argument("--node", required=True)
    p_pub.add_argument("--parm", required=True)
    p_pub.add_argument("--out", required=True)
    p_pub.set_defaults(func=cmd_publish)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
