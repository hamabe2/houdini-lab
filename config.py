"""サイト全体の設定。

ここは「後から変わりうるもの」を1か所に集める場所。特に BASE_URL と
MEDIA_BASE は、公開先やストレージを移すときの唯一の変更点になる。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CONTENT_DIR = ROOT / "content"
TEMPLATE_DIR = ROOT / "templates"
ASSET_DIR = ROOT / "assets"
MEDIA_DIR = ROOT / "media"
SCENE_DIR = ROOT / "scenes"
CACHE_DIR = ROOT / "tools" / "_cache"
OUTPUT_DIR = ROOT / "site"

# --- サイト情報 -------------------------------------------------------------

SITE_TITLE = "Houdini Lab"
SITE_DESCRIPTION = "Houdini のパラメータを実際に振って、見た目の変化で理解するサイト"
SITE_LANG = "ja"

# 検証に使う Houdini。記事側で上書きできるが、既定はこれ。
# ページに必ず表示する（挙動はバージョンで変わるため）。
HOUDINI_VERSION = "21.0.729"

# --- URL ------------------------------------------------------------------

# GitHub Pages はリポジトリ名のサブパス配下に公開される:
#   https://hamabe2.github.io/houdini-lab/
# そのため CSS/JS/動画へのリンクを "/assets/..." と絶対パスで書くと
# ルート直下を指してしまい 404 になる。全リンクの先頭にこれを付ける。
# serve.py も同じサブパスで配信し、ローカルと本番の条件を揃える。
BASE_URL = os.environ.get("HOUDINI_LAB_BASE_URL", "/houdini-lab")

# 動画の配信元。GitHub Pages の 1GB 上限に近づいたら Cloudflare R2 の
# URL に差し替える。JSON の "src" はファイル名しか持たないので、
# 移行時の変更はこの1行で済む。
MEDIA_BASE = os.environ.get("HOUDINI_LAB_MEDIA_BASE", f"{BASE_URL}/media")

# --- 動画の既定値 -----------------------------------------------------------

# 解像度は固定。可変にすると記事に並べたときに揃わずレイアウトが崩れる。
VIDEO_WIDTH = 960
VIDEO_HEIGHT = 540
VIDEO_FPS = 24
VIDEO_CRF = 21

# draft 撮影（--draft）。解像度・フレーム数・段階数をすべて落として
# 「振っても見た目が変わらないパラメータ」を本番前に切り捨てるためのもの。
DRAFT_WIDTH = 480
DRAFT_HEIGHT = 270
DRAFT_FRAMES = 16
DRAFT_STEPS = 3

# --- 外部ツール -------------------------------------------------------------

HOUDINI_ROOT = Path(
    os.environ.get(
        "HOUDINI_LAB_HFS",
        r"C:\Program Files\Side Effects Software\Houdini 21.0.729",
    )
)
HYTHON = HOUDINI_ROOT / "bin" / "hython.exe"
FFMPEG = os.environ.get("HOUDINI_LAB_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("HOUDINI_LAB_FFPROBE", "ffprobe")

# 共通ルックのテンプレート。全シーンはこの複製から始める。
TEMPLATE_HIP = SCENE_DIR / "template.hip"
DEFAULT_CAMERA = "CAM_main"

# --- シミュレーションキャッシュ ---------------------------------------------

# Vellum/Pyro のキャッシュは数百MB〜数十GB になる。リポジトリは Public な
# ので、中に置くと誤コミットで GitHub の 1GB 上限を即座に超える。
# 必ずリポジトリの外に出す。
# Houdini 側からは $HLCACHE で参照できる（sweep.py が環境変数で渡す）。
CACHE_ROOT = Path(os.environ.get("HOUDINI_LAB_CACHE", r"D:\houdini-cache\houdini-lab"))

# このドライブの空きがこれを下回ったら警告する（GB）。
# D: は空きに余裕がないため、埋まる前に気づけるようにする。
CACHE_FREE_WARN_GB = 30

# --- アクセス解析 -----------------------------------------------------------

# Cloudflare Web Analytics のトークン。未設定ならスニペットを出力しない。
CF_ANALYTICS_TOKEN = os.environ.get("HOUDINI_LAB_CF_TOKEN", "")


def media_url(filename: str) -> str:
    """動画・JSON のファイル名を公開 URL に変換する。"""
    return f"{MEDIA_BASE}/{filename}"


def url(path: str = "") -> str:
    """サイト内パスに BASE_URL を付ける。"""
    return f"{BASE_URL}/{path.lstrip('/')}" if path else f"{BASE_URL}/"
