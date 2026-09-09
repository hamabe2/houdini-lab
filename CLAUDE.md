# houdini-lab

Houdini のパラメータを段階的に振り、スライダーで切り替えて比較できる形で公開するナレッジサイト。

- 公開URL: https://hamabe2.github.io/houdini-lab/
- リポジトリ: https://github.com/hamabe2/houdini-lab （**Public**）

## 応答スタイル

日本語で応答する。コード・パス・パラメータ名は英語のまま。

## このプロジェクトの仕組み

```
Houdini (hython)          ffmpeg              静的サイト
  .hip を開いて       →   PNG 連番を     →    ブラウザで
  パラメータを振り        1本の mp4 に        currentTime を
  PNG 連番を書き出す      連結               シークして切替
```

**動画1本 = パラメータ1つ。**その中にそのパラメータの全段階が入る。ビューアは
`currentTime = (p * frames_per_segment + t) / fps` で再生位置をジャンプして値を切り替える。

**パラメータを変えても再生時刻 t を保つ**のがこの設計の核心。値ごとに別の動画へ
切り替える方式では再生位置がずれ、比較として成立しない。

複数パラメータを1本にまとめない。全組み合わせは掛け算で爆発する
（3パラメータ×5段階の総当たりは125通り）。1パラメータずつなら 5+5+5=15 通り。

## よく使うコマンド

PowerShell から実行する。**Git Bash は `/obj/...` を Windows パスに変換してしまう**ので使わない。

```powershell
# 撮影（まず --draft で「振っても差が出ないパラメータ」を数分で切り捨てる）
.venv\Scripts\python.exe tools\sweep.py --hip scenes\vellum_cloth.hip `
  --node /obj/SUBJECT/CONSTRAINTS --parm bendstiffness `
  --values 0,0.1,1,5,10 --frames 1-48 --out vellum-cloth-bend `
  --label "Bend Stiffness" --default-index 2 --draft

# 1フレームだけ確認（カメラやライトの調整用。sweep を回すより圧倒的に速い）
.venv\Scripts\python.exe tools\preview.py --hip scenes\vellum_cloth.hip --frame 24 --bbox

# シーンの作り直し
& "C:\Program Files\Side Effects Software\Houdini 21.0.729\bin\hython.exe" tools\make_template_hip.py
& "C:\Program Files\Side Effects Software\Houdini 21.0.729\bin\hython.exe" tools\make_vellum_cloth_scene.py

# サイト＋プレビュー（1つ起動すれば両方見られる）
.venv\Scripts\python.exe serve.py
#   サイト     : http://127.0.0.1:8765/houdini-lab/
#   プレビュー : http://127.0.0.1:8765/houdini-lab/preview/
.venv\Scripts\python.exe build.py
```

記事に `:::compare <id>` と書くとビューアが埋め込まれ、アンカーは JSON の `parm` から自動生成される。
push すれば GitHub Actions が `build.py` を回して自動公開する。

**プレビュー画像をユーザーに見せる方法**: `SendUserFile` はこの環境（VSCode 拡張）では表示されない。
`Read` で画像を見られるのは自分だけで、ユーザーには見えていない。**必ずブラウザ経由で見せること。**

`serve.py` の `/houdini-lab/preview/` が `tools/_cache/preview` の画像を新しい順に並べ、
5秒ごとに自動更新する。`preview.py` を実行したら URL を伝えるだけでよい。
site/ とは別扱いなのでビルドの影響を受けず、撮影中でも見られる。

## Houdini 側の重要な前提

- **Houdini 21.0.729 を使う。** 22.0.429 も入っているが使わない。fxhoudinimcp は
  [Issue #12](https://github.com/healkeiser/fxhoudinimcp/issues/12) で 22 の GUI セッションが
  使用不能と報告されており（CLOSED / not planned）、修正は公表されていない。
- **ライセンスは Apprentice。** 全レンダー出力にウォーターマークが入る。Flipbook でも同じ。
  消す方法はライセンス条件に反するので取らない。
- **hython に flipbook は無い。** `hou.SceneViewer.flipbook()` はビューアを要求する。
  バッチ書き出しは OpenGL ROP を使う。
  ただし OpenGL ROP は「ビューポートの見た目」をジオメトリで模倣している
  （`template.hip` の `BACKDROP` / `GROUND` / `LIGHT_key` はその代用品）。
  **ビューポートそのままを撮る方針に切り替えるなら、houdini.exe を GUI で
  バッチ起動する経路がある**（`HOUDINI_PATH=<dir>;&` + `scripts/456.py` フック）。
  `FlipbookSettings` の必要な API は 21.0.729 に揃っていることを確認済み
  （`output` / `frameRange` / `useResolution` / `resolution` / `visibleObjects` /
  `beautyPassOnly` / `initializeSimulations`）。**未実装。**
- シミュレーションキャッシュは **`D:\houdini-cache\houdini-lab`**（リポジトリ外）。
  Public リポジトリに巨大ファイルが入る事故を構造的に防ぐため。$HLCACHE で参照できる。

## ハマりどころ（実際に踏んだもの）

| 症状 | 原因と対処 |
|---|---|
| 点を固定したのに布が落下し続ける | `pintoanimation` はターゲット入力への追従用。その場に固定するのは **`stopped`** |
| 固定対象の点が0個になる | `grid` の `orient` は **0=XY(垂直) / 1=YZ / 2=ZX(水平)**。0 は水平ではない |
| `substeps` を上げても効かない | **`dosubstep` トグルがオフだと数値が無視される。** Houdini はこの構造が多い。`do<parm>` の有無を疑う |
| 初速を与えても布が動かない | 面内方向に振っても stretch 拘束で伸びず動かない。**面に垂直な方向**に振る |
| オブジェクトが真っ黒 | ライト0灯だと OpenGL は真っ黒に落とす（ヘッドライト自動化は効かない）。また薄い面を裏側から見ていないか確認 |
| パラメータを変えても全部同じ映像 | sim キャッシュが残っている。`_hou_sweep.py` の `clear_sim_caches()` が担当。File Cache SOP が読み込みモードでも起きる |
| スライダーを動かすと別の値が出る | セグメント境界にキーフレームが無い。`encode.py` が `ffprobe` で毎回検証している |
| ビューアが1フレームで振動する | シーク位置の丸めで手前に落ちたとき巻き戻すと無限ループになる。`_sync()` は秒で判定し、手前のズレは吸収する |
| JSON 書き込みで PermissionError | `serve.py` が `media/` を監視して再ビルド中に掴んでいる。撮影時はサーバーを止めるか、リトライに任せる |

## 比較コンテンツの作り方

**選定基準**（上から順に効く）

1. **視覚差分が出るか。** 見た目が変わらないパラメータはこの媒体では価値がない
2. **公式ドキュメントで分からないか。** SideFX は「何をするか」は書くが「どのくらい変えるとどうなるか」は書かない。その差分が価値の本体
3. 実務で必ず触るか
4. 単独で効くか（他パラメータ依存が強いものは基準値を記事に明記する）
5. sim コストが許容範囲か

**値の刻み方**

- 等間隔にしない。物理系は効きが対数的で、線形だと「最初の1段階で全部変わり、あとは同じ」になる
- デフォルト値を必ず段階に含める（`--default-index` で指定。読者の基準点になる）
- 片端に破綻する値を入れる。破綻を見せることが理解につながる
- 段階数は5が基本

**シーン設計**

要素を増やすほど破綻の原因が増え、「パラメータの差」より「たまたま壊れたか」が絵を支配する。
衝突オブジェクト・床・影は必要になるまで置かない。

球の上に布をドレープさせる構成は**曲面で必然的に滑る**ため、比較として成立しなかった。
一辺を固定して吊るす方が安定する。

**シーンを作ったら、sim を回す前に `preview.py` で静止画を出してユーザーに見せて確認を取る。**
全撮影を回してから問題に気づくのは時間の無駄。

## 制約

- GitHub Pages はサイト 1GB / 帯域 100GB 月。**課金しても増やせない**（Pages 向け容量プランは存在せず、
  Git LFS も Pages が解決しないので使えない）。増やす道は Cloudflare R2 への退避のみ。
  そのため JSON の `src` はファイル名だけを持ち、`config.py` の `MEDIA_BASE` 1か所で切り替えられる。
- 公開は `/houdini-lab/` サブパス配下。**CSS/JS/動画のリンクを絶対パス `/...` で書かない。**
  `config.py` の `url()` / `media_url()` を通す。`serve.py` も同じサブパスで配信して条件を揃えている。
- 解像度は 960x540 固定。可変にすると記事に並べたとき揃わない。

## Git とアカウントの扱い

リポジトリが **Public** なので、コミットに書かれた author / committer のメールは
誰でも読める。過去に個人の Gmail アドレスが全コミットに入っていたため、
履歴を書き換えてリポジトリを作り直した（旧アカウント名 `jun-0927` も同時に除去）。

- **author には必ず noreply を使う。** `193295357+hamabe2@users.noreply.github.com`。
  global / local の両方に設定済み。他マシンで作業するときは最初に確認する。
- **GitHub の "Block command line pushes that expose my email" は当てにならない。**
  この機能は**アカウントに登録済みのアドレスしか対象にしない**。未登録のアドレス
  （個人の Gmail など）は素通しする。使い捨てのプライベートリポジトリで実際に
  検証し、実アドレスの push が通ることを確認した。**仕様であって故障ではない。**
- したがって防御は **ローカルの `git config user.email` 一箇所だけ**。
  コミットのメールは GitHub のアカウント設定とは無関係に、
  ローカルの設定がそのまま文字列として焼き込まれる。
- 一度 push したコミットは、force push で参照を消しても **SHA を直接指定すれば
  しばらく読める**。完全に消すにはリポジトリごと削除して作り直す
  （今回はそうした。star / fork が無いうちなら損失はない）。
