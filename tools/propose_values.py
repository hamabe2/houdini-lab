"""振る値の刻みを、**絵が飽和する端を先に探してから**決める。

  python tools/propose_values.py --hip scenes/vellum_cloth.hip \
      --node /obj/SUBJECT/CONSTRAINTS --parm bendstiffness

既定値を中心に桁を上下させて1フレームずつ撮り、「ここから先は何を入れても
同じ絵」という端を両側で見つける。端が見つからなければ梯子を自分で伸ばす。
最後にその内側から段階を選んで `flipbook.py` のコマンドを出す。

## なぜ要るか

`screen.py` の判定は「既定値との差が何 dB か」しか見ていない。**差が出て
いれば通るので、有効域のごく一部だけを拡大していても採用される。**

実例: `bendstiffness` を 0.1〜100（実効 1e-5〜1e-2）で篩にかけたところ
隣どうし 34〜37dB で「採用・段階に無駄なし」と通った。ところが本撮りして
みると5段階が見分けられない。実測した有効域は **0〜100000**（実効 0〜10）で、
撮っていたのは**その 0.1% 以下**だった。

端を先に探せば、この「有効域のごく一部を 5段階に切る」事故は起きない。

## 手順

  1. 既定値 x 10^k の梯子を作って1フレームずつ撮る
  2. 隣どうしの PSNR を測る。SAME_DB 以上なら「同じ絵」
  3. 既定値から外へ歩いて、同じ絵が続き始めるところ = 飽和の端
  4. 端が見つからない側は梯子を伸ばして撮り足す（--max-rounds 回まで）
  5. 変化している範囲から、既定値と両端を含む段階を選ぶ

**段階は撮った梯子の段から選ぶ。**新しい値を作らない。梯子は既定値の倍数で
できているので既定値は必ず候補にあり、どの段も「実際に撮って隣との差を
測った値」になる。

梯子は端を探すためのもので、採用可否の判定ではない。
**本撮りの前に `screen.py` を通すこと。**
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402
from encode import composite_still, psnr  # noqa: E402
from flipbook import DEFAULT_HIDDEN, launch, visible_pattern  # noqa: E402
from screen import SAME_DB, check_geo  # noqa: E402
from setup_sheet import SHEET_DIR, slug, write_sheet  # noqa: E402

# 既定値の何桁上下から始めるか。
#
# **UI スライダーの範囲は当てにならない。** bendstiffness の範囲は 0〜10（目安）
# だが、絵が頭打ちになるのは 100000。範囲は「よく使う辺り」でしかない。
DECADES = 4

# 端が見つからないとき、1回の撮り足しで何桁伸ばすか。
EXTEND_DECADES = 3

# 1桁を何段に割るか。段は「既定値 x 仮数 x 10^k」で作る。
# **仮数は読める数字にする。** 段階の値は記事にそのまま並ぶので、
# 3.16 より 3 のほうがよい（対数の位置は 0.05 桁しか動かない）。
MANTISSAS = {1: (1.0,), 2: (1.0, 3.0), 3: (1.0, 2.0, 5.0), 4: (1.0, 2.0, 3.0, 5.0)}

PROBE_DIR = config.CACHE_DIR / "propose"


def fmt(value: float) -> str:
    """表示用。1.0 を 1 と書き、0.0001 を 1e-04 にしない。"""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def round_sig(value: float, digits: int = 3) -> float:
    if value == 0:
        return 0.0
    return float(f"{value:.{digits}g}")


def load_meta(node: str, parm: str, args: argparse.Namespace) -> dict:
    """パラメータの型・既定値・範囲を台帳から引く。

    **Houdini を起動せずに済ませる。** 梯子は撮る前に決まっていないと
    いけないが、そのためだけにもう1回 hip を読むのは高い。`ledger.py add`
    が既に list_parms.py 経由で取り込んでいるので、そこから読む。
    """
    entry = ledger.load().get("entries", {}).get(ledger.key_of(node, parm), {})

    meta = {
        "type": entry.get("type"),
        "default": entry.get("default"),
        "min": entry.get("min"),
        "max": entry.get("max"),
        "label": entry.get("label") or parm,
    }
    if args.default is not None:
        meta["default"] = args.default
    if args.min is not None:
        meta["min"] = args.min

    if meta["default"] is None:
        raise SystemExit(
            f"台帳に {node}:{parm} がありません。\n"
            f"  先に  ledger.py add --hip <hip> --node {node}  を実行するか、\n"
            "  --default で既定値を直接渡してください。"
        )
    if not isinstance(meta["default"], (int, float)):
        raise SystemExit(
            f"{parm} の既定値が数値ではありません（{meta['default']!r}）。\n"
            "  メニューやトグルは梯子を作れません。"
        )
    return meta


def ladder_stops(meta: dict, cap: float | None) -> set[float]:
    """梯子が「意図して止まる」値。

    パラメータの下限や `--cap` で止まった端は、その値自体に意味がある
    （`bendstiffness = 0` は「曲げ拘束なし」）。撮り切っただけの外端とは
    区別する（end_index 参照）。
    """
    stops: set[float] = set()
    if isinstance(meta.get("min"), (int, float)):
        stops.add(float(meta["min"]))
    if cap is not None:
        stops.add(float(cap))
    return stops


def rungs(anchor: float, ks: range, per_decade: int, is_int: bool) -> list[float]:
    """anchor x 仮数 x 10^k の段を作る。"""
    mant = MANTISSAS.get(per_decade, MANTISSAS[1])
    out = [anchor * m * 10.0 ** k for k in ks for m in mant]
    return [float(round(v)) if is_int else round_sig(v) for v in out]


def clip(values: list[float], meta: dict, cap: float | None) -> list[float]:
    vmin = meta.get("min")
    floor = float(vmin) if isinstance(vmin, (int, float)) else None
    out = values
    if floor is not None:
        out = [v for v in out if v >= floor]
    if cap is not None:
        out = [v for v in out if v <= cap]
    return out


def build_ladder(
    meta: dict, decades: int, cap: float | None, per_decade: int = 1,
) -> list[float]:
    """既定値を中心に桁を上下させた梯子。

    **段は既定値の倍数で作る。** こうすると既定値が必ず梯子に乗るので、
    後で段階を選ぶときに「既定値を押し込む」細工が要らなくなる。

    既定値が 0 のパラメータ（veldamping など）は掛け算で伸ばせないので、
    範囲の上端から下へ降ろす。
    """
    is_int = meta["type"] == "Int"
    default = float(meta["default"])

    if default > 0:
        values = rungs(default, range(-decades, decades + 1), per_decade, is_int)
    else:
        top = meta.get("max")
        top = float(top) if isinstance(top, (int, float)) and top > 0 else 1.0
        values = [v for v in rungs(top, range(-decades, 1), per_decade, is_int)
                  if v <= top]

    # 下端が 0 のパラメータでは 0 そのものを必ず撮る。「拘束なし」は
    # 掛け算では決して届かない値で、しかも読者にいちばん意味がある端。
    vmin = meta.get("min")
    if isinstance(vmin, (int, float)) and float(vmin) <= 0:
        values.append(0.0)

    values = clip(values, meta, cap)
    if default not in values:
        values.append(default)
    return sorted({float(v) for v in values})


def extend_ladder(
    values: list[float], meta: dict, span: dict, decades: int,
    cap: float | None, per_decade: int,
) -> list[float]:
    """端が見つからなかった側へ梯子を伸ばす（撮り足すぶんだけ返す）。"""
    is_int = meta["type"] == "Int"
    known = {float(v) for v in values}
    new: list[float] = []

    if span["open_high"]:
        new += rungs(max(values), range(1, decades + 1), per_decade, is_int)
    if span["open_low"]:
        positives = [v for v in values if v > 0]
        if positives:
            new += rungs(min(positives), range(-decades, 0), per_decade, is_int)

    return sorted({v for v in clip(new, meta, cap) if v not in known})


def find_range(
    values: list[float], base: int, nb_db: list[float], broken: dict[int, str],
    same_db: float, stops: set[float],
) -> dict:
    """飽和の端を両側で探す。

    inner は「まだ絵が変わっている範囲」、outer は「飽和帯の外端」。
    """
    def walk(step: int) -> tuple[int, int]:
        i = base
        while True:
            nxt = i + step
            if not (0 <= nxt < len(values)):
                return i, i          # 梯子を撮り切った。端は見つかっていない
            if nxt in broken:
                return nxt, nxt      # 破綻はそれ自体が見せる価値のある端
            pair = min(i, nxt)       # nb_db[j] は values[j] と values[j+1] の差
            if nb_db[pair] >= same_db:
                # ここから飽和。同じ絵が続く限り外へ抜けて、その外端も返す
                outer = nxt
                while True:
                    beyond = outer + step
                    if not (0 <= beyond < len(values)):
                        break
                    if nb_db[min(outer, beyond)] < same_db:
                        break
                    outer = beyond
                return i, outer
            i = nxt

    lo_inner, lo_outer = walk(-1)
    hi_inner, hi_outer = walk(+1)

    # **梯子の端まで変化し続けている側は「開いている」。** ただしそこが
    # パラメータの下限（や --cap）なら、それはパラメータ自身の端であって
    # 梯子不足ではない。伸ばしても撮る値が無い。
    return {
        "inner": (lo_inner, hi_inner),
        "outer": (lo_outer, hi_outer),
        "open_low": lo_outer == 0 and lo_inner == 0 and values[0] not in stops,
        "open_high": (hi_outer == len(values) - 1 and hi_inner == len(values) - 1
                      and values[-1] not in stops),
    }


def end_index(outer: int, inner: int, values: list[float], stops: set[float]) -> int:
    """飽和帯を代表する1段を選ぶ。

    飽和帯は「どれを入れても同じ絵」なので代表は1つでいい。どれを採るかは
    **梯子がそこで止まった理由**で決まる:

      下限（や --cap）で止まっている
          その値そのものが端。`bendstiffness = 0`（曲げ拘束なし）のように、
          読者にとって意味のある端であることが多い
      撮り切っただけ
          いちばん外の段は「たまたま最後に撮った値」でしかない。代表は
          **最初に飽和した段**（＝飽和帯の内側の端）を採る

    後者で外端を採ると、実効 10 で頭打ちなのに実効 1000 をサイトに並べる
    ような提案になる（実測で踏んだ）。
    """
    return outer if values[outer] in stops else inner


def visual_positions(nb_db: list[float]) -> list[float]:
    """段を「見た目の差」の軸に並べ替える。

    **値の対数で等間隔に切ってはいけない。** 効きは値に対して一様ではなく、
    同じ1桁でも絵が大きく動く帯とほとんど動かない帯がある。対数等間隔だと
    後者に枠を使ってしまう（実測: bendstiffness で 0 と 0.01 が 48.8dB ＝
    ほとんど同じ絵なのに、別々の段階として提案された）。

    隣どうしの PSNR は測ってあるので、それを距離に変換して積み上げる。

      距離 = 10^(-PSNR/20)   （PSNR の定義から、RMS 誤差に比例する量）

    PSNR そのものは足し合わせられないが、この量なら「隣の差の積み重ね」と
    して扱える。飽和した帯は距離がほぼ 0 になるので、自然に詰まる。
    """
    pos = [0.0]
    for db in nb_db:
        step = 0.0 if db == float("inf") else 10.0 ** (-db / 20.0)
        pos.append(pos[-1] + step)
    return pos


def pick_stages(
    values: list[float], span: dict, base: int, stages: int, stops: set[float],
    nb_db: list[float], is_int: bool,
) -> list[float]:
    """有効域から段階を選ぶ。

    **新しい値を作らず、撮った梯子の段から選ぶ。** 対数等間隔に切り直して
    から既定値を押し込む方式だと、0.5 と 1 のようなほぼ同じ隣が生まれる。
    """
    ci, cj = span["inner"]
    lo = end_index(span["outer"][0], ci, values, stops)
    hi = end_index(span["outer"][1], cj, values, stops)

    # 候補は「変化している範囲」＋「両端の代表」。飽和帯の内側は入れない
    # （どれを入れても同じ絵なので、枠を捨てることになる）。
    cand = sorted({lo, hi, *range(ci, cj + 1)})
    pos = visual_positions(nb_db)

    # 両端と既定値は必ず入れる。残りは「既に選んだ段からいちばん遠い段」を
    # 繰り返し足す（見た目の差で測るので、隣どうしの差が揃う）。
    chosen = {lo, hi, base}
    while len(chosen) < min(stages, len(cand)):
        chosen.add(max(
            (i for i in cand if i not in chosen),
            key=lambda i: min(abs(pos[i] - pos[j]) for j in chosen),
        ))

    return [int(values[i]) if is_int else values[i] for i in sorted(chosen)]


def shoot(hip: Path, node: str, parm: str, values: list[float],
          args: argparse.Namespace, work: Path) -> list[dict]:
    """梯子を1フレームずつ撮る（setup_sheet.py と同じ仕組みを使う）。"""
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    hidden = [n.strip() for n in args.hide.split(",") if n.strip()]
    started = time.time()
    result = launch(
        {
            "mode": "sheet",
            "hip": str(hip),
            "probes": [{"node": node, "parm": parm, "values": values}],
            "frame": args.frame,
            "width": args.width,
            "height": args.height,
            "camera": args.camera,
            "visible": visible_pattern(hidden),
            "clean": True,
            "aa": args.aa,
            "shading": "SmoothWire",
            "scheme": config.VIEWPORT_SCHEME,
            "lighting": "Headlight",
            "work_light": "Headlight",
            "work": str(work),
            "result": str(work / "result.json"),
            "log": str(work / "setup.log"),
        },
        job_path=work / "job.json",
        log_path=work / "setup.log",
        timeout_s=args.timeout * 60,
        show_window=args.show_window,
    )
    print(f"  撮影に {(time.time() - started) / 60:.1f} 分かかりました")

    cells = result["items"]
    if len(cells) != len(values):
        raise SystemExit(f"撮れた枚数が合いません（{len(cells)}/{len(values)}）")
    return cells


def composite(cells: list[dict], probe_dir: Path) -> list[Path]:
    """PSNR を測れる形（不透明な背景に合成済み）にして返す。

    **生の PNG のまま測らないこと。** flipbook の PNG は RGBA で、背景は
    alpha=0。透明な画素の RGB まで計算に入ると、同じ絵でも 15dB になる。
    """
    probe_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for cell in cells:
        path = probe_dir / f"{slug(cell['parm'])}_{slug(str(cell['value']))}.png"
        if not path.exists():
            composite_still(Path(cell["path"]), path)
        out.append(path)
    return out


def propose(
    hip: Path, node: str, parm: str, args: argparse.Namespace,
    work_root: Path | None = None, probe_dir: Path | None = None,
) -> dict:
    """梯子を撮って端を探し、振る値の段階を決める。

    **撮ったセルもそのまま返す。** 段階は梯子の段から選ぶので、提案した値の
    絵は既に撮れている。呼び出し側（loop.py）はそれを setup シートに回せば、
    同じ sim をもう一度回さずに screen.py へ渡せる。
    """
    work_root = work_root or config.CACHE_DIR / "shots" / "_propose"
    probe_dir = probe_dir or PROBE_DIR

    meta = load_meta(node, parm, args)
    is_int = meta["type"] == "Int"
    stops = ladder_stops(meta, args.cap)
    default = float(meta["default"])

    print(f"{node} / {parm}  既定 {fmt(default)}（{meta['label']}）")

    for path in (probe_dir, work_root):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    pending = build_ladder(meta, args.decades, args.cap, args.per_decade)
    cells: list[dict] = []
    rounds = 0

    while True:
        print(f"  梯子 {len(pending)} 段を frame {args.frame} で撮ります: "
              f"{', '.join(fmt(v) for v in pending)}")
        cells += shoot(hip, node, parm, pending, args, work_root / f"r{rounds}")

        # 値でそろえる。撮り足したぶんは順番がばらばらに来る。
        cells.sort(key=lambda c: float(c["value"]))
        images = composite(cells, probe_dir)
        values = [float(c["value"]) for c in cells]

        nb_db = [psnr(images[i], images[i + 1]) for i in range(len(images) - 1)]
        broken = check_geo(cells)
        base = min(range(len(values)), key=lambda i: abs(values[i] - default))
        span = find_range(values, base, nb_db, broken, args.same_db, stops)

        if not (span["open_low"] or span["open_high"]):
            break
        if rounds >= args.max_rounds:
            break
        pending = extend_ladder(values, meta, span, EXTEND_DECADES,
                                args.cap, args.per_decade)
        if not pending:
            break
        rounds += 1
        sides = " と ".join(s for s, on in (("下", span["open_low"]),
                                            ("上", span["open_high"])) if on)
        print(f"  {sides}の端がまだ見つかりません。梯子を伸ばします "
              f"（{rounds}/{args.max_rounds} 回目）")

    stages = pick_stages(values, span, base, args.stages, stops, nb_db, is_int)

    mult = next((c.get("multiplier") for c in cells if c.get("multiplier")), None)
    factor = mult["multiplier"] if mult else 1.0

    lo_i = end_index(span["outer"][0], span["inner"][0], values, stops)
    hi_i = end_index(span["outer"][1], span["inner"][1], values, stops)

    default_value = int(default) if is_int else default
    return {
        "node": node,
        "parm": parm,
        "label": meta["label"],
        "camera": args.camera,
        "frame": args.frame,
        "same_db": args.same_db,
        "is_int": is_int,
        "factor": factor,
        "cells": cells,
        "values": values,
        "neighbour_db": nb_db,
        "broken": broken,
        "base": base,
        "span": span,
        "range": [values[lo_i], values[hi_i]],
        "stages": stages,
        "display_values": [round_sig(v * factor) for v in stages],
        "default_index": stages.index(default_value) if default_value in stages else 0,
    }


def print_report(result: dict, hip: Path) -> None:
    """梯子の実測と提案を表で出す。**提案を疑えるように数字を全部見せる。**"""
    values, nb_db = result["values"], result["neighbour_db"]
    span, factor = result["span"], result["factor"]
    ci, cj = span["inner"]
    chosen = {float(v) for v in result["stages"]}

    print()
    print(f"{'値':>12}  {'隣との差':>10}  {'実効値':>12}  備考")
    for i, value in enumerate(values):
        nb = f"{nb_db[i]:10.1f}" if i < len(nb_db) else " " * 10
        marks = []
        if i == result["base"]:
            marks.append("既定")
        if i < ci or i > cj:
            marks.append("飽和（この先は同じ）")
        if i in result["broken"]:
            marks.append(result["broken"][i])
        print(f"{'->' if value in chosen else '  '}{fmt(value):>10}  {nb}  "
              f"{fmt(round_sig(value * factor)):>12}  {' / '.join(marks)}")

    lo, hi = result["range"]
    print()
    print(f"  有効域: {fmt(lo)} 〜 {fmt(hi)}"
          f"（実効 {fmt(round_sig(lo * factor))} 〜 {fmt(round_sig(hi * factor))}）")
    if span["open_low"] or span["open_high"]:
        sides = " と ".join(s for s, on in (("下", span["open_low"]),
                                            ("上", span["open_high"])) if on)
        print(f"  注意: {sides}の端は見つかっていません。"
              "梯子の外でまだ絵が変わっています。")

    print(f"提案: {','.join(fmt(v) for v in result['stages'])}"
          f"  （実効 {', '.join(fmt(v) for v in result['display_values'])}）")
    print()
    rel = hip.relative_to(config.ROOT) if hip.is_relative_to(config.ROOT) else hip
    print(f"  .venv\\Scripts\\python.exe tools\\flipbook.py --hip {rel} `")
    print(f"    --node {result['node']} --parm {result['parm']} `")
    print(f"    --values {','.join(fmt(v) for v in result['stages'])} --frames 1-48 `")
    print(f"    --out <id> --label \"{result['label']}\" "
          f"--default-index {result['default_index']} --camera {result['camera']}")


def record_proposal(result: dict) -> None:
    """提案を台帳に残す。**次に同じ梯子を撮り直さないため。**"""
    data = ledger.load()
    entry = data.setdefault("entries", {}).setdefault(
        ledger.key_of(result["node"], result["parm"]),
        {"node": result["node"], "parm": result["parm"], "status": "pending"},
    )
    entry["proposal"] = {
        "values": result["stages"],
        "display_values": result["display_values"],
        "default_index": result["default_index"],
        "range": result["range"],
        "ladder": result["values"],
        "neighbour_db": [
            "inf" if d == float("inf") else round(d, 2) for d in result["neighbour_db"]
        ],
        "open_low": result["span"]["open_low"],
        "open_high": result["span"]["open_high"],
        "frame": result["frame"],
        "same_db": result["same_db"],
        "proposed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    ledger.save(data)


def stage_cells(result: dict) -> list[dict]:
    """提案した段階に当たるセルだけを返す（シートに載せる用）。"""
    wanted = {float(v) for v in result["stages"]}
    return [c for c in result["cells"] if float(c["value"]) in wanted]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="絵が飽和する端を探してから、振る値の刻みを決める",
    )
    ap.add_argument("--hip", required=True)
    ap.add_argument("--node", required=True)
    ap.add_argument("--parm", required=True)
    ap.add_argument("--frame", type=int, default=24, help="撮るフレーム（既定 24）")
    ap.add_argument("--stages", type=int, default=5, help="提案する段階数（既定 5）")
    ap.add_argument(
        "--decades", type=int, default=DECADES,
        help=f"既定値の何桁上下から探し始めるか（既定 {DECADES}）",
    )
    ap.add_argument(
        "--max-rounds", type=int, default=3,
        help="端が見つからないときの撮り足し回数の上限（既定 3）",
    )
    ap.add_argument(
        "--cap", type=float,
        help="梯子の上限。sim が重すぎる値を撮らないため（既定は無制限）",
    )
    ap.add_argument(
        "--per-decade", type=int, default=1, choices=(1, 2, 3, 4),
        help="1桁を何段に割るか。増やすと刻みを細かく選べるが撮影も増える（既定 1）",
    )
    ap.add_argument("--same-db", type=float, default=SAME_DB,
                    help=f"この dB 以上離れていなければ同じ絵とみなす（既定 {SAME_DB}）")
    ap.add_argument("--default", type=float, help="既定値を台帳から引かずに指定する")
    ap.add_argument("--min", type=float, help="下限を台帳から引かずに指定する")
    ap.add_argument("--camera", default=f"/obj/{config.DEFAULT_CAMERA}")
    ap.add_argument("--width", type=int, default=config.VIDEO_WIDTH)
    ap.add_argument("--height", type=int, default=config.VIDEO_HEIGHT)
    ap.add_argument("--aa", type=int, default=8, choices=(1, 2, 4, 8, 16, 32))
    ap.add_argument("--hide", default=",".join(DEFAULT_HIDDEN))
    ap.add_argument("--show-window", action="store_true")
    ap.add_argument("--timeout", type=float, default=config.FLIPBOOK_TIMEOUT_MIN)
    ap.add_argument("--no-ledger", action="store_true",
                    help="提案を screening.json に書き戻さない")
    args = ap.parse_args()

    hip = Path(args.hip).resolve()
    if not hip.exists():
        raise SystemExit(f"シーンファイルがありません: {hip}")

    result = propose(hip, args.node, args.parm, args)
    print_report(result, hip)

    # 梯子そのものもブラウザで見られるようにしておく（提案を疑うとき用）。
    write_sheet(result["cells"], args.frame, hip)
    print(f"\n  梯子の絵: {config.BASE_URL}/setup/")

    if not args.no_ledger:
        record_proposal(result)
        print(f"  台帳に提案を残しました: {ledger.LEDGER}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
