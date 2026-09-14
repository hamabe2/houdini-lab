"""検証リスト。**何を調べ、何を落とし、なぜかを1か所に残す。**

  python tools/ledger.py add --hip scenes/vellum_cloth.hip --node /obj/SUBJECT/SOLVER
  python tools/ledger.py list
  python tools/ledger.py next

検証リストは `screening.json`（リポジトリ直下、コミットする）。

## なぜ要るか

同じ調査を繰り返さないため。`stretchstiffness` は一度「差が無い」と誤って
不採用にし、原因（隣の指数）に気づくまで戻れなかった。CLAUDE.md には散文で
書いてあるが、機械が読めないので次の判断に使えない。

## 状態

  pending   候補に挙げただけ。まだ撮っていない
  screened  setup シートを撮って screen.py が判定した（採用 / 差なし / 破綻）
  approved  人が「本撮りしてよい」と決めた
  rejected  人が「撮らない」と決めた（理由つき）
  shot      本撮りが終わった。mp4 はあるが、まだ記事に載せていない
  published 記事に載せて公開した

**`shot` と `published` を分けること。** 撮り終えたものを approved のままに
すると `review.py --approved` が「本撮りするならこれ」と同じものを出し続け、
撮った本数が増えるほど何が残っているのか分からなくなる。撮影（機械）と公開
（記事を書く人）は速さが違うので、その間に状態が要る。

**人が下す決定は3つある。**「却下」と「やり直し」は違う:

  承認      この段階で本撮りしてよい                → approved
  やり直し  候補は生きている。提案（刻み・範囲）が悪い → pending に戻す + retry
  却下      候補そのものに価値がない                → rejected（二度と戻さない）

やり直しは提案だけを捨てて候補を生かす。理由は `retry.note` に、機械が
効かせられる指示（撮る上限・下限・段数）は `retry.overrides` に入る。
`screen_loop.py` は retry のある候補を最優先で拾い、overrides を
`propose_values.py` に渡す。

`screen.py` が判定するたびに、その結果をここへ書き戻す。

**判定（verdict）と決定（status）は別物。** verdict は機械が数値で出すもので、
「絵が変わるか」しか見ていない。撮る価値があるかは人が決める。その境目が
approved / rejected で、`tools/review.py` と `/review/` がその入力口。
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

# 梯子を作れる型。`propose_values.py` は既定値に 10^k を掛けて端を探すので、
# **既定値が数値でないと動かない。** トグルやメニューは候補として残るが
# （0/1 を並べる価値はある）、自動ループには乗せない。
LADDER_TYPES = ("Float", "Int")

# UI のタブ名が他の題材向け（流体・粒・毛など）のもの。除外ではなく順位を下げる。
# グレーアウトほど確実な信号ではないため。
OFF_TOPIC = ("Fluid", "Grain", "Hair", "Muscle", "Wind", "Plasticity", "Pressure")

# 本撮りが済んでいる状態。**判定をやり直しても落とさない。**
SHOT_STATES = ("shot", "published")

STATUSES = ("pending", "screened", "approved", "rejected", "shot", "published", "無効")


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
    """screen.py の判定結果を検証リストに書き戻す。

    **上書きする。** 再測定したなら新しい結果が正しい。
    ただし撮影・公開の事実は消さない（撮ったかどうかは判定と別の軸）。
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
        shot_out = entry.get("out")
        # **撮影済み・公開済みは status を落とさない。** 判定をやり直しても
        # 「もう撮ってある」という事実は変わらないので、screened に戻すと
        # 本撮りの待ち行列に同じものがもう一度並ぶ。
        kept = entry.get("status") if entry.get("status") in SHOT_STATES else None

        entry.update({
            "node": result["node"],
            "parm": result["parm"],
            "status": kept or "screened",
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
        if shot_out:
            entry["out"] = shot_out
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


def rank(entry: dict) -> tuple:
    """次に調べる順。振れる型を優先し、公式の説明があるものを先に。

    **やり直しは最優先。**人が絵を見て「こう撮り直せ」と言った候補で、
    答えを待たせている。まだ誰も見ていない候補より先に返す。
    """
    folders = " ".join(entry.get("folders") or [])
    return (
        0 if entry.get("retry") else 1,
        1 if any(word in folders for word in OFF_TOPIC) else 0,
        0 if entry.get("type") in LADDER_TYPES else 1,
        0 if entry.get("help") else 1,
        entry.get("parm", ""),
    )


def pending(
    data: dict, types: tuple[str, ...] | None = None, max_errors: int | None = None,
) -> list[dict]:
    """未評価の候補を、調べるべき順に並べて返す。"""
    rows = [e for e in data.get("entries", {}).values() if e.get("status") == "pending"]
    if types:
        rows = [e for e in rows if e.get("type") in types]
    if max_errors is not None:
        rows = [e for e in rows if e.get("error_count", 0) < max_errors]
    rows.sort(key=rank)
    return rows


def is_awaiting(entry: dict) -> bool:
    """人の決定を待っているか。

    **「差なし」だけは人に見せない。**既定値と絵が変わらないことは数値で
    決着がついていて、人が覆す材料が無い。逆に**「破綻」は見せる。**
    片端に破綻する値を入れるのは意図的な選び方（破綻を見せることが理解に
    つながる）なので、機械に捨てさせない。
    """
    return entry.get("status") == "screened" and entry.get("verdict") != "差なし"


def awaiting(data: dict | None = None) -> list[dict]:
    """承認待ちの候補を返す。"""
    rows = [e for e in (data or load()).get("entries", {}).values() if is_awaiting(e)]
    rows.sort(key=lambda e: (e.get("node", ""), e.get("parm", "")))
    return rows


def decide(node: str, parm: str, status: str, note: str = "", by: str = "human") -> dict:
    """決定を検証リストに記録する。

    **撮る値をここで凍らせる。** `propose_values.py` を回し直すと提案は
    変わりうるが、承認したのはそのとき見た5枚の絵。後で本撮りする値が
    黙って変わらないよう、決めた時点の値を `approved_values` に写す。

    **誰が決めたかを残す（`decided_by`）。** 無人モードでは機械が承認するので、
    後から人が見るときに「これは誰も絵を見ていない」と分かる必要がある。
    """
    if status not in ("approved", "rejected"):
        raise ValueError(f"status が不正です: {status}")

    data = load()
    entry = data.get("entries", {}).get(key_of(node, parm))
    if entry is None:
        raise KeyError(f"検証リストにありません: {key_of(node, parm)}")

    entry["status"] = status
    entry["decision_note"] = note
    entry["decided_by"] = by
    entry["decided_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if status == "approved":
        proposal = entry.get("proposal") or {}
        entry["approved_values"] = {
            "values": entry.get("values") or proposal.get("values"),
            "display_values": proposal.get("display_values"),
            "default_index": proposal.get("default_index", 0),
            "label": entry.get("label") or parm,
        }
    save(data)
    return entry


def auto_approve(data: dict | None = None) -> list[dict]:
    """機械の判定だけで承認する。**無人モード専用。**

    **通すのは verdict が「採用」のものだけ。**本撮りは1本あたり
    5値 x 48フレームの sim なので、外れを撮る時間が惜しい。

      採用（段階に無駄あり）  隣どうしがほぼ同じ段があり、枠を捨てている
      破綻                    片端に破綻を入れるのは意図的な選び方で、人の判断
      差なし                  そもそも人にも見せない（is_awaiting）

    この2つは `screened` のまま残るので、帰宅後に `/review/` で静止画を見て
    決めることになる。**機械が勝手に捨てはしない。**
    """
    targets = [e for e in awaiting(data) if e.get("verdict") == "採用"]
    # decide() は1件ずつ読み書きするので、返ってきた側を使う（手元の dict は
    # まだ screened のままで、status を見ると嘘になる）。
    return [
        decide(e["node"], e["parm"], "approved",
               "無人モードが判定だけで承認した（絵は誰も見ていない）", by="auto")
        for e in targets
    ]


def approved(data: dict | None = None) -> list[dict]:
    """本撮り待ち（承認済みでまだ撮っていない）を返す。"""
    rows = [
        e for e in (data or load()).get("entries", {}).values()
        if e.get("status") == "approved"
    ]
    rows.sort(key=lambda e: (e.get("node", ""), e.get("parm", "")))
    return rows


def mark_shot(node: str, parm: str, out: str) -> dict:
    """本撮りが終わったことを記録する。**公開したとは言っていない。**

    ここを approved のままにすると `review.py --approved` が同じものを
    「本撮りするならこれ」と出し続ける。撮影は機械が回すが、記事を書いて
    公開するのは人なので、両者の間に状態が要る。
    """
    data = load()
    entry = data.get("entries", {}).get(key_of(node, parm))
    if entry is None:
        raise KeyError(f"検証リストにありません: {key_of(node, parm)}")

    entry["status"] = "shot"
    entry["out"] = out
    entry["shot_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(data)
    return entry


def mark_watched(node: str, parm: str, note: str = "") -> dict:
    """撮れた動画を人が見て「使える」と決めたことを記録する。

    **状態は `shot` のまま。**動画が使えることと記事になったことは別で、
    公開は記事を書いて push して初めて成る（そこで `publish`）。ここで
    増やすのは「もう見た」という印だけ。`/watch/` はこの印の無いものを並べる。
    """
    data = load()
    entry = data.get("entries", {}).get(key_of(node, parm))
    if entry is None:
        raise KeyError(f"検証リストにありません: {key_of(node, parm)}")
    if entry.get("status") != "shot":
        raise SystemExit(
            f"{key_of(node, parm)} は撮影済みではありません（status={entry.get('status')}）"
        )

    entry["watched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    entry["watch_note"] = note
    save(data)
    return entry


def unwatched(data: dict | None = None) -> list[dict]:
    """撮れたが、まだ人が動画を見ていないもの。"""
    rows = [
        e for e in (data or load()).get("entries", {}).values()
        if e.get("status") == "shot" and not e.get("watched_at")
    ]
    rows.sort(key=lambda e: (e.get("shot_at") or "", e.get("parm", "")))
    return rows


def retry(node: str, parm: str, note: str, overrides: dict | None = None) -> dict:
    """やり直し。**提案だけを捨てて、候補は生かす。**

    却下との違いはここ。「この候補に価値がない」のではなく「この刻み・この
    範囲が悪い」という指摘なので、`pending` に戻して撮り直させる。

    **前の判定は retry.previous に畳んで、上の階層からは消す。**status が
    pending なのに verdict が「採用」のまま残っていると、検証リストを読んだとき
    どちらが今の事実なのか分からなくなる。
    """
    data = load()
    entry = data.get("entries", {}).get(key_of(node, parm))
    if entry is None:
        raise KeyError(f"検証リストにありません: {key_of(node, parm)}")

    record = entry.setdefault("retry", {})
    previous = {
        key: entry.pop(key)
        for key in ("verdict", "reason", "values", "vs_default_db",
                    "neighbour_db", "broken", "screened_at")
        if key in entry
    }
    if previous:
        record["previous"] = previous

    record["note"] = note
    record["count"] = record.get("count", 0) + 1
    record["overrides"] = {**(record.get("overrides") or {}), **(overrides or {})}
    record["at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    entry["status"] = "pending"
    entry.pop("decision_note", None)
    entry.pop("decided_by", None)
    entry.pop("decided_at", None)
    # 撮影済みから撮り直すときは「撮った」「見た」も畳む。**out は残す**
    # （同じ動画 ID で撮り直すので、記事のリンクを付け替えずに済む）。
    record["previous_shot"] = {
        key: entry.pop(key) for key in ("shot_at", "watched_at", "watch_note")
        if key in entry
    } or None
    if record["previous_shot"] is None:
        del record["previous_shot"]
    save(data)
    return entry


def reopen(node: str, parm: str) -> dict:
    """却下を取り消す。

    **決定を消すのであって、判定は消さない。**判定に戻すだけなので、
    もう一度 `/review/` に並んで人の決定を待つ状態になる。
    """
    data = load()
    entry = data.get("entries", {}).get(key_of(node, parm))
    if entry is None:
        raise KeyError(f"検証リストにありません: {key_of(node, parm)}")
    if entry.get("status") not in ("rejected", "approved"):
        raise SystemExit(
            f"{key_of(node, parm)} は決定済みではありません"
            f"（status={entry.get('status')}）"
        )

    entry["status"] = "screened"
    entry.pop("decision_note", None)
    entry.pop("decided_at", None)
    entry.pop("approved_values", None)
    save(data)
    return entry


def resolve(parm: str, node: str | None = None) -> tuple[str, str]:
    """`--parm` だけで指せるようにする（同名が複数あるときだけ --node を要求）。"""
    entries = load().get("entries", {})
    hits = [
        e for e in entries.values()
        if e.get("parm") == parm and (node is None or e.get("node") == node)
    ]
    if not hits:
        raise SystemExit(f"検証リストにありません: {parm}" + (f"（{node}）" if node else ""))
    if len(hits) > 1:
        nodes = ", ".join(sorted(e["node"] for e in hits))
        raise SystemExit(f"{parm} は複数のノードにあります。--node で指定してください: {nodes}")
    return hits[0]["node"], hits[0]["parm"]


def record_error(node: str, parm: str, message: str) -> None:
    """撮影が失敗したことを残す。

    **status は pending のままにする。** 時間切れやダイアログは候補そのものの
    性質ではないので、一度の失敗で永久に捨てるのは強すぎる。代わりに回数を
    数えて、ループ側が「何度も失敗するものは飛ばす」判断に使う。
    """
    data = load()
    entry = data.setdefault("entries", {}).setdefault(
        key_of(node, parm), {"node": node, "parm": parm, "status": "pending"},
    )
    entry["error_count"] = entry.get("error_count", 0) + 1
    entry["last_error"] = message[:500]
    entry["last_error_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(data)


def cmd_list(args: argparse.Namespace) -> int:
    data = load()
    entries = data.get("entries", {})
    if not entries:
        print("検証リストは空です。`ledger.py add` で候補を積んでください。")
        return 0

    rows = [e for e in entries.values() if not args.status or e.get("status") == args.status]
    rows.sort(key=lambda e: (e.get("status", ""), e.get("node", ""), e.get("parm", "")))

    by_status: dict[str, int] = {}
    for entry in entries.values():
        by_status[entry.get("status", "?")] = by_status.get(entry.get("status", "?"), 0) + 1

    print(f"検証リスト: {LEDGER}  （更新 {data.get('updated', '?')}）")
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
    rows = pending(data)
    if not rows:
        print("未評価の候補はありません。")
        return 0

    picked = rows[: args.count]

    print(f"未評価 {len(rows)} 件のうち {len(picked)} 件:")
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
    print("  そのあと tools\\screen.py で判定 → 検証リストに自動で書き戻ります。")
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


def cmd_awaiting(args: argparse.Namespace) -> int:
    """人の決定を待っている候補を出す。"""
    rows = awaiting()
    if not rows:
        print("承認待ちはありません。"
              "（`screen_loop.py` を回すと溜まります）")
        return 0

    print(f"承認待ち {len(rows)} 件:")
    for entry in rows:
        proposal = entry.get("proposal") or {}
        display = proposal.get("display_values") or entry.get("values") or []
        print(f"\n  {entry['parm']}  （{entry.get('label', '')}）  [{entry.get('verdict', '')}]")
        print(f"      {entry['node']}")
        print(f"      {entry.get('reason', '')}")
        if display:
            print(f"      実効値 {', '.join(str(v) for v in display)}")
    print(f"\n  絵を見て決める: {config.BASE_URL}/review/")
    print("  コマンドで決める: ledger.py approve --parm <名前>  /  reject --parm <名前> --why \"...\"")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    """まとめて承認・却下する。"""
    status = args.decision
    if getattr(args, "auto", False):
        if status != "approved":
            raise SystemExit("--auto は approve でだけ使えます")
        rows = auto_approve()
        for entry in rows:
            print(f"  自動承認: {entry['node']} / {entry['parm']}")
        held = [e["parm"] for e in awaiting()]
        print(f"{len(rows)} 件を承認しました（機械の判定だけ）")
        if held:
            print(f"  人の判断に回したもの: {', '.join(held)}")
        return 0

    if args.all:
        targets = [(e["node"], e["parm"]) for e in awaiting()]
        if not targets:
            print("承認待ちはありません。")
            return 0
    else:
        if not args.parm:
            raise SystemExit("--parm か --all を指定してください")
        names = [p.strip() for p in ",".join(args.parm).split(",") if p.strip()]
        targets = [resolve(name, args.node) for name in names]

    # **却下には理由を要る。**「なぜ落としたか」が残っていないと、次に
    # 同じ候補を見たときに判断をやり直すことになる（検証リストの存在理由そのもの）。
    if status == "rejected" and not args.why:
        raise SystemExit("却下には --why で理由を書いてください")

    for node, parm in targets:
        decide(node, parm, status, args.why or "")
        print(f"  {'承認' if status == 'approved' else '却下'}: {node} / {parm}")

    if status == "rejected":
        import review
        review.prune()
    print(f"{len(targets)} 件を {status} にしました")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    """やり直しを指示する。"""
    if not args.why:
        raise SystemExit("やり直しには --why で「何が悪いか」を書いてください")

    overrides = {
        key: value
        for key, value in (
            ("cap", args.cap), ("min", args.min),
            ("stages", args.stages), ("per_decade", args.per_decade),
        )
        if value is not None
    }
    names = [p.strip() for p in ",".join(args.parm or []).split(",") if p.strip()]
    if not names:
        raise SystemExit("--parm を指定してください")

    for name in names:
        node, parm = resolve(name, args.node)
        retry(node, parm, args.why, overrides)
        print(f"  やり直し: {node} / {parm}")
    if overrides:
        print(f"  次の提案に効かせる指示: {overrides}")
    print(f"{len(names)} 件を pending に戻しました"
          "（screen_loop.py が最優先で拾います）")
    return 0


def cmd_reopen(args: argparse.Namespace) -> int:
    """決定を取り消して、承認待ちに戻す。"""
    names = [p.strip() for p in ",".join(args.parm or []).split(",") if p.strip()]
    if not names:
        raise SystemExit("--parm を指定してください")
    for name in names:
        node, parm = resolve(name, args.node)
        reopen(node, parm)
        print(f"  取り消し: {node} / {parm}")
    print(f"{len(names)} 件を承認待ちに戻しました")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """公開したことを記録する。"""
    data = load()
    node, parm = resolve(args.parm, args.node) if not args.node else (args.node, args.parm)
    key = key_of(node, parm)
    entry = data.get("entries", {}).get(key)
    if entry is None:
        raise SystemExit(f"検証リストにありません: {key}")

    # **--out は省略できる。** shoot.py が撮った時点で out を書いてあるので、
    # 公開するときに動画 ID を人が打ち直す必要はない（打ち間違えると
    # 検証リストと記事が食い違う）。
    out = args.out or entry.get("out")
    if not out:
        raise SystemExit(
            f"{key} には out がありません。--out で動画 ID を指定してください。"
        )

    entry["status"] = "published"
    entry["out"] = out
    entry["published_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(data)
    print(f"{key} を published にしました（{out}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="検証リスト")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="ノードのパラメータを候補として積む")
    p_add.add_argument("--hip", required=True)
    p_add.add_argument("--node", action="append", required=True)
    p_add.add_argument("--filter", help="内部名・ラベル・説明への絞り込み（正規表現）")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="検証リストの中身を表示する")
    p_list.add_argument("--status", choices=STATUSES)
    p_list.set_defaults(func=cmd_list)

    p_next = sub.add_parser("next", help="次に調べる候補を出す")
    p_next.add_argument("--count", type=int, default=3)
    p_next.set_defaults(func=cmd_next)

    p_await = sub.add_parser("awaiting", help="人の決定を待っている候補を出す")
    p_await.set_defaults(func=cmd_awaiting)

    for name, help_text in (
        ("approve", "本撮りしてよいと決める"),
        ("reject", "撮らないと決める（--why で理由を残す）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--parm", action="append",
                       help="パラメータ名。カンマ区切りと複数指定ができる")
        p.add_argument("--node", help="同名が複数のノードにあるときだけ必要")
        p.add_argument("--all", action="store_true", help="承認待ちを全部")
        p.add_argument("--why", default="", help="決めた理由")
        if name == "approve":
            p.add_argument(
                "--auto", action="store_true",
                help="verdict が「採用」のものだけ機械が承認する（無人モード）",
            )
        # dest は cmd と別にする。サブパーサ名（approve）と記録する状態
        # （approved）が違ううえ、argparse がどちらで上書きするかに
        # 頼りたくない。
        p.set_defaults(
            func=cmd_decide, decision="approved" if name == "approve" else "rejected",
        )

    p_retry = sub.add_parser(
        "retry", help="提案が悪いので撮り直させる（候補は生かす）",
    )
    p_retry.add_argument("--parm", action="append")
    p_retry.add_argument("--node")
    p_retry.add_argument("--why", default="", help="何が悪いか（必須）")
    p_retry.add_argument("--cap", type=float, help="ここより上は撮らない")
    p_retry.add_argument("--min", type=float, help="ここより下は撮らない")
    p_retry.add_argument("--stages", type=int, help="提案する段階数")
    p_retry.add_argument("--per-decade", type=int, choices=(1, 2, 3, 4),
                         help="1桁を何段に割るか")
    p_retry.set_defaults(func=cmd_retry)

    p_reopen = sub.add_parser("reopen", help="承認・却下を取り消して承認待ちに戻す")
    p_reopen.add_argument("--parm", action="append")
    p_reopen.add_argument("--node")
    p_reopen.set_defaults(func=cmd_reopen)

    p_pub = sub.add_parser("publish", help="公開したことを記録する")
    p_pub.add_argument("--node", help="同名が複数のノードにあるときだけ必要")
    p_pub.add_argument("--parm", required=True)
    p_pub.add_argument("--out", help="動画 ID（既定は撮影時に記録したもの）")
    p_pub.set_defaults(func=cmd_publish)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
