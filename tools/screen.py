"""setup シートの結果を数値で判定する。**採用可否を主観から外すためのもの。**

  python tools/screen.py

`setup_sheet.py` が出した画像を PSNR で比べ、sim が壊れていないかを
アトリビュートの記録と突き合わせて、パラメータごとに判定を出す。

## なぜ要るか

「振っても見た目が変わらないパラメータはこの媒体では価値がない」というのが
選定基準の1番目なのに、その判断を目視でやっていた。再現できないし、
記録も残らない。実際 `stretchstiffness` は一度「差が無い」と誤って不採用にし、
原因（隣の指数）に気づくまで戻れなかった。

## 判定

  差なし     既定値との差が SAME_DB 以上（＝ほぼ同じ絵）。振る価値がない
  段階が無駄 隣の段階との差が SAME_DB 以上。その段階は枠を捨てている
  破綻       NaN/inf、または速度が中央値の SPEED_RATIO 倍を超えた
  採用       上のいずれでもない

**PSNR は「大きいほど同じ」**。20dB は明確に違う絵、60dB はほぼ同一。

結果は `tools/_cache/setup/screen.json` に残す。何を測って何を落としたかの
記録がこれ（次に同じ調査をやり直さないため）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from encode import psnr  # noqa: E402

SHEET_DIR = config.CACHE_DIR / "setup"

# この dB 以上離れていなければ「同じ絵」とみなす。
# 実測の目安: 1〜5 で 19〜47dB が有効域だった `stretchstiffnessexp` に対し、
# 6 以上は 59dB で頭打ち（＝差が無い）。その間に線を引く。
SAME_DB = 50.0

# 速度がこの倍率を超えたら破綻を疑う（中央値比）。
SPEED_RATIO = 20.0


def jsonable(value: float) -> float | str:
    """JSON は inf を持てない。完全一致は文字列 'inf' で残す。

    **None（基準セル）と混同しないこと。** 一致は「振っても絵が変わらない」
    という結論そのもので、基準であることとは意味が違う。
    """
    return "inf" if value == float("inf") else round(value, 2)


def fmt_db(value: float | str | None, is_base: bool) -> str:
    if is_base:
        return "  （基準）"
    if value is None:
        return " " * 10
    if value == "inf":
        return "      同一"
    return f"{value:10.1f}"


def check_geo(cells: list[dict]) -> dict[int, str]:
    """アトリビュートの記録から、明らかに壊れているセルを拾う。

    絵の比較だけでは「爆発したのか、そういうパラメータなのか」が
    区別できない。速度と NaN を先に見る。
    """
    problems: dict[int, str] = {}

    speeds = [
        c["geo"].get("speed_p95") for c in cells
        if c.get("geo") and c["geo"].get("speed_p95") is not None
    ]
    speeds = [s for s in speeds if s is not None]
    median = sorted(speeds)[len(speeds) // 2] if speeds else None

    for i, cell in enumerate(cells):
        geo = cell.get("geo") or {}
        if geo.get("error"):
            problems[i] = geo["error"]
            continue
        if geo.get("nan_P") or geo.get("nan_v"):
            problems[i] = f"NaN/inf（P {geo.get('nan_P', 0)} / v {geo.get('nan_v', 0)}）"
            continue
        p95 = geo.get("speed_p95")
        if median and p95 and median > 0 and p95 > median * SPEED_RATIO:
            problems[i] = f"速度が中央値の {p95 / median:.0f} 倍（爆発の疑い）"
    return problems


def screen_group(group: dict, sheet_dir: Path, same_db: float) -> dict:
    """1パラメータぶんを判定する。"""
    cells = group["cells"]
    images = [sheet_dir / c["img"] for c in cells]
    missing = [p.name for p in images if not p.exists()]
    if missing:
        raise SystemExit(f"画像がありません: {', '.join(missing)}")

    # 既定値のセルを基準にする。無ければ先頭。
    default = group.get("default")
    base = 0
    for i, cell in enumerate(cells):
        if default is not None and cell["value"] == default:
            base = i
            break

    vs_default = [psnr(images[base], img) if i != base else float("inf")
                  for i, img in enumerate(images)]
    neighbours = [psnr(images[i], images[i + 1]) for i in range(len(images) - 1)]
    broken = check_geo(cells)

    # 既定値そのものを除いて、どこかに差があるか
    diffs = [d for i, d in enumerate(vs_default) if i != base]
    best = min(diffs) if diffs else float("inf")

    if broken:
        verdict = "破綻"
        reason = "; ".join(f"{cells[i]['value']}: {why}" for i, why in broken.items())
    elif best == float("inf"):
        verdict = "差なし"
        reason = "全ての値で既定値と完全に同一の絵（1画素も変わっていない）"
    elif best >= same_db:
        verdict = "差なし"
        reason = f"既定値との差が最小でも {best:.1f} dB（{same_db} dB 以上＝ほぼ同じ絵）"
    else:
        wasted = [i for i, d in enumerate(neighbours) if d >= same_db]
        if wasted:
            verdict = "採用（段階に無駄あり）"
            pairs = ", ".join(
                f"{cells[i]['value']}→{cells[i + 1]['value']}" for i in wasted
            )
            reason = f"隣どうしがほぼ同じ: {pairs}"
        else:
            widest = max(d for d in diffs if d != float("inf"))
            verdict = "採用"
            reason = f"既定値との差 {best:.1f}〜{widest:.1f} dB"

    return {
        "parm": group["parm"],
        "node": group["node"],
        "default": default,
        "base_index": base,
        "values": [c["value"] for c in cells],
        # 基準セルだけ None。完全一致は "inf"（＝差が無いという結論）。
        "vs_default_db": [
            None if i == base else jsonable(d) for i, d in enumerate(vs_default)
        ],
        "neighbour_db": [jsonable(d) for d in neighbours],
        "broken": {str(cells[i]["value"]): why for i, why in broken.items()},
        "verdict": verdict,
        "reason": reason,
    }


def print_group(result: dict, cells: list[dict]) -> None:
    print(f"=== {result['parm']}  [{result['verdict']}] ===")
    print(f"  {result['reason']}")
    print(f"  {'値':>12}  {'既定との差':>10}  {'隣との差':>10}  備考")
    for i, value in enumerate(result["values"]):
        vs_s = fmt_db(result["vs_default_db"][i], i == result["base_index"])
        nb = result["neighbour_db"][i] if i < len(result["neighbour_db"]) else None
        nb_s = fmt_db(nb, False)
        geo = cells[i].get("geo") or {}
        speed = geo.get("speed_p95")
        note = result["broken"].get(str(value), "")
        if not note and speed is not None:
            note = f"v_p95 {speed}"
        print(f"  {value:>12}  {vs_s}  {nb_s}  {note}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="setup シートの結果を PSNR とアトリビュートで判定する",
    )
    ap.add_argument("--sheet", default=str(SHEET_DIR), help="setup シートのディレクトリ")
    ap.add_argument(
        "--same-db", type=float, default=SAME_DB,
        help=f"この dB 以上なら「同じ絵」とみなす（既定 {SAME_DB}）",
    )
    ap.add_argument(
        "--no-ledger", action="store_true",
        help="判定を screening.json に書き戻さない（試しに測るだけのとき）",
    )
    args = ap.parse_args()

    sheet_dir = Path(args.sheet)
    meta_path = sheet_dir / "sheet.json"
    if not meta_path.exists():
        raise SystemExit(
            f"setup シートがありません: {meta_path}\n"
            "  先に tools/setup_sheet.py を実行してください。"
        )

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    print(f"シート: {meta.get('generated', '?')}  /  hip: {meta.get('hip', '?')}")
    print(f"判定しきい値: {args.same_db} dB 以上を「同じ絵」とみなす\n")

    results = []
    for group in meta["groups"]:
        result = screen_group(group, sheet_dir, args.same_db)
        print_group(result, group["cells"])
        results.append(result)

    record = {
        "generated": meta.get("generated"),
        "hip": meta.get("hip"),
        "frame": meta.get("frame"),
        "same_db": args.same_db,
        "speed_ratio": SPEED_RATIO,
        "results": results,
    }
    out = sheet_dir / "screen.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    adopted = [r["parm"] for r in results if r["verdict"].startswith("採用")]
    print(f"採用候補 {len(adopted)}/{len(results)}: {', '.join(adopted) or 'なし'}")
    print(f"記録: {out}")

    # 判定は台帳にも書き戻す。**同じ調査を繰り返さないため**で、
    # screen.json は最新の1回ぶんしか持たない（上書きされる）。
    if not args.no_ledger:
        import ledger
        n = ledger.merge_screen(out)
        print(f"台帳を更新しました: {ledger.LEDGER}（{n} 件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
