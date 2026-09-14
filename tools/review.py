"""承認待ちの絵と一覧。**機械の判定と人の決定の境目。**

  python tools/review.py              # 承認待ちを一覧する（絵は /review/）

`screen.py` の判定（verdict）は「既定値と絵が変わるか」しか見ていない。
撮る価値があるかは人が決める。その判断に要る材料をここに集める。

## なぜ絵を別の場所に取っておくのか

判定に使った絵は `tools/_cache/setup/` にあるが、**次に
`setup_sheet.py` / `screen_loop.py` を回すと丸ごと消える**
（`write_sheet()` が `rmtree` する）。承認は撮影と同じ速さでは進まないので、
消える場所に置いたままだと「判定は残っているのに絵が無い」状態になる。

ここへ写しておけば、何本撮ったあとでも承認待ちの絵が残る。

## 置き方

  tools/_cache/review/<ノード>__<パラメータ>__<値>.png

`serve.py` の画像配信はディレクトリを掘らない（`Path(name).name` しか使わない）
ので、**平らに置いてファイル名で区別する。**
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402
from encode import composite_still  # noqa: E402

REVIEW_DIR = config.CACHE_DIR / "review"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-")


def image_name(node: str, parm: str, value) -> str:
    """値ごとのファイル名。**退避するときと探すときで同じ式を使う。**"""
    return f"{slug(node.strip('/').replace('/', '-'))}__{slug(parm)}__{slug(value)}.png"


def keep(cells: list[dict], node: str, parm: str) -> list[str]:
    """判定に使ったセルを承認待ちの絵として退避する。

    **合成してから置く。** flipbook の PNG は RGBA で背景が alpha=0。
    生のまま置くとページの地色が透けて、本番と違う絵を見て判断することになる。
    """
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    for cell in cells:
        name = image_name(node, parm, cell["value"])
        composite_still(Path(cell["path"]), REVIEW_DIR / name)
        names.append(name)
    return names


def images_for(entry: dict) -> list[dict]:
    """検証リストの1件に対応する絵を、値の順に並べて返す（無ければ空）。"""
    out = []
    for i, value in enumerate(entry.get("values") or []):
        name = image_name(entry["node"], entry["parm"], value)
        if not (REVIEW_DIR / name).is_file():
            continue
        display = (entry.get("proposal") or {}).get("display_values") or []
        out.append({
            "value": value,
            "display": display[i] if i < len(display) else value,
            "name": name,
            "is_default": value == entry.get("default"),
            "broken": (entry.get("broken") or {}).get(str(value), ""),
        })
    return out


def overrides_from_excluded(
    entry: dict, excluded: list,
) -> tuple[dict, list[float]]:
    """「この値は除く」印を、次の提案に効く指示に変える。

    **自由文は機械に効かない。**「1e6 で破綻している」という指摘は、値を
    指してもらって初めて `--cap` に変換できる。`screen.py` の破綻判定
    （速度が中央値の20倍）は目より鈍く、人が先に気づくことがある。

    段は梯子の段から選ばれるので、上限は**除いた値のひとつ下の段**にする。
    `clip()` は `v <= cap` で見るため、除いた値そのものを渡すとまた撮る。

    **効かせられなかった印は黙って捨てない。**`(overrides, 効かなかった値)`
    を返す。既定値そのものは指せない（上下を切る仕組みなので、既定を境に
    できない）し、梯子の端の外側も切りようがない。黙って無視すると
    「チェックしたのに次も同じ値が出てくる」ことになる。
    """
    proposal = entry.get("proposal") or {}
    previous = (entry.get("retry") or {}).get("previous") or {}
    ladder = sorted(
        float(v) for v in
        (proposal.get("ladder") or entry.get("values") or previous.get("values") or [])
    )
    marks = [float(v) for v in excluded]
    default = entry.get("default")
    default = float(default) if isinstance(default, (int, float)) else 0.0

    out: dict = {}
    ignored: list[float] = [v for v in marks if v == default]
    high = [v for v in marks if v > default]
    low = [v for v in marks if v < default]

    if high:
        below = [r for r in ladder if r < min(high)]
        if below:
            out["cap"] = below[-1]
        else:
            ignored += high
    if low:
        above = [r for r in ladder if r > max(low)]
        if above:
            out["min"] = above[0]
        else:
            ignored += low
    return out, sorted(set(ignored))


def awaiting() -> list[dict]:
    """承認待ちを、絵と判定の数字を添えて返す。"""
    rows = []
    for entry in ledger.awaiting():
        row = dict(entry)
        row["key"] = ledger.key_of(entry["node"], entry["parm"])
        row["images"] = images_for(entry)
        rows.append(row)
    return rows


def prune() -> int:
    """もう承認待ちでない候補の絵を消す。

    **approved と published は残す。** 本撮りの前に見返すことがあるし、
    撮り直さない限り二度と手に入らない絵でもある。消すのは rejected と、
    検証リストから消えたものだけ。
    """
    if not REVIEW_DIR.is_dir():
        return 0

    keep_names = set()
    for entry in ledger.load().get("entries", {}).values():
        if entry.get("status") in ("rejected",):
            continue
        # やり直しは前の判定を retry.previous に畳んであるので、そちらも見る。
        # **撮り直す前に前回の絵が消えると、何が悪かったのか確かめられない。**
        previous = (entry.get("retry") or {}).get("previous") or {}
        for value in (entry.get("values") or []) + (previous.get("values") or []):
            keep_names.add(image_name(entry["node"], entry["parm"], value))

    removed = 0
    for path in REVIEW_DIR.glob("*.png"):
        if path.name not in keep_names:
            path.unlink()
            removed += 1
    return removed


def fmt_value(value) -> str:
    """コマンドに貼る形。1.0 を 1 と書く。"""
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:.10g}"


def ui_path(entry: dict) -> str:
    """UI でどこを触るか。**内部名では Houdini 上で探せない。**

    `niter` は UI では "Constraint Iterations"。内部名だけ見ても何のことか
    分からないので、タブ名とラベルを繋いで出す。
    """
    folders = entry.get("folders") or []
    label = entry.get("label") or entry.get("parm", "")
    return " > ".join([*folders, label])


def spec_line(entry: dict) -> str:
    """型・既定値・範囲を1行にする。**判断に要る素性。**"""
    bits = [entry.get("type", "")]
    if entry.get("default") is not None:
        bits.append(f"既定 {fmt_value(entry['default'])}")
    if entry.get("min") is not None and entry.get("max") is not None:
        bits.append(f"範囲の目安 {fmt_value(entry['min'])}〜{fmt_value(entry['max'])}")
    return " / ".join(b for b in bits if b)


def help_summary(text: str, sentences: int = 3, limit: int = 240) -> str:
    """公式の説明を2〜3文に切る。

    **nodes.zip の本文は長い。**動画を見て決める場所で全文を読ませると、
    肝心の動画より説明のほうが場所を取る。冒頭だけ出して、全文は畳む。

    翻訳はしない。**機械にできるのは切ることだけ**で、訳し損ねた説明を
    信じて判断されるほうが害が大きい。
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for part in parts[:sentences]:
        if out and len(out) + len(part) + 1 > limit:
            break
        out = f"{out} {part}".strip()
    return out if out else text[:limit]


def flipbook_command(entry: dict) -> str:
    """承認済みの1件を本撮りするコマンド。

    **承認したときの値と画角をそのまま使う。**`approved_values` に凍らせて
    あるので、あとで提案が変わっても撮るものは動かない。

    **まとめて撮るなら `shoot.py`。** ここが出すのは1本ぶんを人が打ち込む
    ための形で、`--out` は人が決める前提のプレースホルダのまま。
    """
    approved = entry.get("approved_values") or {}
    proposal = entry.get("proposal") or {}
    values = approved.get("values") or entry.get("values") or []
    hip = Path(entry.get("hip", "")) if entry.get("hip") else config.SCENE_DIR
    rel = hip.relative_to(config.ROOT) if hip.is_relative_to(config.ROOT) else hip
    camera = proposal.get("camera") or f"/obj/{config.DEFAULT_CAMERA}"

    return (
        f"  .venv\\Scripts\\python.exe tools\\flipbook.py --hip {rel} `\n"
        f"    --node {entry['node']} --parm {entry['parm']} `\n"
        f"    --values {','.join(fmt_value(v) for v in values)} --frames 1-48 `\n"
        f"    --out <id> --label \"{approved.get('label') or entry.get('label', '')}\" "
        f"--default-index {approved.get('default_index', 0)} --camera {camera}"
    )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="承認待ち・承認済み・撮影済みを一覧する")
    ap.add_argument("--approved", action="store_true",
                    help="承認済み（本撮り待ち）を出す")
    ap.add_argument("--shot", action="store_true",
                    help="撮影済み・未公開を出す（記事を書く番のもの）")
    args = ap.parse_args()

    if args.approved:
        rows = ledger.approved()
        if not rows:
            print("承認済みはありません。（/review/ か ledger.py approve で決めてください）")
            return 0
        print(f"承認済み {len(rows)} 件。まとめて撮るならこれ1本:")
        print("  .venv\\Scripts\\python.exe tools\\shoot.py")
        print("\n1本ずつ撮るなら:")
        for entry in sorted(rows, key=lambda e: e["parm"]):
            print(f"\n[{entry['parm']}] {entry.get('decision_note', '')}")
            print(flipbook_command(entry))
        return 0

    if args.shot:
        rows = [e for e in ledger.load().get("entries", {}).values()
                if e.get("status") == "shot"]
        if not rows:
            print("撮影済み・未公開はありません。（`shoot.py` で本撮りすると溜まります）")
            return 0
        print(f"撮影済み・未公開 {len(rows)} 件。記事を書く番:")
        for entry in sorted(rows, key=lambda e: e["parm"]):
            out = entry.get("out", "")
            print(f"\n  {entry['parm']}  （{entry.get('label', '')}）  撮影 {entry.get('shot_at', '?')}")
            print(f"      {config.MEDIA_DIR / (out + '.mp4')}")
            print(f"      記事に書く : :::compare {out}")
            print(f"      公開したら : ledger.py publish --parm {entry['parm']}")
        return 0

    rows = awaiting()
    if not rows:
        print("承認待ちはありません。（`screen_loop.py` を回すと溜まります）")
        return 0

    print(f"承認待ち {len(rows)} 件:")
    for row in rows:
        n = len(row["images"])
        print(f"  {row['parm']:<26} [{row.get('verdict', '')}]  絵 {n} 枚  {row['node']}")
        print(f"      {row.get('reason', '')}")
    print(f"\n  絵を見て決める: {config.BASE_URL}/review/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
