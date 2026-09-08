# Houdini Lab

Houdini のパラメータを実際に振って、**見た目の変化で理解する**ナレッジサイトです。

公式ドキュメントは「そのパラメータが何をするか」は書いていますが、「**どのくらい変えるとどうなるか**」は書いていません。ここはその差分を埋めることを目的にしています。

サイト: https://hamabe2.github.io/houdini-lab/

## 何が置いてあるか

パラメータごとに、値を段階的に変えたシミュレーション結果を撮影し、**スライダーで切り替えられるビューア**として公開しています。パラメータを変えても再生時刻は保たれるので、常に「同じ瞬間の別の値」を比べられます。

検証に使った Houdini のバージョンは各ページに明記しています。

## 仕組み

```
Houdini (hython)          ffmpeg              静的サイト
  .hip を開いて       →   PNG 連番を     →    ブラウザで
  パラメータを振り        1本の mp4 に        currentTime を
  PNG 連番を書き出す      連結               シークして切替
```

1本の動画に1つのパラメータの全段階を連結し、ビューアは再生位置をジャンプするだけで値を切り替えます。動画ファイルは1つなのでリクエストは1回、切り替えはデコード済みバッファ内のシークになるため待ち時間がありません。

## ディレクトリ

| パス | 中身 |
|---|---|
| `content/nodes/` | ノード別の記事（Markdown） |
| `media/` | 生成された mp4 とメタ JSON |
| `assets/param-compare.js` | 比較ビューア（依存ゼロの Web Component） |
| `tools/sweep.py` | パラメータを振って撮影する CLI |
| `tools/encode.py` | PNG 連番 → 連結 mp4 + JSON |
| `scenes/template.hip` | カメラ・ライトを固定した共通ルック |
| `build.py` | `content/` → `site/` の静的生成 |

## 開発

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

.venv/Scripts/python.exe serve.py     # ローカルプレビュー
.venv/Scripts/python.exe build.py     # site/ を生成
```

素材の撮影には Houdini 21.0.729 と ffmpeg が必要です。

```bash
# まず draft で「振っても見た目が変わらないか」を数分で確認する
python tools/sweep.py --hip scenes/vellum_cloth.hip \
  --node /obj/cloth/vellumconstraints1 \
  --parm stretchstiffness --values 1e5,1e6,1e7,1e8,1e9 \
  --frames 1-48 --out vellum-cloth-stretch --draft

# 差が出たものだけ本番解像度で撮り直す
python tools/sweep.py ... （--draft を外す）
```
