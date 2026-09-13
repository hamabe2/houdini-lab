# houdini-lab

Houdini のパラメータを段階的に振り、スライダーで切り替えて比較できる形で公開するナレッジサイト。

- 公開URL: https://hamabe2.github.io/houdini-lab/
- リポジトリ: https://github.com/hamabe2/houdini-lab （**Public**）

## 応答スタイル

日本語で応答する。コード・パス・パラメータ名は英語のまま。

## 現在地（2026-09-13 時点）

**最終目標はループエンジニアリングによる自動生成。**候補出しから公開まで人が
介在せずに回ることを目指す。現状は**1周を手で通し終えた**ところ。

- `scenes/vellum_cloth.hip` は三角メッシュ（remesh 0.12 / 638三角）+
  Vellum Configure Cloth と同じ拘束設定 + ビューポート work light
- `vellum-cloth-bend.mp4` は**新シーンで撮り直し済み**（`CAM_angle` /
  実効 0, 1e-4, 1e-3, 1e-2, 10 / 隣どうし 35〜39dB）。記事も実測に書き直した。
  カラースキーム `Dark` + `overlay=format=rgb` で撮り直したのが最新
  （エッジの白い縁と灰色潰れの対処。「カラースキームは背景の色ではなくエッジに効く」）
- `screening.json`: 281 件 pending / `bendstiffness` は published /
  `maxviscosityiterations` は「差なし」

**工程の自動化の度合い**

| 工程 | |
|---|---|
| 候補を積む・出す（`ledger.py`） | 自動 |
| 振る値の刻みを決める（`propose_values.py`） | 自動 |
| 篩（`setup_sheet.py`）・判定（`screen.py`） | 自動 |
| 判定結果の承認 | **人間・1件ずつ**（未着手） |
| 本撮り（`flipbook.py`）| 手動起動 |
| JSON / 記事 / push | **手作業** |

**次にやること**: 判定結果をまとめて承認する仕組み（1件ずつ聞かずに済ませる）と、
`next → propose → sheet → screen` を N件まとめて回すドライバ。

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

# 複数本をまとめて撮る（Houdini の起動と hip 読み込みが1回で済む）
.venv\Scripts\python.exe tools\flipbook.py --hip scenes\vellum_cloth.hip --frames 1-48 `
  --sweep "/obj/SUBJECT/CONSTRAINTS:niter=5,10,25,50,100;out=vellum-cloth-niter;label=Iterations;default=1" `
  --sweep "/obj/SUBJECT/CONSTRAINTS:veldamping=0,0.1,0.5,1,2;out=vellum-cloth-damp;label=Velocity Damping;default=0"

# 振る候補を探す（内部名とUIラベルは一致しない。まずここを見る）
.venv\Scripts\python.exe tools\list_parms.py --hip scenes\vellum_cloth.hip --tree /obj
.venv\Scripts\python.exe tools\list_parms.py --hip scenes\vellum_cloth.hip `
  --node /obj/SUBJECT/SOLVER --filter "iter|damp" --doc

# 振る値の刻みを決める（絵が飽和する端を先に探す。端が無ければ自分で梯子を伸ばす）
.venv\Scripts\python.exe tools\propose_values.py --hip scenes\vellum_cloth.hip `
  --node /obj/SUBJECT/CONSTRAINTS --parm bendstiffness --camera /obj/CAM_angle

# 検証の台帳（screening.json）。何を調べ、何を落とし、なぜかを残す
.venv\Scripts\python.exe tools\ledger.py add --hip scenes\vellum_cloth.hip --node /obj/SUBJECT/SOLVER
.venv\Scripts\python.exe tools\ledger.py next --count 3   # 次に調べる候補
.venv\Scripts\python.exe tools\ledger.py list --status screened

# シートの結果を数値で判定する（採用可否を主観から外す）
.venv\Scripts\python.exe tools\screen.py

# sim 前に候補を篩にかける（別の効果を持つ複数パラメータを1枚ずつ）
.venv\Scripts\python.exe tools\setup_sheet.py --hip scenes\vellum_cloth.hip `
  --frame 24 --camera /obj/CAM_angle `
  --probe "/obj/SUBJECT/CONSTRAINTS:bendstiffness=0,1,10" `
  --probe "/obj/SUBJECT/SOLVER:substeps=1,3,10"
#   確認 : http://127.0.0.1:8765/houdini-lab/setup/

# 1フレームだけ確認（カメラやライトの調整用。sweep を回すより圧倒的に速い）
.venv\Scripts\python.exe tools\preview.py --hip scenes\vellum_cloth.hip `
  --camera /obj/CAM_angle --frame 1 --frame 24 --frame 48
#   照明の候補を見比べる: --look headlight --look threepoint --look dome --look sky

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

## Houdini の事実はインストール先から引く

**推測しない。**`$HFS` = `C:\Program Files\Side Effects Software\Houdini 21.0.729`

| 知りたいこと | 場所 |
|---|---|
| tab メニューの "Vellum Configure Cloth" などが実際に設定する値 | `$HFS\houdini\toolbar\*.shelf`（XML）。`<tool name="geometry_vellumconfigurecloth">` の `kwargs['parms']` に全項目 |
| ノードのパラメータの公式説明 | `$HFS\houdini\help\nodes.zip` の `sop/<型名>.txt`。`#id: <内部名>` の印。`list_parms.py --doc` が解析する |
| 環境変数 | `$HFS\houdini\help\ref.zip` の `env.txt`（`::HOUDINI_XXX`） |
| シーンにあるノードの現在値 | `list_parms.py --node` |
| まだシーンに無いノード型のパラメータ | `list_parms.py --type remesh` |

- **パラメータ説明は `parmTemplate().help()` には入っていない**（実測で vellumsolver は全部空）。nodes.zip を読むしかない
- **tab メニューの項目は正式なノード型ではない。** 値が入った状態の既存ノードを作るツール定義で、`vellumconfigurecloth` というノード型は存在しない。**ユーザーはノード名の感覚で呼ぶ**ので、そう言われたら .shelf を探す。シェルフから呼ぶものはノードセットで別物
- **Houdini の挙動の判断はユーザーに訊くのが最速。**hython で実測するより速くて確実なことが多い

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
  - **スプラッシュと「Start Here」ページは環境変数で止めている**
    （`HOUDINI_NO_SPLASH` / `HOUDINI_NO_START_PAGE_SPLASH`、`flipbook.py` が設定）。
    最小化したウィンドウの前に出られても操作できないため。
  - **houdini.exe の stdout は親に届かない**（GUI サブシステムのアプリ）。進捗は
    `tools/_cache/shots/<out>/flipbook.log` に書き、`flipbook.py` がそれを読んで表示する。
    `--sweep` を複数渡したときは 1セッション = 複数本なので `shots/_batch/` に置く。
  - **`--sweep` を複数渡すと1回の起動で複数本を撮る。** 削れるのは起動と hip 読み込みの
    時間だけで、sim は値ごとに作り直す必要が消えないので変わらない。
    撮る前に全スイープのパラメータを解決し（打ち間違いで数十分を捨てないため）、
    1本が失敗しても残りは続行して、撮れた分だけ mp4 にする（終了コードは 1）。
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
    | `colorScheme` | `Light` | `--scheme` `Dark`（`config.VIEWPORT_SCHEME`。理由は後述） |
    | `lighting` | `HighQuality` | `--lighting` `Headlight` |
    | work light | 環境依存 | `--work-light` `Headlight` |
    | `resolution` | 1280x720 | 960x540（`config.py`） |

  - **照明はシーンに置かず、ビューポートの work light で決める。**
    `template.hip` の `LIGHT_key` は削除した（平板な絵にしかならなかった）。
    種類は `hou.viewportWorkLight` の Headlight / ThreePoint / Domelight / PhysicalSky。
    `preview.py --look` で1回の起動で見比べられる。
  - **work light を使うには `lighting` を `Headlight` にすること。**
    `setWorkLightType()` の docstring に明記されている
    （"Does not change the lighting mode to Headlight; this must be done separately."）。
    `Normal` / `HighQuality` のままだと work light は無視される。実測で
    4モードの出力がバイト単位で一致した（`Normal` を入れても読み返すと
    `HighQuality` に戻る）。同じ理由で `setHeadlightDirection()` /
    `setHeadlightSpecular()` も届かない。
  - **`setAmbientOcclusion(True)` は布を真っ黒にする**（0灯だと OpenGL が黒に落ちる）。使わない。
  - **副作用: `sweep.py`（OpenGL ROP 経路）は真っ黒になる。** ライト0灯だから。
    撮影は flipbook 経路に一本化している。

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

### 決定: 最小化のまま進める

ユーザー判断で「グリッドはこのままでいい」。**床グリッドの密度がウィンドウ状態に
依存する点は許容する。**`GROUND` ジオメトリで置き直す案は採らない
（＝上の「BACKDROP / GROUND を復活させないこと」は引き続き有効）。

副作用として、**既に公開済みの `vellum-cloth-bend.mp4` はウィンドウ表示のまま
撮っており、以後の撮影とは床グリッドの密度が食い違う。**記事内で並べて比較する
ものではないので撮り直しはしていない。気になったら撮り直す。

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

**`overlay` の `format` を既定（`yuv420`）のままにしないこと。** overlay は混ぜる
前に両入力をその形式へ変換するので、既定だと**前景のクロマを半分に落としてから
合成する**ことになり、1〜2px のエッジで色が消えて無彩色に潰れる。実測（布の
シルエットを暗い背景に合成）:

```
yuv420  (125,123,124)   <- 灰色に潰れている
rgb     (135,121,115)   <- 手計算したストレート alpha の over と完全に一致
```

`OVERLAY_FORMAT = "rgb"` で固定してある。yuv420p への変換は最後の
`-pix_fmt` 1回だけにする。

**この不具合は MPlay では絶対に見えない。** MPlay はアルファを正しく合成して
表示するので、GUI で FlipBook ボタンを押しても再現しない。壊れるのは
PNG を経由して mp4 に変換するこのパイプラインだけ。

### カラースキームは背景の色ではなくエッジに効く

背景の「面」の色は合成先の `config.VIDEO_BG` だけで決まる。ビューポートで
見えているグラデーションは再現できない。**だがカラースキームは無関係ではない。
エッジに効く。**

**flipbook の PNG は「RGB = ビューポートを平坦化した絵（背景を焼き込み済み）」
＋「alpha = それとは別立てのマット」で、両者が一致していない。**
布のシルエット（frame 24 / y=200）の実測:

```
Light  x=337 RGB(255,255,255) a= 7   x=338 RGB(255,255,255) a= 27   x=339 RGB(157,137,128) a=198
Dark   x=337 RGB(  0,  0,  0) a= 4   x=338 RGB(101, 81, 75) a= 24   x=339 RGB(124, 95, 82) a=197
```

Light では x=338 で **alpha だけが 7→27 に上がり、RGB は純白のまま**。
マットが RGB の被覆より 1px 広いので、暗い背景に合成すると白が 10% 乗って
**明るい縁**になる（合成後 (81,85,90)、背景は (65,70,76)）。Dark なら焼き込まれる
背景が黒側なので縁が背景と地続きになる（合成後 (64,67,71)、背景 (59,64,70)）。

逆向きの証拠もある。**alpha=0 なのに RGB が明るい画素**が Light で 8,294 個あり、
173〜175 行に集中している（地平線のヘイズ帯）。ここは合成すると丸ごと消える。

- **プリマルチプライではない。**`RGB > alpha` の画素が 18万個あり、
  `(255,255,255, alpha=0)` すら実在する（プリマルチプライなら原理的にあり得ない）。
  自前で PNG を展開しても ffmpeg と同じ値。**デコードも合成の式も正しい**
- Houdini 側に逃げ道は無い。`hou.FlipbookSettings` にあるのは `antialias` /
  `backgroundImage` / `beautyPassOnly` / `cropOutMaskOverlay` だけで、
  アルファの出し方を変える設定は無い
- したがって **`VIEWPORT_SCHEME` は `VIDEO_BG` と対で決める。**
  `VIDEO_BG` を明るい色にするなら `Light` に戻すこと

副作用として床グリッドの色が変わる:

```
Dark  -> グリッド RGB(197,238,255) alpha 25   （青みがかって淡い）
Light -> グリッド RGB(255,255,255) alpha 40   （純白で濃い）
```

**未対応**: 右下の Houdini ウォーターマークの縁は、合成後も色が混ざって
不自然に見える。実害が小さいので放置している。

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
| パラメータを振っても効かない | **トグルがオフでグレーアウトしている。** Houdini はこの構造が多い。`_hou_common.py` の `check_disabled()` が撮影前に `isDisabled()` で見て止める。**名前から親トグルを推測しないこと**（`substeps` に対する `dosubstep` は取り違え。あれが制御するのは `substep` = Global Substeps で、vellum の `substeps` とは別々に作用する） |
| 初速を与えても布が動かない | 面内方向に振っても stretch 拘束で伸びず動かない。**面に垂直な方向**に振る |
| オブジェクトが真っ黒 | ライト0灯だと OpenGL は真っ黒に落とす（ヘッドライト自動化は効かない）。また薄い面を裏側から見ていないか確認 |
| パラメータを変えても全部同じ映像 | sim キャッシュが残っている。`_hou_sweep.py` の `clear_sim_caches()` が担当。File Cache SOP が読み込みモードでも起きる |
| スライダーを動かすと別の値が出る | セグメント境界にキーフレームが無い。`encode.py` が `ffprobe` で毎回検証している |
| ビューアが1フレームで振動する | シーク位置の丸めで手前に落ちたとき巻き戻すと無限ループになる。`_sync()` は秒で判定し、手前のズレは吸収する |
| 背景が真っ黒・グリッドが真っ白・エッジがガビガビ | **PNG が RGBA なのに合成せず yuv420p に落としている。** Houdini は背景を alpha=0、床グリッドを「白+alpha 16%」で書き出す。ffmpeg は alpha を捨てて RGB をそのまま使うのでこうなる。`encode.py` が `color` + `overlay` で `config.VIDEO_BG` に合成して防いでいる |
| flipbook にライトやカメラの線が写り込む | ビューポートはライト/カメラを**ギズモとして線で描く**。**`setDisplayFlag(False)` で消す。ギズモだけ消えて照明は残る**（実測: 布の画素 (204,158,140)→(203,156,135)）。`enableGuide` では消えず、`visibleObjects` から外すと光ごと消えてヘッドライトに落ちる |
| flipbook が終わらない | GUI がダイアログを出して止まっている。`tools/_cache/shots/<out>/flipbook.log` を見る。`--timeout` で打ち切られる |
| setup シートに前回のパラメータの画像が残る | `serve.py` がファイルを掴んでいて `write_sheet()` の `rmtree(ignore_errors=True)` が消しきれない。**`sheet.json` は今回の分しか載せないのでページには出ない**（実害なし）。消したいならサーバーを止める |
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

**「効かないパラメータ」は隣の指数を疑う。** `stretchstiffness` を 0.001〜1 で
振っても既定との差が 58〜69dB しかなく、一度は不採用にした。原因は隣の
`stretchstiffnessexp = 10` で、**実効値が「値 x 10^exp」**になっていたこと。
0.001〜1 はどれも 10^7〜10^10 に収まり、全部「伸びない布」だった。
Houdini はこの「値 + 指数」の並びが多いので、**振る対象は指数側**にする。

`list_parms.py` が `実効値 = 値 x 10^N` として一覧に出すので、選ぶ時点で気づける。
vellumconstraints では `stretchstiffness` / `compressstiffness` / `tangentstiffness` /
`bendstiffness` の4つがこの並び。指数は `make_vellum_cloth_scene.py` が
明示的に固定している（bend は -4 = Configure Cloth と同じ）。

`vellum-cloth-bend.mp4` は新シーンで撮り直し済み（2026-09-13）。

以下は**旧シーン（四角メッシュ / 旧ライト / bend 指数 -1）での実測**なので、
新しいシーンでは測り直しが要る。傾向の参考としてのみ残す。
（frame 24 / `CAM_angle` / 合成後 PSNR、既定 `exp=10` との差）:

| `stretchstiffnessexp` | 既定との差 | | 隣どうしの差 |
|---|---|---|---|
| 1 | 19.7 dB | 1→2 | 26.1 dB |
| 2 | 20.0 dB | 2→3 | 19.9 dB |
| 3 | 28.8 dB | 3→4 | 28.5 dB |
| 4 | 34.1 dB | 4→5 | 34.3 dB |
| 5 | 46.8 dB | 5→6 | 47.5 dB |
| 6 | 59.8 dB | | |

**有効域は 1〜5、6 以上は頭打ち。**指数は既に対数の軸なので刻みは等間隔でよいが、
`2,4,6,8,10` にすると 6/8/10 が同じ絵になって枠を3つ捨てる。
**提案した段階は `1,2,3,4,10`**（既定 10 を含め、有効域を密に取る）。
ユーザーへのシート提示まで済んでおり、**本撮りは未実施。**

`niter`（5〜100 で 24〜32dB）と `veldamping`（0〜2 で 28〜31dB）も採用見込みだが
未撮影。

### 段階を決める前に、絵が飽和する端を探す

**`screen.py` を通しただけでは「有効域のごく一部を5段階に切った動画」が作れて
しまう。**判定は「既定値と差があるか」しか見ないので、差さえ出ていれば通る。

実際に踏んだ: `bendstiffness` を 0.1〜100（実効 1e-5〜1e-2）で篩にかけたら
隣どうし 34〜37dB で「採用・段階に無駄なし」と通った。本撮りしたら5段階が
見分けられなかった。**実測した有効域は 0〜100000（実効 0〜10）で、撮っていたのは
その 0.1% 以下だった。**

`propose_values.py` が既定値から桁を上下に振って端を探す
（frame 24 / `CAM_angle` / 合成後 PSNR、隣どうし）:

| 入力欄 | 0 | 1e-4 | 1e-3 | 1e-2 | 0.1 | 1 | 10 | 100 | 1000 | 1e4 | 1e5 | 1e6 | 1e7 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 隣との差 dB | 一致 | 59.3 | 48.1 | 40.9 | 35.4 | 36.1 | 36.2 | 37.0 | 37.7 | 46.5 | 60.1 | 68.9 | |

**下は 1e-4 以下が `0` と完全一致、上は 1e5 以上が頭打ち。**UI スライダーの
範囲（0〜10）はまるで当てにならない。

この経路で決まっていること:

- **端が見つからない側は梯子を自分で伸ばす**（`--max-rounds` 回まで）。
  人が `--decades` を指定し直すのではループにならない
- **飽和帯の代表は1段だけ採る。**どちらの端を採るかは梯子が止まった理由で決める。
  パラメータの下限や `--cap` で止まったならその値そのもの（`bendstiffness = 0`
  ＝曲げ拘束なしは読者に意味がある端）。撮り切っただけなら**最初に飽和した段**
  （外端を採ると、実効 10 で頭打ちなのに実効 1000 を並べる提案になる）
- **刻みは値の対数ではなく「見た目の差」で等間隔にする。**隣どうしの PSNR を
  `10^(-PSNR/20)` で距離に直して積み上げ、その軸で等間隔に選ぶ。値の対数で
  切ると、視覚差が詰まっている帯に枠を使う（実測: `0` と `0.01` が 48.8dB
  ＝ほとんど同じ絵なのに別々の段階として提案された）
- **段階は撮った梯子の段から選ぶ。**新しい値を作らない。梯子は既定値の倍数で
  できているので既定値が必ず候補にあり、どの段も実測済みの値になる

**採用可否は数値で決める。目視で決めない。**`screen.py` が PSNR とアトリビュートで判定する。

```
ledger.py add       ノードの全パラメータを候補として積む（既に判定済みのものは触らない）
ledger.py next      次に調べる候補を出す
propose_values.py   端を探して振る値の刻みを決める → 台帳に proposal を残す
setup_sheet.py      候補を1フレームずつ撮る
screen.py           PSNR で判定 → screening.json に書き戻す
flipbook.py         採用されたものだけ本撮り
```

- **差なし** = 既定値との差が 50dB 以上（＝ほぼ同じ絵）。振る価値がない
- **採用（段階に無駄あり）** = 隣どうしが 50dB 以上。その段階は枠を捨てている
- **破綻** = NaN/inf、または速度 p95 が中央値の20倍超

**PSNR は合成後の画像で測ること。**RGBA のまま比べると透明部分の RGB まで
計算に入り、同じ絵でも 15dB のような数字になる。`setup_sheet.py` がセルを
`VIDEO_BG` に合成してから置いている。

限界: **1フレームの比較なので、途中で破綻して最後に戻る挙動は捕まらない。**
速度の判定もゆっくり進む破綻には効かない。閾値 50dB は実測（有効域 19〜47dB /
頭打ち 59dB）から引いた線で、題材が変われば `--same-db` で見直す。

**`isDisabled()` は「この構成に無関係なパラメータ」を落とせない**（実測で
283件中0件しか該当せず、`adhesion` や `attachframe` も False だった）。
`ledger.py next` は UI のタブ名で順位を下げているだけなので、無関係な候補は
混ざる。**それでよい**設計にしてある — 外した候補は1フレーム撮るだけで
「差なし」と記録され、二度と候補に戻らない。

**候補はノードのパラメータ一覧から選ぶ。**記憶や勘で挙げない。
`list_parms.py` が 内部名 / UI ラベル / 現在値 / 既定 / 範囲 / 公式の説明 を並べる。

- **内部名と UI ラベルは一致しない。** `niter` は UI では "Constraint Iterations"。
  UI を眺めても内部名は分からず、内部名だけ見ても何のことか分からない。
  **記事の `label` には UI ラベルを書く**（読者が Houdini で探せる名前）
- **組み込みノードの説明は `parmTemplate().help()` に入っていない**（実測で全部空）。
  本文は `houdini/help/nodes.zip` の `sop/vellumsolver.txt` などにあり、
  `#id: <内部名>` の印で引ける。`list_parms.py --doc` がそこを読む
- 範囲の `(目安)` は強制でない範囲（UI スライダーの端）。**外の値も入れられる**ので、
  「片端に破綻する値を入れる」ときはここを超えてよい

**サイトに表記するのは実効値。**Houdini の入力欄の値ではない。
stiffness 系は「数値の入力欄」と「× 10^N のメニュー（`<parm>exp`）」が並んでいて、
効いているのは積のほう。入力欄が 10 で `exp` が 1 なら **表記は 100**。
`_hou_common.py` の `effective_values()` が計算し、JSON の `values` に入る
（入力欄の値は `raw_values`、倍率は `multiplier` に残す）。

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
