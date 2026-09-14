"""撮影済み（`shot`）から記事の下書きを作る。

  python tools/draft.py            # 撮影済みで下書きの無いものを全部
  python tools/draft.py --parm benddampingratio

## 何を書けて、何を書けないか

**書けるのは測った数字だけ。**振った値・既定値・隣どうしの dB・有効域・
公式の説明は検証リストと `media/<id>.json` にある。

**書けないのは「何が見えるか」。**「下端の角が巻き込む」「振れの位相が変わる」は
動画を見た人にしか書けない。`bendrestscale` の記事は hython で二面角を実測して
初めて「触っても無駄」と書けた。ここは空欄にして、人が埋める場所だと明示する。

## 公開されない

出力は `content/drafts/` で、front matter に `draft: true` が入る。
`config.SHOW_DRAFTS` はローカルの `serve.py` しか立てないので、**push しても
サイトには出ない。**人が動画を見て言葉を足し、本体の記事へ移して初めて公開される。

（`media/*.mp4` は別扱いで、コミットすれば URL を知る人には届く。撮っただけの
動画は公開が決まるまでコミットしないこと。）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402
from review import fmt_value, ui_path  # noqa: E402

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def front_matter(path: Path) -> dict:
    """記事の front matter だけを読む。**PyYAML に頼らない。**

    ここで要るのは `scene_node` / `node` / `context` / `tags` の4つで、
    どれも1行の素朴な値。ツール側に依存を増やさずに済ませる。
    """
    m = FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        if line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"')
        if value.startswith("[") and value.endswith("]"):
            out[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            out[key.strip()] = value
    return out


def parent_page(node_path: str) -> tuple[Path | None, dict]:
    """このシーンノードを扱っている記事を探す。

    記事の front matter に `scene_node: /obj/SUBJECT/CONSTRAINTS` と書いて
    おけば、下書きは**どこへ移すのか**と、ノード型・コンテキスト・タグを
    そこから引ける。無ければ空で作り、人が決める。
    """
    node_dir = config.CONTENT_DIR / "nodes"
    for path in sorted(node_dir.glob("*.md")) if node_dir.exists() else []:
        fm = front_matter(path)
        if fm.get("scene_node") == node_path:
            return path, fm
    return None, {}


def measured_table(entry: dict, meta: dict) -> list[str]:
    """振った値と、機械が測った差を表にする。

    `vs_default_db` は既定値との差、`neighbour_db` は隣の段との差。
    **どちらも 1 フレームの PSNR**で、大きいほど「同じ絵」を意味する。
    """
    values = meta.get("values") or entry.get("values") or []
    raw = meta.get("raw_values")
    vs = entry.get("vs_default_db") or []
    neighbour = entry.get("neighbour_db") or []
    default_index = meta.get("default_index")

    head = "| 実効値 |" + (" 入力欄 |" if raw else "") + " 既定との差 | 隣との差 |"
    rows = [head, "|---|" + ("---|" if raw else "") + "---|---|"]
    for i, value in enumerate(values):
        cells = [f"`{fmt_value(value)}`" + (" **既定**" if i == default_index else "")]
        if raw:
            cells.append(f"`{fmt_value(raw[i])}`" if i < len(raw) else "")
        db = vs[i] if i < len(vs) else None
        cells.append("—" if db is None else f"{db} dB")
        # 隣との差は「1つ前の段との差」なので、先頭には無い。
        cells.append(f"{neighbour[i - 1]} dB" if 0 < i <= len(neighbour) else "—")
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def section(entry: dict, meta: dict) -> str:
    """1パラメータぶんの本文。"""
    parm = entry["parm"]
    lines = [f"## {parm}", ""]

    help_text = (entry.get("help") or "").strip()
    ja = (entry.get("ja") or "").strip()
    # 人が書いた日本語があれば先頭に置く（`ledger.py describe` か `/watch/`
    # の入力欄で書いたもの）。記事の書き出しに一番近い文はこれ。
    if ja:
        lines += [ja, ""]
    lines += [f"UI では **{ui_path(entry)}**。", ""]
    lines += [f":::compare {entry['out']}", ""]

    lines += ["<!-- ここから下は機械が書いた。動画を見て直すこと。 -->", ""]
    lines += ["**まだ人が見ていない。** 以下は 1 フレームの数値比較だけで、"
              "動画で何が起きているかは誰も確認していない。", ""]

    lines += measured_table(entry, meta) + [""]

    proposal = entry.get("proposal") or {}
    facts = []
    rng = proposal.get("range") or []
    if len(rng) == 2:
        facts.append(f"**測った有効域は `{fmt_value(rng[0])}` 〜 `{fmt_value(rng[1])}`。**"
                     "この外はどの値を入れても同じ絵になる（1フレーム比較）")
    if proposal.get("open_high") or proposal.get("open_low"):
        facts.append("**端が見つかっていない。** 梯子を伸ばし切る前に打ち切っている")
    if entry.get("min") is not None and entry.get("max") is not None:
        facts.append(f"Houdini の UI が示す範囲の目安は `{fmt_value(entry['min'])}` 〜 "
                     f"`{fmt_value(entry['max'])}`（強制ではない）")
    if meta.get("multiplier"):
        mult = meta["multiplier"]
        facts.append(f"**表記は実効値。** 入力欄の値 x `{mult.get('source', '')}`")
    if entry.get("broken"):
        broken = ", ".join(f"`{k}`" for k in entry["broken"])
        facts.append(f"**破綻している値が入っている**: {broken}")
    if facts:
        lines += [f"- {f}" for f in facts] + [""]

    if help_text:
        lines += ["公式の説明:", "", f"> {help_text}", ""]

    todo = []
    if not ja:
        todo.append("- [ ] **概要。** このパラメータが何をするか2〜3行"
                    "（`ledger.py describe --ja` か `/watch/` の入力欄で書くと"
                    "次からここに入る）")
    todo.append("- [ ] **固定値の表。** この動画で動かさなかった他のパラメータ"
                "（`list_parms.py` で実測する。記憶で書かない）")
    todo.append("- [ ] 本体（`content/nodes/`）へ移す")

    # **「どこを見ると違いが分かるか」は書かない。**それは動画がやる仕事で、
    # 読者は自分の見たいところを見る。この一行を落としたので、記事に人しか
    # 書けない部分は「固定値の表」だけになった。
    lines += ["### 残っていること", ""] + todo + [""]
    return "\n".join(lines)


def write_draft(entry: dict, meta: dict) -> Path:
    parent, fm = parent_page(entry["node"])
    title = fm.get("title") or entry["node"].rsplit("/", 1)[-1]
    slug = f"draft-{entry['out']}"

    head = [
        "---",
        f"title: {title} / {entry['parm']}",
        f"node: {fm.get('node', '')}",
        f"context: {fm.get('context', 'SOP')}",
        f'houdini_version: "{config.HOUDINI_VERSION}"',
        f"tags: [{', '.join(fm.get('tags') or [])}]",
        f"updated: {(entry.get('shot_at') or '')[:10]}",
        f"description: {entry.get('label', entry['parm'])} を段階的に振った比較動画（下書き）。",
        "# **公開されない。** config.SHOW_DRAFTS はローカルの serve.py しか立てない。",
        "draft: true",
        f"scene_node: {entry['node']}",
        f"merge_into: {parent.name if parent else '（見つからない。scene_node を書いた記事がない）'}",
        "---",
        "",
    ]
    body = section(entry, meta)
    config.DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DRAFT_DIR / f"{slug}.md"
    path.write_text("\n".join(head) + body, encoding="utf-8")
    return path


def has_draft(entry: dict) -> bool:
    return (config.DRAFT_DIR / f"draft-{entry.get('out', '')}.md").exists()


def pending_drafts(parms: list[str] | None, node: str | None) -> list[dict]:
    rows = [e for e in ledger.load().get("entries", {}).values()
            if e.get("status") == "shot" and e.get("out")]
    if parms:
        wanted = {ledger.resolve(name, node) for name in parms}
        rows = [e for e in rows if (e["node"], e["parm"]) in wanted]
    rows.sort(key=lambda e: (e.get("node", ""), e.get("parm", "")))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="撮影済みから記事の下書きを作る")
    ap.add_argument("--parm", action="append",
                    help="この候補だけ（カンマ区切りと複数指定ができる）")
    ap.add_argument("--node", help="同名が複数のノードにあるときだけ必要")
    ap.add_argument("--force", action="store_true",
                    help="すでにある下書きを作り直す（人が書き足した内容は消える）")
    args = ap.parse_args()

    names = [p.strip() for p in ",".join(args.parm or []).split(",") if p.strip()]
    rows = pending_drafts(names or None, args.node)
    if not rows:
        print("下書きを作れる撮影済みはありません。"
              "（`shoot.py` で本撮りすると溜まります）")
        return 0

    made, skipped = [], []
    for entry in rows:
        # **人が書き足した下書きを黙って上書きしない。** 下書きは
        # 「機械が書いた土台に人が言葉を足す」場所なので、消えると手戻りになる。
        if has_draft(entry) and not args.force:
            skipped.append(entry)
            continue

        meta_path = config.MEDIA_DIR / f"{entry['out']}.json"
        if not meta_path.exists():
            print(f"  {entry['parm']}: {meta_path} がありません（撮り直してください）")
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        made.append((entry, write_draft(entry, meta)))

    for entry, path in made:
        print(f"  下書き: {path.relative_to(config.ROOT)}  （{entry['parm']}）")
    if skipped:
        print(f"  既にある下書きは触っていません: "
              f"{', '.join(e['parm'] for e in skipped)}（作り直すなら --force）")
    if made:
        print(f"\n{len(made)} 件。動画を見て言葉を足す:")
        print(f"  {config.BASE_URL}/  （serve.py を起動しておくこと。下書きはここにしか出ない）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
