"""開発用サーバー。保存を検知して再ビルドし、ブラウザを自動リロードする。

本番（GitHub Pages）はリポジトリ名のサブパス配下に公開されるため、ここでも
同じ BASE_URL の下で配信して条件を揃える。ルートで配信してしまうと、
「ローカルでは動くのに公開すると CSS も動画も 404」という典型的な事故を
見逃すことになる。
"""

from __future__ import annotations

import http.server
import json
import socketserver
import sys
import threading
import time
from html import escape as esc
from pathlib import Path
from urllib.parse import quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

import build as builder  # noqa: E402
import config  # noqa: E402
import ledger  # noqa: E402
import review  # noqa: E402

PORT = 8765
WATCH = ["content", "templates", "assets", "media", "config.py", "build.py"]
PREFIX = config.BASE_URL.rstrip("/")

RELOAD_JS = """
<script>
(function () {
  let known = null;
  setInterval(async () => {
    try {
      const r = await fetch("%s/__gen__", { cache: "no-store" });
      const gen = await r.text();
      if (known === null) known = gen;
      else if (gen !== known) location.reload();
    } catch (e) {}
  }, 700);
})();
</script>
""" % PREFIX

generation = 0
lock = threading.Lock()

# Range を付けずに全体を返してよいファイル。HTML はリロード用スクリプトを
# 差し込んで長さが変わるので、部分応答の対象にしない。
_NO_RANGE_SUFFIXES = (".html",)


class RangeReader:
    """指定バイト数で止まる読み出しラッパー。

    do_GET は send_head が返したファイルを EOF まで copyfile する。部分応答
    ではそれだと範囲外まで送ってしまうので、残りバイト数で頭打ちにする。
    """

    def __init__(self, fh, length: int):
        self._fh = fh
        self._left = length

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        if size is None or size < 0:
            size = self._left
        data = self._fh.read(min(size, self._left))
        self._left -= len(data)
        return data

    def close(self) -> None:
        self._fh.close()


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """'bytes=START-END' を (start, end) に変換する。end は含む。

    範囲が不正・複数指定・ファイル外なら None を返し、呼び出し側は
    通常の 200 応答にフォールバックする。
    """
    if not header or not header.strip().lower().startswith("bytes="):
        return None
    spec = header.split("=", 1)[1].strip()
    if "," in spec:  # 複数範囲は使わないので対応しない
        return None

    start_s, _, end_s = spec.partition("-")
    try:
        if not start_s:                      # bytes=-500 → 末尾 500 バイト
            length = int(end_s)
            if length <= 0:
                return None
            start, end = max(0, size - length), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return None

    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def snapshot() -> dict[str, float]:
    stamps: dict[str, float] = {}
    for name in WATCH:
        path = config.ROOT / name
        if path.is_file():
            stamps[str(path)] = path.stat().st_mtime
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file():
                    stamps[str(f)] = f.stat().st_mtime
    return stamps


def watcher() -> None:
    global generation
    last = snapshot()
    while True:
        time.sleep(0.5)
        now = snapshot()
        if now == last:
            continue
        last = now
        try:
            with lock:
                builder.build()
                generation += 1
        except Exception as e:  # ビルドが落ちてもサーバーは生かす
            print(f"再ビルド失敗: {e}", file=sys.stderr)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(config.OUTPUT_DIR), **kwargs)

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass

    def end_headers(self):
        # 開発中は CSS/JS が古いまま残ると原因の切り分けが難しくなるので
        # すべてキャッシュさせない
        self.send_header("Cache-Control", "no-store, must-revalidate")
        # シーク可能であることを明示する。ブラウザはこれが無いと動画を
        # 「先頭から流すだけ」のリソースとして扱う。
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self):
        # シーン調整中に preview.py が吐いた画像を、ブラウザですぐ確認できる
        # ようにする。site/ とは別扱いなのでビルドの影響を受けず、撮影中でも
        # 見られる。
        if self.path.split("?")[0].rstrip("/") == f"{PREFIX}/preview".rstrip("/"):
            self.send_preview_index()
            return
        if self.path.startswith(f"{PREFIX}/preview/"):
            self.send_preview_file(self.path[len(f"{PREFIX}/preview/"):].split("?")[0])
            return

        # setup_sheet.py が出した「sim 前の確認用」一覧。
        if self.path.split("?")[0].rstrip("/") == f"{PREFIX}/setup".rstrip("/"):
            self.send_setup_index()
            return
        if self.path.startswith(f"{PREFIX}/setup/"):
            self.send_cache_file(
                config.CACHE_DIR / "setup",
                self.path[len(f"{PREFIX}/setup/"):].split("?")[0],
            )
            return

        # screen.py の判定を人が承認する画面。**絵を見ている場所でそのまま
        # 決められるようにする。**判断材料（絵・有効域・dB）と入力口が
        # 離れていると、結局1件ずつ端末とブラウザを往復することになる。
        if self.path.split("?")[0].rstrip("/") == f"{PREFIX}/review".rstrip("/"):
            self.send_review_index()
            return
        if self.path.startswith(f"{PREFIX}/review/"):
            self.send_cache_file(
                review.REVIEW_DIR,
                self.path[len(f"{PREFIX}/review/"):].split("?")[0],
            )
            return

        if self.path.rstrip("/") == f"{PREFIX}/__gen__".rstrip("/"):
            body = str(generation).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # BASE_URL の外に来たらサイトのトップへ送る
        if PREFIX and not self.path.startswith(PREFIX):
            self.send_response(302)
            self.send_header("Location", f"{PREFIX}/")
            self.end_headers()
            return

        super().do_GET()

    def translate_path(self, path: str) -> str:
        if PREFIX and path.startswith(PREFIX):
            path = path[len(PREFIX):] or "/"
        return super().translate_path(path)

    # --- 部分応答 -----------------------------------------------------------

    def send_partial(self, p: Path):
        """Range リクエストに 206 で応じる。対象外なら None を返す。

        **これが無いと動画のシークが壊れる。** SimpleHTTPRequestHandler は
        Range を無視して 200 で全体を返すため、ブラウザは「シークできない
        リソース」と判断する。すると動画は先頭からしか再生できず、
        比較ビューアは最初のセグメント（p=0）以外が動かなくなる。
        しかも本番の GitHub Pages は Range に対応しているので、
        **ローカルでだけ再現する**という一番たちの悪い出方をする。
        """
        if not p.is_file() or p.suffix.lower() in _NO_RANGE_SUFFIXES:
            return None

        size = p.stat().st_size
        rng = parse_range(self.headers.get("Range", ""), size)
        if rng is None:
            # Range 無し。シーク可能であることだけ伝えて通常応答に任せる。
            return None

        start, end = rng
        length = end - start + 1
        fh = open(p, "rb")
        fh.seek(start)

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(p)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()  # Accept-Ranges はここで付く
        return RangeReader(fh, length)

    # --- プレビュー ---------------------------------------------------------

    def send_preview_index(self) -> None:
        """preview.py が出した画像を新しい順に並べて表示する。"""
        pdir = config.CACHE_DIR / "preview"
        images = []
        if pdir.exists():
            images = sorted(
                (p for p in pdir.iterdir() if p.suffix.lower() in (".png", ".jpg")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

        if images:
            cards = "\n".join(
                f'<figure><img src="{PREFIX}/preview/{quote(p.name)}'
                f'?t={int(p.stat().st_mtime)}" '
                f'alt="{p.name}">'
                f"<figcaption>{p.name}"
                f'<span>{time.strftime("%H:%M:%S", time.localtime(p.stat().st_mtime))}</span>'
                f"</figcaption></figure>"
                for p in images
            )
        else:
            cards = (
                '<p class="empty">まだ画像がありません。<br>'
                "<code>python tools/preview.py --hip scenes/vellum_cloth.hip --frame 24</code>"
                "<br>を実行すると、ここに出ます。</p>"
            )

        html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>プレビュー | Houdini Lab</title>
<style>
  body {{ margin:0; padding:1.5rem; background:#12141a; color:#e6e8eb;
         font-family:system-ui,"Segoe UI","Yu Gothic UI",sans-serif; }}
  h1 {{ font-size:1.05rem; margin:0 0 .3rem; }}
  .hint {{ color:#9aa0a6; font-size:.82rem; margin:0 0 1.5rem; }}
  a {{ color:#6ea8fe; }}
  figure {{ margin:0 0 1.75rem; }}
  img {{ width:100%; max-width:960px; display:block; border-radius:8px;
        background:#000; border:1px solid #2a2e35; }}
  figcaption {{ font-size:.8rem; color:#9aa0a6; margin-top:.4rem;
                display:flex; gap:.75rem; }}
  figcaption span {{ opacity:.65; }}
  code {{ background:#22262d; padding:.12em .4em; border-radius:4px; }}
  .empty {{ color:#9aa0a6; line-height:2; }}
</style></head><body>
<h1>プレビュー</h1>
<p class="hint">新しい順。5秒ごとに自動更新します &middot;
<a href="{PREFIX}/">サイトへ</a></p>
{cards}
<script>setTimeout(() => location.reload(), 5000);</script>
</body></html>"""

        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_preview_file(self, name: str) -> None:
        self.send_cache_file(config.CACHE_DIR / "preview", name)

    def send_cache_file(self, root: Path, name: str) -> None:
        # **URL の % を戻してから探す。** ブラウザはファイル名の非 ASCII や
        # 空白をパーセントエンコードして送ってくる。戻さずに探すと
        # 「画像だけ 404」になり、ページは出るのに絵が出ない状態になる
        # （静的ファイルは SimpleHTTPRequestHandler が自分で戻すので、
        # この分岐だけが取りこぼしていた）。
        name = unquote(name)
        # ディレクトリを抜けられないようファイル名だけを使う
        path = root / Path(name).name
        if not path.is_file():
            self.send_error(404, "not found")
            return
        data = path.read_bytes()
        ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- セットアップ一覧 ----------------------------------------------------

    def send_setup_index(self) -> None:
        """setup_sheet.py の結果を、パラメータごとに横並びで見せる。

        判断してほしいのは「この見せ方でいいか」なので、値は横に並べて
        差が読み取れる形にする。画像は RGBA のままなのでブラウザが合成する。
        **ページの背景色を動画の合成先と同じにして、見え方を揃える。**
        """
        meta_path = config.CACHE_DIR / "setup" / "sheet.json"
        if not meta_path.is_file():
            body = (
                '<p class="empty">まだありません。<br>'
                "<code>python tools/setup_sheet.py --hip scenes/vellum_cloth.hip "
                '--probe "/obj/SUBJECT/CONSTRAINTS:bendstiffness=0,1,10"</code>'
                "<br>を実行すると、ここに出ます。</p>"
            )
            info = ""
        else:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            info = (
                f"{Path(meta['hip']).name} &middot; frame {meta['frame']} "
                f"&middot; {meta['generated']}"
            )
            blocks = []
            for g in meta["groups"]:
                cells = "\n".join(
                    f'<figure><img src="{PREFIX}/setup/{quote(c["img"])}" alt="{c["value"]}">'
                    f'<figcaption>{c["value"]}'
                    f'{" <b>既定</b>" if c["value"] == g["default"] else ""}'
                    f"</figcaption></figure>"
                    for c in g["cells"]
                )
                blocks.append(
                    f'<section><h2>{g["parm"]}</h2>'
                    f'<p class="node">{g["node"]} &middot; 既定 {g["default"]}</p>'
                    f'<div class="row">{cells}</div></section>'
                )
            body = "\n".join(blocks)

        html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>セットアップ確認 | Houdini Lab</title>
<style>
  body {{ margin:0; padding:1.5rem; background:#12141a; color:#e6e8eb;
         font-family:system-ui,"Segoe UI","Yu Gothic UI",sans-serif; }}
  h1 {{ font-size:1.05rem; margin:0 0 .3rem; }}
  h2 {{ font-size:.95rem; margin:0 0 .2rem; font-family:ui-monospace,monospace; }}
  .hint, .node {{ color:#9aa0a6; font-size:.8rem; margin:0 0 1rem; }}
  .node {{ margin:0 0 .5rem; }}
  a {{ color:#6ea8fe; }}
  section {{ margin:0 0 2rem; }}
  .row {{ display:flex; gap:.75rem; overflow-x:auto; padding-bottom:.4rem; }}
  figure {{ margin:0; flex:0 0 auto; }}
  /* 画像は RGBA。動画と同じ色に合成して見え方を揃える。 */
  img {{ display:block; width:320px; border-radius:6px;
        background:{config.VIDEO_BG.replace("0x", "#")}; border:1px solid #2a2e35; }}
  figcaption {{ font-size:.78rem; color:#9aa0a6; margin-top:.3rem;
                font-variant-numeric:tabular-nums; }}
  figcaption b {{ color:#e6e8eb; font-weight:600; }}
  code {{ background:#22262d; padding:.12em .4em; border-radius:4px; }}
  .empty {{ color:#9aa0a6; line-height:2; }}
</style></head><body>
<h1>セットアップ確認</h1>
<p class="hint">{info} &middot; <a href="{PREFIX}/">サイト</a>
&middot; <a href="{PREFIX}/preview/">プレビュー</a></p>
{body}
</body></html>"""

        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- 承認 ---------------------------------------------------------------

    def send_review_index(self) -> None:
        """screen.py の判定を人が承認する画面。

        **まとめて決めるための画面。** 1件ずつ聞かずに済ませる条件は、
        判断材料（絵・有効域・隣どうしの dB）が1画面に揃っていることと、
        決定をまとめて1回で送れること。

        `/setup/` と違って **`screening.json` を書き換える**ので、
        これはローカル専用の口。公開されるのは `site/` の中身だけで、
        この画面は `serve.py` が動いているときにしか存在しない。
        """
        rows = review.awaiting()
        if not rows:
            body = (
                '<p class="empty">承認待ちはありません。<br>'
                "<code>python tools/screen_loop.py --hip scenes/vellum_cloth.hip "
                "--count 5 --camera /obj/CAM_angle</code><br>"
                "を回すと、ここに溜まります。</p>"
            )
        else:
            blocks = []
            for row in rows:
                proposal = row.get("proposal") or {}
                rng = proposal.get("range") or []
                figures = []
                for img in row["images"]:
                    caption = esc(str(img["display"]))
                    if img["is_default"]:
                        caption += " <b>既定</b>"
                    if img["broken"]:
                        caption += f' <em>{esc(img["broken"])}</em>'
                    # **値を指せる口。** 「ここで破綻している」は自由文だと
                    # 機械に効かないが、値を指してもらえば --cap に変わる。
                    figures.append(
                        f'<figure><img src="{PREFIX}/review/{quote(img["name"])}" '
                        f'alt="{esc(str(img["value"]))}">'
                        f"<figcaption>{caption}</figcaption>"
                        f'<label class="drop"><input type="checkbox" class="exclude" '
                        f'value="{esc(str(img["value"]))}">この値は除く</label>'
                        f"</figure>"
                    )
                cells = "\n".join(figures) or (
                    '<p class="empty">絵がありません'
                    "（screen_loop.py 経由で撮ると残ります）</p>"
                )

                key = esc(row["key"])
                extra = []
                if len(rng) == 2:
                    extra.append(f"有効域 {rng[0]} 〜 {rng[1]}")
                if proposal.get("open_high") or proposal.get("open_low"):
                    extra.append("<b>端が見つかっていない</b>")
                blocks.append(
                    f'<section data-key="{key}">'
                    f'<h2>{esc(row["parm"])}'
                    f'<span class="verdict">{esc(row.get("verdict", ""))}</span></h2>'
                    f'<p class="node">{esc(row.get("label", ""))} &middot; {esc(row["node"])}'
                    f'{" &middot; " + " &middot; ".join(extra) if extra else ""}</p>'
                    f'<p class="node">{esc(row.get("reason", ""))}</p>'
                    f'<div class="row">{cells}</div>'
                    f'<div class="decide">'
                    f'<label><input type="radio" name="{key}" value="hold" checked>保留</label>'
                    f'<label><input type="radio" name="{key}" value="approved">承認</label>'
                    f'<label><input type="radio" name="{key}" value="retry">やり直し</label>'
                    f'<label><input type="radio" name="{key}" value="rejected">却下</label>'
                    f'<input type="text" class="why" '
                    f'placeholder="理由（やり直し・却下には必須）">'
                    f"</div>"
                    f'<p class="node hintline">'
                    f"やり直し = 候補は残す。刻みや範囲を変えて撮り直す"
                    f"（除いた値より先は次から撮らない）&nbsp;/&nbsp;"
                    f"却下 = この候補自体をやめる（二度と出てこない）</p>"
                    f"</section>"
                )
            body = "\n".join(blocks)

        html_text = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>承認 | Houdini Lab</title>
<style>
  body {{ margin:0; padding:1.5rem 1.5rem 5rem; background:#12141a; color:#e6e8eb;
         font-family:system-ui,"Segoe UI","Yu Gothic UI",sans-serif; }}
  h1 {{ font-size:1.05rem; margin:0 0 .3rem; }}
  h2 {{ font-size:.95rem; margin:0 0 .2rem; font-family:ui-monospace,monospace; }}
  .hint, .node {{ color:#9aa0a6; font-size:.8rem; margin:0 0 1rem; }}
  .node {{ margin:0 0 .4rem; }}
  a {{ color:#6ea8fe; }}
  section {{ margin:0 0 2rem; border-top:1px solid #2a2e35; padding-top:1rem; }}
  .verdict {{ margin-left:.6rem; font-family:system-ui; font-size:.75rem;
              color:#0d0f14; background:#9aa0a6; border-radius:99px; padding:.1rem .5rem; }}
  .row {{ display:flex; gap:.75rem; overflow-x:auto; padding-bottom:.4rem; }}
  figure {{ margin:0; flex:0 0 auto; }}
  /* 画像は合成済みだが、地色は動画と揃えておく。 */
  img {{ display:block; width:260px; border-radius:6px;
        background:{config.VIDEO_BG.replace("0x", "#")}; border:1px solid #2a2e35; }}
  figcaption {{ font-size:.78rem; color:#9aa0a6; margin-top:.3rem;
                font-variant-numeric:tabular-nums; }}
  figcaption b {{ color:#e6e8eb; font-weight:600; }}
  figcaption em {{ color:#f0a35e; font-style:normal; }}
  .drop {{ display:flex; gap:.3rem; align-items:center; margin-top:.25rem;
           font-size:.75rem; color:#9aa0a6; cursor:pointer; }}
  .hintline {{ margin:.45rem 0 0; font-size:.75rem; color:#6b7280; }}
  .decide {{ display:flex; gap:1rem; align-items:center; margin-top:.6rem;
             font-size:.85rem; flex-wrap:wrap; }}
  .decide label {{ display:flex; gap:.3rem; align-items:center; cursor:pointer; }}
  .why {{ flex:1 1 18rem; background:#1b1e25; color:#e6e8eb;
          border:1px solid #2a2e35; border-radius:5px; padding:.35rem .5rem; }}
  code {{ background:#22262d; padding:.12em .4em; border-radius:4px; }}
  .empty {{ color:#9aa0a6; line-height:2; }}
  .bar {{ position:fixed; left:0; right:0; bottom:0; padding:.7rem 1.5rem;
          background:#191c22; border-top:1px solid #2a2e35; display:flex;
          gap:1rem; align-items:center; }}
  button {{ background:#2f6feb; color:#fff; border:0; border-radius:6px;
            padding:.45rem 1.1rem; font-size:.9rem; cursor:pointer; }}
  button:disabled {{ background:#2a2e35; color:#6b7280; cursor:default; }}
  #status {{ color:#9aa0a6; font-size:.82rem; }}
</style></head><body>
<h1>承認</h1>
<p class="hint">承認待ち {len(rows)} 件 &middot; <a href="{PREFIX}/setup/">セットアップ</a>
&middot; <a href="{PREFIX}/">サイト</a></p>
{body}
<div class="bar">
  <button id="send">決定を送る</button>
  <span id="status">保留のままのものは何も変わりません。</span>
</div>
<script>
document.getElementById("send").addEventListener("click", async (ev) => {{
  const decisions = [];
  for (const section of document.querySelectorAll("section[data-key]")) {{
    const picked = section.querySelector("input[type=radio]:checked").value;
    if (picked === "hold") continue;
    const note = section.querySelector(".why").value.trim();
    if (picked !== "approved" && !note) {{
      document.getElementById("status").textContent =
        "理由が要ります: " + section.dataset.key;
      return;
    }}
    const excluded = [...section.querySelectorAll(".exclude:checked")]
      .map((el) => parseFloat(el.value));
    decisions.push({{
      key: section.dataset.key, status: picked, note: note, excluded: excluded,
    }});
  }}
  if (!decisions.length) {{
    document.getElementById("status").textContent = "決まっているものがありません。";
    return;
  }}
  ev.target.disabled = true;
  document.getElementById("status").textContent = "送っています…";
  try {{
    const r = await fetch("{PREFIX}/review/decide", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ decisions }}),
    }});
    const out = await r.json();
    if (!r.ok) throw new Error(out.error || r.status);
    location.reload();
  }} catch (e) {{
    ev.target.disabled = false;
    document.getElementById("status").textContent = "失敗しました: " + e.message;
  }}
}});
</script>
</body></html>"""

        data = html_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        """承認画面からの決定を受ける。**ローカル専用の書き込み口。**"""
        if self.path.split("?")[0].rstrip("/") != f"{PREFIX}/review/decide":
            self.send_error(404, "not found")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            decisions = payload.get("decisions") or []

            # **全部検証してから、まとめて書く。** 途中で弾くと半分だけ
            # 適用された検証リストが残り、画面の表示と食い違う。
            prepared = []
            for item in decisions:
                node, _, parm = str(item["key"]).rpartition(":")
                status, note = item["status"], (item.get("note") or "").strip()
                if status != "approved" and not note:
                    raise ValueError(f"理由が要ります: {item['key']}")

                if status != "retry":
                    prepared.append((status, node, parm, note, None))
                    continue

                # **overrides は retry を呼ぶ前に作る。** retry は前の判定を
                # retry.previous へ畳んでしまうので、梯子を先に読む。
                entry = ledger.load()["entries"][ledger.key_of(node, parm)]
                overrides, ignored = review.overrides_from_excluded(
                    entry, item.get("excluded") or [],
                )
                if ignored:
                    values = ", ".join(review.fmt_value(v) for v in ignored)
                    raise ValueError(
                        f"{parm}: 除外に使えない値です（{values}）。"
                        "既定値そのものと梯子の外は境にできません"
                    )
                prepared.append((status, node, parm, note, overrides))

            done = 0
            for status, node, parm, note, overrides in prepared:
                if status == "retry":
                    ledger.retry(node, parm, note, overrides)
                else:
                    ledger.decide(node, parm, status, note)
                done += 1
            # 却下したぶんの絵は残しておく理由がない。
            review.prune()
            self.reply_json(200, {"ok": True, "count": done})
        except Exception as exc:
            self.reply_json(400, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def reply_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_head(self):
        """HTML にはリロード用スクリプトを差し込み、それ以外は Range に応じる。"""
        path = self.translate_path(self.path)
        p = Path(path)
        if p.is_dir():
            p = p / "index.html"
        if p.suffix != ".html" or not p.exists():
            head = self.send_partial(p)
            if head is not None:
                return head
            return super().send_head()

        content = p.read_text(encoding="utf-8")
        if "</body>" in content:
            content = content.replace("</body>", RELOAD_JS + "</body>")
        data = content.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))

        self.end_headers()
        import io

        return io.BytesIO(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    builder.build()
    threading.Thread(target=watcher, daemon=True).start()

    with Server(("127.0.0.1", PORT), Handler) as httpd:
        print(f"サイト     : http://127.0.0.1:{PORT}{PREFIX}/")
        print(f"プレビュー : http://127.0.0.1:{PORT}{PREFIX}/preview/")
        print(f"承認       : http://127.0.0.1:{PORT}{PREFIX}/review/")
        print("(Ctrl+C で終了)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n終了しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
