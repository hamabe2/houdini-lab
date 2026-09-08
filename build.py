"""content/ から site/ を生成する静的サイトビルダー。

依存は markdown / jinja2 / PyYAML の3つだけ。Node.js は使わない。
素材生成側（hython / ffmpeg）と言語が揃い、数年放置してもビルドが壊れない
ことを優先している。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# :::compare <id>   — 記事本文に比較ビューアを埋め込む独自記法
COMPARE = re.compile(r"^:::compare\s+([A-Za-z0-9_-]+)\s*$", re.MULTILINE)


class BuildError(RuntimeError):
    pass


def slugify_unicode(value: str, separator: str) -> str:
    """日本語見出しでも意味のあるアンカーを作る。

    markdown 既定の slugify は非 ASCII を捨てるため、日本語の見出しが
    すべて _1 / _2 のような id になってしまう。
    """
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    return re.sub(r"[%s\s]+" % re.escape(separator + " "), separator, value, flags=re.UNICODE)


@dataclass
class Page:
    slug: str
    title: str
    node: str = ""
    context: str = ""
    houdini_version: str = config.HOUDINI_VERSION
    updated: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    viewers: list[dict] = field(default_factory=list)
    html: str = ""


def load_meta(media_id: str) -> dict:
    """media/<id>.json を読む。無ければ何が足りないかを具体的に伝える。"""
    path = config.MEDIA_DIR / f"{media_id}.json"
    if not path.exists():
        raise BuildError(
            f"比較ビューアのメタデータが見つかりません: {path}\n"
            f"  tools/sweep.py で '{media_id}' を書き出してください。"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BuildError(f"{path} が壊れています: {e}") from e


def expand_compare(text: str, viewers: list[dict]) -> str:
    """:::compare <id> を <param-compare> に展開する。

    アンカー id は JSON の parm から自動生成する。手でアンカーを書かせない
    ことで、記事とメタデータのずれを構造的に防ぐ。
    """

    def repl(m: re.Match[str]) -> str:
        media_id = m.group(1)
        meta = load_meta(media_id)
        parm = meta.get("parm") or media_id
        label = meta.get("label") or parm
        viewers.append({"parm": parm, "label": label, "id": media_id})
        src = config.media_url(f"{media_id}.json")
        return (
            f'<section class="compare" id="{parm}">\n'
            f'<param-compare src="{src}"></param-compare>\n'
            f"</section>"
        )

    return COMPARE.sub(repl, text)


def read_page(path: Path) -> Page:
    raw = path.read_text(encoding="utf-8")
    m = FRONT_MATTER.match(raw)
    if not m:
        raise BuildError(
            f"{path} に front matter がありません。\n"
            "  ファイル先頭を --- で囲んだ YAML から始めてください。"
        )
    fm = yaml.safe_load(m.group(1)) or {}
    body = raw[m.end():]

    if "title" not in fm:
        raise BuildError(f"{path}: front matter に title がありません")

    viewers: list[dict] = []
    body = expand_compare(body, viewers)

    md = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"slugify": slugify_unicode}},
    )
    html = md.convert(body)

    updated = fm.get("updated", "")
    return Page(
        slug=path.stem,
        title=fm["title"],
        node=fm.get("node", ""),
        context=fm.get("context", ""),
        houdini_version=str(fm.get("houdini_version", config.HOUDINI_VERSION)),
        updated=str(updated) if updated else "",
        description=fm.get("description", ""),
        tags=list(fm.get("tags", []) or []),
        viewers=viewers,
        html=html,
    )


def asset_version() -> str:
    """assets/ の更新時刻からキャッシュバスターを作る。

    CSS/JS を更新したのにブラウザが古い版を使い続ける、という
    原因の分かりにくい不具合を防ぐ。
    """
    if not config.ASSET_DIR.exists():
        return "0"
    latest = max(
        (f.stat().st_mtime for f in config.ASSET_DIR.rglob("*") if f.is_file()),
        default=0.0,
    )
    return str(int(latest))


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        url=config.url,
        asset_v=asset_version(),
        site_title=config.SITE_TITLE,
        site_description=config.SITE_DESCRIPTION,
        lang=config.SITE_LANG,
        cf_token=config.CF_ANALYTICS_TOKEN,
        page_title=None,
        page_description=None,
    )
    return env


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def build() -> int:
    out = config.OUTPUT_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    env = make_env()

    node_dir = config.CONTENT_DIR / "nodes"
    sources = sorted(node_dir.glob("*.md")) if node_dir.exists() else []
    pages = [read_page(p) for p in sources]
    pages.sort(key=lambda p: p.title.lower())

    # ノードページ
    tmpl_node = env.get_template("node.html")
    for page in pages:
        write(
            out / "nodes" / page.slug / "index.html",
            tmpl_node.render(page=page, page_title=page.title, page_description=page.description),
        )

    # トップとノード一覧
    tmpl_index = env.get_template("index.html")
    write(out / "index.html", tmpl_index.render(pages=pages))
    write(out / "nodes" / "index.html", tmpl_index.render(pages=pages, page_title="ノード"))

    # タグ
    tags: dict[str, list[Page]] = {}
    for page in pages:
        for tag in page.tags:
            tags.setdefault(tag, []).append(page)

    tmpl_tag = env.get_template("taglist.html")
    write(
        out / "tags" / "index.html",
        tmpl_tag.render(tags=sorted(tags.items()), tag=None, page_title="タグ"),
    )
    for tag, items in tags.items():
        write(
            out / "tags" / tag / "index.html",
            tmpl_tag.render(pages=items, tag=tag, page_title=f"タグ: {tag}"),
        )

    # 静的ファイル
    copy_tree(config.ASSET_DIR, out / "assets")
    # MEDIA_BASE が外部（R2 など）を指しているときは同梱しない
    if not config.MEDIA_BASE.startswith("http"):
        copy_tree(config.MEDIA_DIR, out / "media")

    # GitHub Pages に Jekyll 処理をさせない（_ 始まりのパスが消えるのを防ぐ）
    write(out / ".nojekyll", "")

    n_viewers = sum(len(p.viewers) for p in pages)
    print(f"ビルド完了: {len(pages)} ページ / {n_viewers} ビューア / {len(tags)} タグ -> {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(build())
    except BuildError as e:
        print(f"エラー: {e}", file=sys.stderr)
        raise SystemExit(1)
