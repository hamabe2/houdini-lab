"""承認済みをまとめて本撮りする。**`approved → shot` のドライバ。**

  python tools/shoot.py                  # 承認済みを全部撮る
  python tools/shoot.py --dry-run        # 何を撮るかだけ見る（Houdini を起動しない）
  python tools/shoot.py --parm niter --out niter=vellum-cloth-niter

## なぜ要るか

`review.py --approved` が出すのは**人が読んで打ち込むためのコマンド**で、
`--out <id>` はプレースホルダのまま。承認が3件あれば3回貼り付けて3回
Houdini を起動することになる。起動と hip 読み込みは1本あたり数十秒かかり、
これは sim と違って削れる時間。

`flipbook.py --sweep` は1回の起動で複数本を撮れるので、承認済みのぶんを
組み立てて渡すだけでよい。

## 撮ったあと

**`shot`（撮影済み・未公開）に進める。**approved のままにすると
`review.py --approved` が同じものを出し続け、本数が増えるほど何が残って
いるのか分からなくなる。記事に載せたら `ledger.py publish` で published へ。

## 動画 ID の決め方

既定は `<hip のファイル名>-<パラメータ名>`（`vellum-cloth-benddampingratio`）。
機械的に決まって衝突しないことを優先している。`--out <parm>=<id>` で
1件ずつ上書きできる。**ID は mp4 / JSON のファイル名になり、記事に
`:::compare <id>` と書く名前**でもあるので、後から変えると記事も直すことになる。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402
from _signals import handle_break  # noqa: E402
from review import fmt_value  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent
FLIPBOOK = TOOLS_DIR / "flipbook.py"
RUN_DIR = config.CACHE_DIR / "shots" / "_shoot"


def slug_id(text: str) -> str:
    """out= に渡せる形にする（英小文字・数字・ハイフンだけ）。"""
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return re.sub(r"-{2,}", "-", out)


def default_out(entry: dict) -> str:
    """動画 ID の既定。シーン名 + パラメータ名。"""
    hip = entry.get("hip") or ""
    stem = slug_id(Path(hip).stem) if hip else "shot"
    return f"{stem}-{slug_id(entry['parm'])}"


def camera_of(entry: dict) -> str:
    """撮ったときと同じ画角。**判定した絵と本撮りが違う画角では意味がない。**"""
    return (entry.get("proposal") or {}).get("camera") or f"/obj/{config.DEFAULT_CAMERA}"


def build_sweep(entry: dict, out: str) -> str:
    """`flipbook.py --sweep` の1件を組み立てる。

    **承認したときの値をそのまま使う。**`approved_values` に凍らせてあるので、
    あとで `propose_values.py` を回し直しても撮るものは動かない。
    """
    approved = entry.get("approved_values") or {}
    values = approved.get("values") or entry.get("values") or []
    if not values:
        raise SystemExit(
            f"{entry['parm']}: 撮る値がありません"
            "（承認し直すか、ledger.py reopen で戻してください）"
        )

    label = approved.get("label") or entry.get("label") or entry["parm"]
    # --sweep は ';' と '=' で区切るので、ラベルに入っていると黙って壊れる
    # （label が途中で切れ、残りが知らないキーとして弾かれる）。
    if ";" in label or "=" in label:
        raise SystemExit(
            f"{entry['parm']}: ラベルに ';' か '=' が入っていて --sweep に渡せません: {label!r}"
        )

    text = (
        f"{entry['node']}:{entry['parm']}={','.join(fmt_value(v) for v in values)}"
        f";out={out};label={label}"
    )
    index = approved.get("default_index")
    if index is not None:
        text += f";default={index}"
    return text


def plan(rows: list[dict], overrides: dict[str, str]) -> list[dict]:
    """撮る対象に動画 ID を割り当て、重複を弾く。"""
    jobs = []
    for entry in rows:
        out = overrides.get(entry["parm"]) or default_out(entry)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", out):
            raise SystemExit(
                f"動画 ID は英小文字・数字・ハイフンだけにしてください: {out!r}"
            )
        jobs.append({"entry": entry, "out": out})

    ids = [job["out"] for job in jobs]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SystemExit(
            f"動画 ID が重複しています: {', '.join(dupes)}\n"
            "  --out <parm>=<id> で別々の ID を付けてください。"
        )
    return jobs


def existing(jobs: list[dict]) -> list[str]:
    """上書きしてよくない動画 ID を返す。

    **同じ候補が前に撮った ID は黙って上書きしてよい。**`/watch/` で
    「撮り直し」を選ぶと同じ ID のまま提案からやり直すので、そこで毎回
    `--overwrite` を要求すると撮り直しが通らない。止めるのは**別の候補が
    使っている ID** を潰そうとしたときだけ。
    """
    return [
        job["out"] for job in jobs
        if (config.MEDIA_DIR / f"{job['out']}.mp4").exists()
        and job["entry"].get("out") != job["out"]
    ]


def group_key(entry: dict) -> tuple[str, str]:
    """1回の GUI セッションで撮れる単位。

    `flipbook.py` の job は hip とカメラを1つずつしか持たない（セッション中に
    シーンを開き直したりビューを組み直したりはしない）。違うものが混ざって
    いたらセッションを分ける。
    """
    return (entry.get("hip") or "", camera_of(entry))


def run_session(
    jobs: list[dict], hip: str, camera: str, args: argparse.Namespace, tag: str,
) -> tuple[list[str], list[tuple[str, str]]]:
    """1回の起動で撮る。撮れた out と、失敗した (out, 理由) を返す。"""
    report = RUN_DIR / f"{tag}.json"
    cmd = [
        sys.executable, str(FLIPBOOK),
        "--hip", hip,
        "--frames", args.frames,
        "--camera", camera,
        "--report", str(report),
        "--timeout", str(args.timeout),
    ]
    if args.show_window:
        cmd.append("--show-window")
    if args.keep_frames:
        cmd.append("--keep-frames")
    for job in jobs:
        cmd += ["--sweep", build_sweep(job["entry"], job["out"])]

    print(f"\n=== {len(jobs)} 本を1回の起動で撮ります（{Path(hip).name} / {camera}）===")
    subprocess.run(cmd)

    # **終了コードでは決めない。** flipbook.py は一部が失敗しても残りを mp4 に
    # して 1 を返すので、「1 なら全滅」と読むと撮れた本まで撮り直すことになる。
    if not report.exists():
        return [], [(job["out"], "flipbook.py が結果を残しませんでした") for job in jobs]

    result = json.loads(report.read_text(encoding="utf-8"))
    if result.get("draft"):
        raise SystemExit("draft の結果は本撮りとして記録できません")
    failed = [(f["out"], f["reason"]) for f in result.get("failed", [])]
    return result.get("ok", []), failed


def main() -> int:
    handle_break()   # Ctrl+Break でも Houdini を残さない

    ap = argparse.ArgumentParser(
        description="承認済みをまとめて本撮りする（approved → shot）",
    )
    ap.add_argument("--parm", action="append",
                    help="この候補だけ撮る（カンマ区切りと複数指定ができる）")
    ap.add_argument("--node", help="同名が複数のノードにあるときだけ必要")
    ap.add_argument("--count", type=int, help="上から N 件だけ撮る")
    ap.add_argument("--out", action="append", default=[], metavar="PARM=ID",
                    help="動画 ID を指定する（既定は <シーン名>-<パラメータ名>）")
    ap.add_argument("--frames", default="1-48", help="フレーム範囲（既定 1-48）")
    ap.add_argument("--overwrite", action="store_true",
                    help="すでに media にある動画 ID を上書きする")
    ap.add_argument("--dry-run", action="store_true",
                    help="何を撮るかだけ表示して、Houdini を起動しない")
    ap.add_argument("--show-window", action="store_true")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--timeout", type=float, default=config.FLIPBOOK_TIMEOUT_MIN)
    args = ap.parse_args()

    overrides = {}
    for text in args.out:
        parm, sep, value = text.partition("=")
        if not sep:
            raise SystemExit(f"--out は <parm>=<id> の形で指定してください: {text!r}")
        overrides[parm.strip()] = value.strip()

    rows = ledger.approved()
    if args.parm:
        names = [p.strip() for p in ",".join(args.parm).split(",") if p.strip()]
        wanted = {ledger.resolve(name, args.node) for name in names}
        rows = [e for e in rows if (e["node"], e["parm"]) in wanted]
    if not rows:
        print("本撮りする承認済みはありません。"
              f"（絵を見て決める: {config.BASE_URL}/review/）")
        return 0
    if args.count:
        rows = rows[: args.count]

    jobs = plan(rows, overrides)

    unknown = set(overrides) - {job["entry"]["parm"] for job in jobs}
    if unknown:
        raise SystemExit(
            f"--out に承認済みでない候補があります: {', '.join(sorted(unknown))}"
        )

    clash = existing(jobs)
    if clash and not args.overwrite:
        raise SystemExit(
            f"すでに media にある動画 ID です: {', '.join(clash)}\n"
            "  撮り直すなら --overwrite、別名にするなら --out <parm>=<id> を使ってください。"
        )

    print(f"本撮りする {len(jobs)} 件:")
    for job in jobs:
        entry = job["entry"]
        approved = entry.get("approved_values") or {}
        values = approved.get("values") or entry.get("values") or []
        display = approved.get("display_values") or []
        print(f"  {job['out']}")
        print(f"      {entry['node']} / {entry['parm']}"
              f"  （{approved.get('label') or entry.get('label', '')}）")
        print(f"      値 {', '.join(fmt_value(v) for v in values)}"
              + (f"  → 実効値 {', '.join(str(v) for v in display)}" if display else ""))
        if entry.get("decision_note"):
            print(f"      承認メモ: {entry['decision_note']}")
    if clash:
        print(f"  （上書きします: {', '.join(clash)}）")

    sessions: dict[tuple[str, str], list[dict]] = {}
    for job in jobs:
        sessions.setdefault(group_key(job["entry"]), []).append(job)
    if len(sessions) > 1:
        print(f"\nシーンか画角が違うので {len(sessions)} 回に分けて起動します。")

    if args.dry_run:
        print("\n--dry-run なのでここまで。")
        return 0

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    done: list[dict] = []
    failed: list[tuple[str, str]] = []

    stopped = False
    try:
        for i, ((hip, camera), group) in enumerate(sessions.items(), 1):
            if not hip:
                failed += [(job["out"], "検証リストに hip がありません") for job in group]
                continue

            ok, bad = run_session(group, hip, camera, args, tag=f"s{i}")
            failed += bad

            # **1セッション終わるごとに書き戻す。** 途中で止めても、撮れた分が
            # 「撮ったのに approved のまま」で残らないようにする。
            for job in group:
                if job["out"] in ok:
                    ledger.mark_shot(
                        job["entry"]["node"], job["entry"]["parm"], job["out"],
                    )
                    done.append(job)
    except KeyboardInterrupt:
        # 撮りかけの本は approved のまま残る（次に叩けばまた撮れる）。
        # Houdini は flipbook.launch() の finally が終わらせている。
        stopped = True
        print("\n中断（Ctrl+C）。撮り終えた本だけ記録しました。")

    print(f"\n=== {len(done)} / {len(jobs)} 本 / {(time.time() - started) / 60:.1f} 分 ===")
    for job in done:
        print(f"  [撮影済み] {job['entry']['parm']:<26} {job['out']}")
    for out, reason in failed:
        print(f"  [失敗    ] {out}: {reason.splitlines()[0]}")

    if done:
        print("\n記事に書くとき:")
        for job in done:
            print(f"  :::compare {job['out']}")
        print("\n公開したら記録する:")
        for job in done:
            print(f"  .venv\\Scripts\\python.exe tools\\ledger.py publish "
                  f"--parm {job['entry']['parm']}")
    return 130 if stopped else (0 if not failed else 1)


if __name__ == "__main__":
    raise SystemExit(main())
