"""無人モード。**外出中に篩から下書きまで通す。**

  python tools/unattended.py --hip scenes/vellum_cloth.hip --hours 4
  python tools/unattended.py --hip scenes/vellum_cloth.hip --count 6 --dry-run

  screen_loop.py  候補を篩にかけて判定する
        ↓
  自動承認        verdict が「採用」のものだけ（ledger.auto_approve）
        ↓
  shoot.py        承認済みを1回の起動でまとめて本撮り → shot
        ↓
  draft.py        撮影済みから記事の下書きを作る（公開はされない）

帰宅後は **`/watch/` で動画を見て**、公開・撮り直し・却下を決める。

## 人の判断を機械に肩代わりさせない線引き

自動で通すのは verdict が「採用」のものだけ。「採用（段階に無駄あり）」と
「破綻」は `screened` のまま残り、帰宅後に `/review/` で静止画を見て決める。
**機械が勝手に捨てはしない。**

記事も同じで、下書きは `content/drafts/` に入って `config.SHOW_DRAFTS` の
無いビルドからは落ちる。**push しても公開されない。**人が動画を見て言葉を
足し、本体の記事へ移して初めて公開される。

## 止まり方

- `--hours` を過ぎたら**新しい候補を始めない**（走っている本撮りは終わらせる）
- `--count` まで篩にかけたら終わる
- 途中で失敗しても次へ進む。失敗は検証リストに `error_count` として残る

**撮影中は `/review/` で承認しないこと。** 検証リストはファイル1本を丸ごと
読み書きするので、後から保存したほうが勝って片方が消える。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent

# **行ごとに吐く。** 無人モードの出力はファイルに落として後から読むもので、
# 既定のブロックバッファのままだと子プロセス（screen_loop / shoot）の出力が
# 先に出て、どの見出しの下で何が起きたのか読めなくなる。
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:                                   # 3.6 以前
    pass


def last_camera() -> str | None:
    """このシーンで実際に使ってきたカメラ。**既定に頼らない。**

    `config.DEFAULT_CAMERA` は `CAM_main` だが、布は `CAM_angle` で撮る。
    無人モードは何時間も無人で回るので、画角を取り違えたまま回ると
    そのぶん丸ごと無駄になる。過去の提案が使ったカメラを既定にする。
    """
    seen: dict[str, int] = {}
    for entry in ledger.load().get("entries", {}).values():
        camera = (entry.get("proposal") or {}).get("camera")
        if camera:
            seen[camera] = seen.get(camera, 0) + 1
    return max(seen, key=seen.get) if seen else None


class Tee:
    """端末とログファイルの両方に書く。

    **ログはファイルに残す。** 無人モードの出力は後から読むもので、
    端末のスクロールバックに頼ると、閉じた時点で消える。UTF-8 で書くのは
    PowerShell の `*>` に任せると環境の文字コード次第で化けるため。

    子プロセス（screen_loop / shoot）の出力は親が1行ずつ読んで `print` する
    ので、そちらも同じファイルに入る。
    """

    def __init__(self, stream, path: Path):
        self.stream = stream
        self.file = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, text: str) -> int:
        self.stream.write(text)
        self.file.write(text)
        return len(text)

    def flush(self) -> None:
        self.stream.flush()
        self.file.flush()


def run(name: str, cmd: list[str], log: Path | None = None) -> int:
    """子プロセスを回して終了コードを返す。**落ちても次へ進む。**"""
    print(f"\n{'=' * 60}\n  {name}\n{'=' * 60}")
    sys.stdout.flush()
    try:
        if log is None:
            return subprocess.run(cmd).returncode
        # **子の出力もログに入れる。** 撮影の進捗（flipbook.log の中身）は
        # ここにしか出ないので、取りこぼすと何分かかって何が失敗したのか
        # 後から分からない。
        #
        # **1行ずつ読んで流す。** まとめて受け取ると、screen_loop が数十分
        # 走っている間ターミナルが無音になり、止まっているのか進んでいるのか
        # 分からなくなる。
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with proc.stdout:
            for line in proc.stdout:
                print(line, end="")
        return proc.wait()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"  {name} が起動できませんでした: {type(exc).__name__}: {exc}")
        return 1


def elapsed(started: float) -> str:
    return f"{(time.time() - started) / 60:.0f} 分"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="無人モード（篩 → 自動承認 → 本撮り → 下書き）",
    )
    ap.add_argument("--hip", required=True)
    ap.add_argument("--camera",
                    help="既定は検証リストで一番使われているカメラ"
                         f"（無ければ {config.DEFAULT_CAMERA}）")
    ap.add_argument("--count", type=int, default=5, help="篩にかける候補の数（既定 5）")
    ap.add_argument("--hours", type=float, default=0,
                    help="これを過ぎたら新しい候補を始めない（0 で無制限）")
    ap.add_argument("--frames", default="1-48", help="本撮りのフレーム範囲")
    ap.add_argument("--no-shoot", action="store_true",
                    help="篩と自動承認だけ。本撮りはしない")
    ap.add_argument("--dry-run", action="store_true",
                    help="何を回すかだけ表示して、Houdini を起動しない")
    ap.add_argument("--log", metavar="PATH", nargs="?", const="unattended.log",
                    help="出力を UTF-8 でこのファイルにも書く"
                         "（値を省くと unattended.log）")
    args = ap.parse_args()

    # **ログは自前で書く。** PowerShell の `*>` に任せると環境の文字コード
    # 次第で日本語が化ける。出かける前に回すものなので、帰ってきて読めない
    # ログほど困るものはない。
    log_path = Path(args.log).resolve() if args.log else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = Tee(sys.stdout, log_path)

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンファイルがありません: {hip}")

    if not args.camera:
        args.camera = last_camera() or f"/obj/{config.DEFAULT_CAMERA}"
        print(f"カメラ: {args.camera}（過去の提案から。変えるなら --camera）")

    started = time.time()
    deadline = started + args.hours * 3600 if args.hours else None

    before = {
        "pending": len(ledger.pending(ledger.load(), types=ledger.LADDER_TYPES)),
        "awaiting": len(ledger.awaiting()),
        "approved": len(ledger.approved()),
    }
    print(f"\n無人モード開始 {time.strftime('%Y-%m-%d %H:%M')}"
          f"（未評価 {before['pending']} 件 / 承認待ち {before['awaiting']} 件）")
    if log_path:
        print(f"  ログ: {log_path}")
    if deadline:
        print(f"  {time.strftime('%H:%M', time.localtime(deadline))} を過ぎたら"
              "新しい候補を始めません")
    if args.dry_run:
        print("  --dry-run: Houdini は起動しません")

    # --- 1. 篩にかける -------------------------------------------------------
    loop = [
        sys.executable, str(TOOLS_DIR / "screen_loop.py"),
        "--hip", str(hip), "--count", str(args.count), "--camera", args.camera,
    ]
    if args.dry_run:
        loop.append("--dry-run")
    run(f"篩にかける（{args.count} 件）", loop, log_path)

    # --- 2. 自動承認 ---------------------------------------------------------
    # **ここは機械の判定だけで決める。**「採用」以外は screened のまま残して
    # 帰宅後の判断に回す（ledger.auto_approve のコメント参照）。
    print(f"\n{'=' * 60}\n  自動承認（verdict が「採用」のものだけ）\n{'=' * 60}")
    if args.dry_run:
        ready = [e["parm"] for e in ledger.awaiting() if e.get("verdict") == "採用"]
        held = [e["parm"] for e in ledger.awaiting() if e.get("verdict") != "採用"]
        print(f"  承認する: {', '.join(ready) or 'なし'}")
        print(f"  人に回す: {', '.join(held) or 'なし'}")
    else:
        approved = ledger.auto_approve()
        for entry in approved:
            print(f"  自動承認: {entry['parm']}  （{entry.get('reason', '')}）")
        held = [e for e in ledger.awaiting()]
        print(f"  {len(approved)} 件を承認、{len(held)} 件は人の判断に回しました")
        for entry in held:
            print(f"      保留: {entry['parm']}  [{entry.get('verdict', '')}]")

    # --- 3. 本撮り -----------------------------------------------------------
    if args.no_shoot:
        print("\n--no-shoot なので本撮りはしません。")
    elif deadline and time.time() > deadline and not args.dry_run:
        # 時間切れでも**本撮りには入らない**。走り出すと1本あたり数十分かかり、
        # 「帰ってきたらまだ回っていた」ことになる。承認済みは残るので、
        # 次に shoot.py を叩けば撮れる。
        print(f"\n時間切れ（{elapsed(started)}）なので本撮りには入りません。"
              "  承認済みは残っています: tools\\shoot.py")
    else:
        shoot = [sys.executable, str(TOOLS_DIR / "shoot.py"), "--frames", args.frames]
        if args.dry_run:
            shoot.append("--dry-run")
        run("承認済みをまとめて本撮り", shoot, log_path)

        # --- 4. 下書き -------------------------------------------------------
        drafts = [sys.executable, str(TOOLS_DIR / "draft.py")]
        if not args.dry_run:
            run("撮影済みから記事の下書きを作る", drafts, log_path)

    # --- まとめ -------------------------------------------------------------
    after_await = ledger.awaiting()
    shot = [e for e in ledger.load().get("entries", {}).values()
            if e.get("status") == "shot"]

    print(f"\n{'=' * 60}\n  {elapsed(started)}\n{'=' * 60}")
    print(f"  撮影済み・未公開   {len(shot)} 件"
          + (f"  ({', '.join(e['parm'] for e in shot)})" if shot else ""))
    print(f"  承認待ち（静止画） {len(after_await)} 件"
          + (f"  ({', '.join(e['parm'] for e in after_await)})" if after_await else ""))
    print(f"  本撮り待ち         {len(ledger.approved())} 件")
    print("\n帰宅後にやること:")
    print(f"  動画を見て決める : {config.BASE_URL}/watch/")
    print(f"  静止画の承認     : {config.BASE_URL}/review/")
    print(f"  下書きを読む     : {config.BASE_URL}/")
    print("  （どれも serve.py を起動しておくこと）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
