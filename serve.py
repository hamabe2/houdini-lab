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
        print(f"http://127.0.0.1:{PORT}{PREFIX}/  (Ctrl+C で終了)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n終了しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
