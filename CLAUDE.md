# houdini-lab

Houdini のパラメータを段階的に振り、スライダーで切り替えて比較できる形で公開するナレッジサイト。

- 公開URL: https://hamabe2.github.io/houdini-lab/
- リポジトリ: https://github.com/hamabe2/houdini-lab （**Public**）

## 応答スタイル

日本語で応答する。コード・パス・パラメータ名は英語のまま。

## このプロジェクトの仕組み

```
Houdini                   ffmpeg              静的サイト
  .hip を開いて       →   PNG 連番を     →    ブラウザで
  パラメータを振り        背景に合成し        currentTime を
  PNG 連番を書き出す      1本の mp4 に連結    シークして切替
```

撮影は2経路ある（`sweep.py` = hython + OpenGL ROP / `flipbook.py` = GUI の
houdini.exe + ビューポート）。**PNG は RGBA なので、mp4 にする前に必ず
不透明な背景へ合成する**（後述の「絵の作り方」。飛ばすと絵が壊れる）。

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

# ビューポートそのままを撮る（GUI の houdini.exe が開く。触らずに待つ）
.venv\Scripts\python.exe tools\flipbook.py --hip scenes\vellum_cloth.hip `
  --node /obj/SUBJECT/CONSTRAINTS --parm bendstiffness `
  --values 0,0.1,1,5,10 --frames 1-48 --out vellum-cloth-bend `
  --label "Bend Stiffness" --default-index 2 --draft

# sim 前に候補を篩にかける（別の効果を持つ複数パラメータを1枚ずつ）
.venv\Scripts\python.exe tools\setup_sheet.py --hip scenes\vellum_cloth.hip `
  --frame 24 --camera /obj/CAM_angle `
  --probe "/obj/SUBJECT/CONSTRAINTS:bendstiffness=0,1,10" `
  --probe "/obj/SUBJECT/SOLVER:substeps=1,3,10"
#   確認 : http://127.0.0.1:8765/houdini-lab/setup/

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

**このサイトの主役は比較動画。説明は補足の数行に留める。**散文で解説を書かない。
箇条書きと表で、動画を見るときの着眼点と固定値だけを示す。
一般的な Houdini の落とし穴は記事ではなくこの CLAUDE.md に書く。
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
- **撮影経路は2つある。入出力は同じで、絵の作り方だけが違う。**

  | | `sweep.py` | `flipbook.py` |
  |---|---|---|
  | 撮り方 | OpenGL ROP | ビューポートそのもの |
  | 実行 | hython（ヘッドレス） | houdini.exe（GUI ウィンドウが開く） |
  | 背景・床 | **無し**（要るなら ROP 側で用意する） | ビューポートの背景と参照グリッド |
  | ワイヤー | `smoothwire` で出る | `--shading`（既定 `SmoothWire`） |

  hython に flipbook は無い（`hou.SceneViewer.flipbook()` はビューアを要求する）。
  そこで `flipbook.py` は `HOUDINI_PATH=<hooks>;&` を差して houdini.exe を GUI 起動し、
  `tools/flipbook_hooks/scripts/456.py` フックからセッションの中に入り込む。

  **ウィンドウは親から `ShowWindow(SW_MINIMIZE)` を投げて引っ込める**
  （`--show-window` で表示のまま）。詳細と副作用は下の「GUI ウィンドウの扱い」。

  この経路特有の注意:
  - **houdini.exe の stdout は親に届かない**（GUI サブシステムのアプリ）。進捗は
    `tools/_cache/shots/<out>/flipbook.log` に書き、`flipbook.py` がそれを読んで表示する。
  - **終了コードは当てにならない。** 成否は `result.json` の有無と中身で判断する。
    GUI がダイアログを出して固まると外からは「終わらない」としか見えないので、
    `--timeout`（既定60分）で必ず打ち切る。
  - **`456.py` は hip を読むたびに走る**（起動直後の空シーンでも）。再入防止は
    `_hou_flipbook.STARTED`。456.py 自体は毎回 exec され直すのでフラグを置けない。
  - **flipbook の PNG は RGBA で、背景は alpha=0。**（最重要。下の「絵の作り方」参照）
  - **表示設定は全部明示的に固定する。** 何も指定しないと**その機械の個人設定が
    そのまま出力に出る**。同じコマンドを別のマシンで叩いて絵が変わっては
    比較素材にならない。実測した既定値と、固定している値:

    | | 環境の既定（実測） | 固定値 |
    |---|---|---|
    | `sceneAntialias` | 4 | `--aa` 8 |
    | `flipbookAntialias` | `UseViewportSetting` | `HighQuality` |
    | シェーディング | 環境依存 | `--shading` `SmoothWire` |
    | `colorScheme` | `Light` | `--scheme` `Light` |
    | `resolution` | 1280x720 | 960x540（`config.py`） |

  - **`--draft` の絵で品質を判断しない。** draft は 480x270 なので床グリッドの
    線が潰れてモアレが出る。本番（960x540）とは別物に見える。
  - パラメータ解決と sim キャッシュ破棄は `_hou_common.py` に共通化してある。
    **片方の経路にしか入っていないと、そちらだけで「全部同じ映像」が再発する。**
- シミュレーションキャッシュは **`D:\houdini-cache\houdini-lab`**（リポジトリ外）。
  Public リポジトリに巨大ファイルが入る事故を構造的に防ぐため。$HLCACHE で参照できる。

## GUI ウィンドウの扱い（flipbook 経路）

**`STARTUPINFO.wShowWindow` では最小化できない。** あれは親からのヒントに
過ぎず、Houdini は自前で `ShowWindow` を呼ぶので無視される。実測で
`IsIconic=False` のまま前面に出ることを確認した。**親から明示的に
`ShowWindow(SW_MINIMIZE)` を投げるしかない**（`flipbook.py` の
`minimize_windows()`）。ウィンドウは起動から数秒遅れて出るので待ちループから
毎周回試す。Houdini は自分を別プロセスとして起動し直すため、Popen の PID では
なく `houdini.exe` の名前で拾う。

- **起動から1秒弱はウィンドウが出る。**ウィンドウ生成前には最小化できない
- **最小化すると床の参照グリッドが粗くなる。** Houdini の参照グリッドは
  実際のビューポートのピクセルサイズに応じて分割数を変えるため。実測:

  | 領域 | 最小化あり / なしの差 |
  |---|---|
  | 背景 | inf dB（完全一致） |
  | 布 | 36.6 dB（ほぼ同じ） |
  | 床グリッド | **29.4 dB（明確に違う）** |

**検証時の注意**: RGBA の PNG を PSNR で比べると、**透明部分の RGB も比較に
入って数字が壊れる**（同じ絵でも 15dB などになる）。必ず `VIDEO_BG` に合成して
から比べること。

### 未決: 参照グリッドをどうするか（次のセッションで決める）

現状は「出力がウィンドウの状態に依存する」状態で、これまで潰してきた環境依存が
1つ残っている。**既に公開済みの `vellum-cloth-bend.mp4` はウィンドウ表示のまま
撮っており、以後の撮影と床グリッドの密度が食い違う。**選択肢:

1. 最小化のまま進める（現状）。密度がウィンドウ状態に依存する
2. `--show-window` を既定に戻す。画面に出るが挙動は安定
3. **参照グリッドを切り、床を `GROUND` ジオメトリで置き直す**（推奨）。
   グリッドがシーンの一部になるのでウィンドウ状態に一切依存しなくなる。
   かつて `GROUND` を消したのはアルファ不具合を隠していたからで、
   床そのものを否定したわけではない

## 絵の作り方（flipbook の RGBA）

**Houdini の flipbook は PNG を RGBA で書き出し、ビューポートの背景を alpha=0、
床のグリッドを半透明で置く。** 実測（480x270 の1フレーム、合成前の生 PNG）:

```
背景      RGBA = (0,   0,   0,   0)     ← alpha=0 が全画素の約 40%
グリッド  RGBA = (255, 255, 255, 40)    ← 純白を alpha 16% で乗せる想定
布        RGBA = (202, 155, 134, 255)   ← ジオメトリだけ不透明
```

**必ず不透明な背景に合成してから yuv420p に落とすこと。** 合成せずに変換すると
ffmpeg は alpha を捨てて RGB をそのまま使い、こうなる:

- 背景 → alpha 0 なのに RGB 0 が採用されて**純黒**
- グリッド → alpha 40 なのに RGB 255 が採用されて**純白**
- ジオメトリのエッジ → 中間 alpha なのに RGB 全開で、**アンチエイリアスが消える**

`encode.py` が `color` + `overlay` で `config.VIDEO_BG`（`0x3c4147`）に合成する。
Houdini 側はストレート（非プリマルチプライ）alpha なので overlay の既定でよい。

**この不具合は MPlay では絶対に見えない。** MPlay はアルファを正しく合成して
表示するので、GUI で FlipBook ボタンを押しても再現しない。壊れるのは
PNG を経由して mp4 に変換するこのパイプラインだけ。

### 背景色はカラースキームでは決まらない

ビューポートの背景は alpha=0 で書き出されるため、**Houdini 側のカラースキームは
出力の背景に一切届かない。** 背景色は合成先の `config.VIDEO_BG` だけで決まり、
ビューポートで見えている背景（グラデーション）は再現できない。
カラースキームが変えるのは**床グリッドの色と濃さ**:

```
Dark  -> グリッド RGB(197,238,255) alpha 25   （青みがかって淡い）
Light -> グリッド RGB(255,255,255) alpha 40   （純白で濃い）
```

### かつて置いていた BACKDROP / GROUND を復活させないこと

`template.hip` には背景の板（`BACKDROP`）と床のワイヤーグリッド（`GROUND`）が
あったが削除した。あれは OpenGL ROP のための代用品だったうえ、
**画面全体を alpha=255 で覆うことで上記の合成漏れを隠していた。**
合成を直した今は不要で、置くとビューポート本来のグリッドと二重になる。

デバッグ時の注意: **`BACKDROP` を表示すると alpha の問題が見えなくなる。**
「隠すと壊れて、出すと直る」ように見えるが、原因はアルファ合成であって
このジオメトリではない。

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
| 背景が真っ黒・グリッドが真っ白・エッジがガビガビ | **PNG が RGBA なのに合成せず yuv420p に落としている。** Houdini は背景を alpha=0、床グリッドを「白+alpha 16%」で書き出す。ffmpeg は alpha を捨てて RGB をそのまま使うのでこうなる。`encode.py` が `color` + `overlay` で `config.VIDEO_BG` に合成して防いでいる |
| flipbook にライトやカメラの線が写り込む | ビューポートはライト/カメラを**ギズモとして線で描く**。**`setDisplayFlag(False)` で消す。ギズモだけ消えて照明は残る**（実測: 布の画素 (204,158,140)→(203,156,135)）。`enableGuide` では消えず、`visibleObjects` から外すと光ごと消えてヘッドライトに落ちる |
| flipbook が終わらない | GUI がダイアログを出して止まっている。`tools/_cache/shots/<out>/flipbook.log` を見る。`--timeout` で打ち切られる |
| JSON 書き込みで PermissionError | `serve.py` が `media/` を監視して再ビルド中に掴んでいる。撮影時はサーバーを止めるか、リトライに任せる |
| 動画の最初のフレームで止まって再生されない（p=0 だけ動く） | **ローカルの `serve.py` が HTTP Range に応えていない。** `SimpleHTTPRequestHandler` は Range を無視して 200 で全体を返し、ブラウザはシーク不可と判断する。**GitHub Pages は Range に対応しているのでローカルでしか再現しない。**`serve.py` の `send_partial()` が担当 |

## 比較コンテンツの作り方

**進め方**: **sim を全部回す前に `setup_sheet.py` で候補を篩にかけ、ユーザーに
見せて判断を仰ぐ。**本撮り（5段階 x 48フレーム）を回し切ってから「差が出ない」
「画角が悪い」と分かるのは時間の無駄。**一度に大量の画像を作らない**
（3パラメータ x 3値くらいずつ）。

```powershell
.venv\Scripts\python.exe tools\setup_sheet.py --hip scenes\vellum_cloth.hip `
  --frame 24 --camera /obj/CAM_angle `
  --probe "/obj/SUBJECT/CONSTRAINTS:bendstiffness=0,1,10" `
  --probe "/obj/SUBJECT/SOLVER:substeps=1,3,10"
#   http://127.0.0.1:8765/houdini-lab/setup/
```

ここでいう「複数パラメータ」は**別の効果を持つパラメータ**（bendstiffness と
substeps など）。同じパラメータの段階を並べるのは `flipbook.py` の仕事。
パラメータごとに元の値へ戻すので、既定値のセルは全グループで同一画像になる
（＝混ざっていないことの検算になる）。

**カメラは題材の動く向きで選ぶ。** 布は面に垂直な Z 方向へ振れるので、
正対の `CAM_main` だと揺れが奥行き方向の動きになり、折れているのか手前に
来ているのかが読み取れない。**斜め 40 度の `CAM_angle` を使う。**
カメラの向きは `r` ではなく `lookatpath`（`/obj/AIM`）で決めている。
斜めだと必要な回転が回転順の解釈に依存し、手計算した角度では対象が
画面の端に寄ってしまうため。

**測って落とした例**: `stretchstiffness` を 0.001〜1 で振っても既定との差が
58〜69dB しかなく、視覚差分が出ないので不採用にした。隣の
`stretchstiffnessexp = 10` で**実効値が「値 x 10^10」**になっているのが原因。
`niter`（5〜100 で 24〜32dB）と `veldamping`（0〜2 で 28〜31dB）は採用見込み。

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
