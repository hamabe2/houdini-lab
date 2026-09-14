"""候補を N 件まとめて篩にかける。**`next → propose → sheet → screen` のドライバ。**

  python tools/screen_loop.py --hip scenes/vellum_cloth.hip --count 3

検証リストから未評価の候補を順に取り、値の刻みを決め、判定して検証リストに書き戻す。
人が介在するのは最後の「採用されたものを本撮りするか」だけになる。

## なぜ要るか

1件ごとに `ledger.py next` を読み、値を決めて `setup_sheet.py` に打ち込み、
`screen.py` を叩く、という手順を人がやっていた。**判断していない工程が
人の手番になっている。**刻みは `propose_values.py` が決め、採用可否は
`screen.py` が決めるので、間をつなぐだけなら機械でよい。

## 1件あたりの流れ

  1. 検証リストから次の候補を取る（Float / Int のみ。後述）
  2. `propose_values.propose()` で端を探し、段階を決める
  3. 決まった段階の絵を setup シートに積む
  4. `screen.py` の判定にかけ、検証リストへ書き戻す

**3 で撮り直さない。** 段階は梯子の段から選ばれるので、提案した値の絵は
2 で既に撮れている。ここで `setup_sheet.py` を別に起動すると、同じ値の sim を
もう一度回すことになる（1件あたり5値ぶん）。

## この loop が扱わないもの

- **Toggle / メニュー。** 梯子は既定値に 10^k を掛けて作るので、数値でないと
  伸ばせない。候補としては検証リストに残す（0/1 を並べる価値はある）が、ここでは
  飛ばす。`ledger.LADDER_TYPES` がその線引き。
- **撮る価値があるかの判断。** 判定（verdict）は「絵が変わるか」しか見ていない。
  採用されたものは `screened` のまま承認待ちに積み、`/review/` か
  `ledger.py approve` で人が決める。本撮りのコマンドはそのあと
  `review.py --approved` が出す。
"""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402
import propose_values  # noqa: E402
import review  # noqa: E402
import screen  # noqa: E402
from flipbook import DEFAULT_HIDDEN  # noqa: E402
from propose_values import DECADES  # noqa: E402
from setup_sheet import SHEET_DIR, write_sheet  # noqa: E402

RUN_DIR = config.CACHE_DIR / "shots" / "_loop"

# 同じ候補で何回失敗したら飛ばすか。時間切れやダイアログは候補そのものの
# 性質ではないので1回では捨てないが、毎回同じもので詰まるのも無駄。
MAX_ERRORS = 2


def elapsed(started: float) -> str:
    return f"{(time.time() - started) / 60:.1f} 分"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="候補を N 件まとめて篩にかける（propose → sheet → screen）",
    )
    ap.add_argument("--hip", required=True)
    ap.add_argument("--count", type=int, default=3, help="回す件数（既定 3）")
    ap.add_argument(
        "--node", action="append",
        help="このノードの候補だけを回す（複数指定可。既定は検証リストの全ノード）",
    )
    ap.add_argument(
        "--max-errors", type=int, default=MAX_ERRORS,
        help=f"これ以上失敗している候補は飛ばす（既定 {MAX_ERRORS}）",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="何を回すかだけ表示して、Houdini を起動しない",
    )

    # 以下は propose_values.py にそのまま渡す。
    ap.add_argument("--frame", type=int, default=24)
    ap.add_argument("--stages", type=int, default=5)
    ap.add_argument("--decades", type=int, default=DECADES)
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--cap", type=float)
    ap.add_argument("--per-decade", type=int, default=1, choices=(1, 2, 3, 4))
    ap.add_argument("--same-db", type=float, default=screen.SAME_DB)
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument("--width", type=int, default=config.VIDEO_WIDTH)
    ap.add_argument("--height", type=int, default=config.VIDEO_HEIGHT)
    ap.add_argument("--aa", type=int, default=8, choices=(1, 2, 4, 8, 16, 32))
    ap.add_argument("--hide", default=",".join(DEFAULT_HIDDEN))
    ap.add_argument("--show-window", action="store_true")
    ap.add_argument("--timeout", type=float, default=config.FLIPBOOK_TIMEOUT_MIN)
    args = ap.parse_args()

    # propose_values.load_meta() が見る上書き。ループでは候補ごとに違うので
    # 使わない（検証リストの値をそのまま信じる）。
    args.default = None
    args.min = None

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンファイルがありません: {hip}")

    rows = ledger.pending(
        ledger.load(), types=ledger.LADDER_TYPES, max_errors=args.max_errors,
    )
    if args.node:
        wanted = set(args.node)
        rows = [e for e in rows if e.get("node") in wanted]
    if not rows:
        print("回せる候補がありません。"
              "（`ledger.py add` で積むか、--max-errors を上げてください）")
        return 0

    picked = rows[: args.count]
    print(f"未評価 {len(rows)} 件のうち {len(picked)} 件を回します"
          f"（frame {args.frame} / {args.camera}）")
    for i, entry in enumerate(picked, 1):
        folders = " / ".join(entry.get("folders") or []) or "(タブなし)"
        print(f"  {i}. {entry['node']} / {entry['parm']}"
              f"  （{entry.get('label', '')} — {folders}）")
        again = entry.get("retry") or {}
        if again:
            print(f"      やり直し{again.get('count', 1)}回目: {again.get('note', '')}")
            if again.get("overrides"):
                print(f"      指示: {again['overrides']}")
    if args.dry_run:
        print("\n--dry-run なのでここまで。")
        return 0

    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR, ignore_errors=True)

    started = time.time()
    cells: list[dict] = []
    done: list[dict] = []
    failed: list[tuple[dict, str]] = []
    record: dict = {}

    for i, entry in enumerate(picked, 1):
        node, parm = entry["node"], entry["parm"]
        print(f"\n=== [{i}/{len(picked)}] {node} / {parm} "
              f"（経過 {elapsed(started)}）===")
        one = time.time()

        # **やり直しの指示をこの候補にだけ効かせる。** args を直接書き換えると
        # 次の候補にも漏れる（--cap を1件のために下げたつもりが全部に効く）。
        again = entry.get("retry") or {}
        call_args = copy.copy(args)
        if again:
            print(f"  やり直し{again.get('count', 1)}回目: {again.get('note', '')}")
            for key, value in (again.get("overrides") or {}).items():
                if not hasattr(call_args, key):
                    print(f"  （知らない指示なので無視します: {key}）")
                    continue
                setattr(call_args, key, value)
                print(f"  指示を反映: {key} = {value}")

        try:
            result = propose_values.propose(
                hip, node, parm, call_args,
                work_root=RUN_DIR / f"c{i}", probe_dir=RUN_DIR / "probe" / f"c{i}",
            )
        except SystemExit as exc:          # 梯子が作れない・撮れ高が合わない等
            message = str(exc)
            print(f"  失敗: {message}")
            ledger.record_error(node, parm, message)
            failed.append((entry, message))
            continue
        except Exception as exc:           # 時間切れ、GUI のダイアログ、etc.
            message = f"{type(exc).__name__}: {exc}"
            print(f"  失敗: {message}")
            ledger.record_error(node, parm, message)
            failed.append((entry, message))
            continue

        propose_values.print_report(result, hip)
        propose_values.record_proposal(result)

        # **提案した段階の絵をそのままシートに積む（撮り直さない）。**
        # 1件ずつ積み直すのは、途中で止めてもそこまでの判定が残るようにするため。
        picked_cells = propose_values.stage_cells(result)
        cells += picked_cells
        write_sheet(cells, args.frame, hip)

        print()
        record = screen.screen_sheet(SHEET_DIR, args.same_db, verbose=False)
        ledger.merge_screen(Path(record["path"]))

        judged = next(
            (r for r in record["results"] if r["node"] == node and r["parm"] == parm),
            None,
        )
        if judged:
            # 判定表は今回の候補のぶんだけ出す。シートには前の候補も
            # 載っているが、それらは既に表示済み。
            screen.print_group(judged, picked_cells)
            done.append(judged)

            # **人の決定を待つものは絵を退避する。** setup シートは次の run で
            # 丸ごと消えるので、置いたままだと「判定は残っているのに絵が無い」
            # 状態になる。承認は撮影と同じ速さでは進まない。
            entry = ledger.load()["entries"][ledger.key_of(node, parm)]
            if ledger.is_awaiting(entry):
                review.keep(picked_cells, node, parm)
        print(f"  ここまで {elapsed(one)}")

    print(f"\n=== {len(picked)} 件 / {elapsed(started)} ===")
    for result in done:
        print(f"  [{result['verdict']:<14}] {result['parm']}  {result['reason']}")
    for entry, message in failed:
        print(f"  [失敗          ] {entry['parm']}  {message.splitlines()[0]}")

    print(f"\n  検証リスト: {ledger.LEDGER}")

    # **本撮りのコマンドはここでは出さない。** 撮る価値があるかは人が決める
    # 工程で、その入力口は /review/（と ledger.py approve）。
    waiting = ledger.awaiting()
    if waiting:
        print(f"\n承認待ち {len(waiting)} 件。絵を見て決める:")
        print(f"  {config.BASE_URL}/review/  （serve.py を起動しておくこと）")
        print("  まとめて承認するなら: .venv\\Scripts\\python.exe tools\\ledger.py approve --all")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
