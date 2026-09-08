"""開発用サーバー。保存を検知して再ビルドし、ブラウザを自動リロードする。

本番（GitHub Pages）はリポジトリ名のサブパス配下に公開されるため、ここでも
同じ BASE_URL の下で配信して条件を揃える。ルートで配信してしまうと、
「ローカルでは動くのに公開すると CSS も動画も 404」という典型的な事故を
見逃すことになる。
"""

from __future__ import annotations

import http.server
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build as builder  # noqa: E402
import config  # noqa: E402

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
                f'<figure><img src="{PREFIX}/preview/{p.name}?t={int(p.stat().st_mtime)}" '
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
        # ディレクトリを抜けられないようファイル名だけを使う
        safe = Path(name).name
        path = config.CACHE_DIR / "preview" / safe
        if not path.is_file():
            self.send_error(404, "preview not found")
            return
        data = path.read_bytes()
        ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_head(self):
        """HTML にだけ自動リロード用スクリプトを差し込む。"""
        path = self.translate_path(self.path)
        p = Path(path)
        if p.is_dir():
            p = p / "index.html"
        if p.suffix != ".html" or not p.exists():
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
        print("(Ctrl+C で終了)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n終了しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
