---
title: Vellum Constraints
node: vellumconstraints
# 検証シーンでこのノードにあたるパス。下書き生成（tools/draft.py）が
# 「この候補はどの記事に入るのか」をここで引く。
scene_node: /obj/SUBJECT/CONSTRAINTS
context: SOP
houdini_version: "21.0.729"
tags: [vellum, cloth]
updated: 2026-09-14
description: Vellum Constraints の各パラメータを、値を段階的に変えた比較動画で確認する。
---

検証シーン: 2.4 x 1.6 の布（remesh 0.12 / 三角 638）を上端の一辺で固定して吊るし、
面に垂直な初速 2.5 を与えて振らせたもの。衝突オブジェクトも床も無し。48 フレーム / 24fps。

## bendstiffness

曲げに対する硬さ。UI では **Bend > Stiffness**。

:::compare vellum-cloth-bend

- **表記は実効値。** Houdini の入力欄の値 x `bendstiffnessexp`（= 10^-4）。入力欄の `1` が実効 `0.0001`
- **下端と左下の角を見る。** 実効 `0` は下端が折れて角が巻き込む。上げるほど下端が一直線に近づき、実効 `10` では皺が消えて完全な板になる
- **振れの位相が変わる。** 硬いほど布全体が一体で動くので、同じ再生時刻でも傾きが違う
- **効くのは実効 `0`〜`10` の範囲だけ。** それ以上は何を入れても同じ絵になる（1フレーム比較で PSNR 60dB 以上＝ほぼ同一）。下も実効 `1e-7` 以下は `0` と区別がつかない
- **既定（実効 `0.0001`）は有効域のかなり柔らかい側にある。** ヘルプにある「高解像度の布ほど高い stiffness が要る」の実例で、このメッシュ（辺長 0.12）では既定のままだとほぼ「曲げ拘束なし」に近い

固定値:

| | |
|---|---|
| Stretch / Compression Stiffness | 実効 1e10 / 1e3 |
| Damping Ratio | stretch 0.001 / bend 0.01 |
| Substeps / Constraint Iterations | 3 / 100 |
| 質量 / 厚み | Calculate Varying (density 0.1) / Calculate Uniform (Edge Length Scale 0.25) |
| Gravity / Velocity Damping | -9.80665 / 0 |

## bendrestscale

曲げ拘束の休息角を何倍にするか。UI では **Bend > Rest Angle Scale**（範囲の目安 0〜2）。

:::compare vellum-cloth-restscale

- **UI の範囲では絵が変わらない。** 既定 `1` に対して `100` でも 57.8dB（＝ほぼ同一）。
  公式の説明どおり、休息角は「三角形どうしの元の二面角」。**この布は実測で
  二面角が最大 0.000001 度＝完全に平らなので、何倍しても 0 のまま**
  （remesh 後の 638 三角 / 内部エッジ 909 本を実測）
- **つまり平らな布から始める限り、このパラメータは触っても無駄。**
  効かせたいなら、最初から折り目やカーブの付いたメッシュを入力にする
- **絵が動き始めるのは `1000` から**（既定との差 43.0dB → `10000` で 35.9dB →
  `100000` で 28.6dB）。ただし **`100000` 倍しても休息角は最大 0.12 度**にしかならない。
  **0.1 度の違いが 48 フレームのあいだに育っていく**のがこの動画で見えているもので、
  休息角そのものが絵を作っているのではない
- **`100000` は全面が皺になり、下端が房のように割れる。** それでも sim は壊れていない
  （速度 p95 は全段 2.5 で揃っている）
- **`1e8` まで上げると破綻する**（休息角が最大 100 度に届き、速度 p95 が中央値の 39 倍）。
  動画には入れていない
- **UI の範囲外を並べている。** 0〜2 では差が出ないため。Houdini の範囲表示は
  「よく使う辺り」でしかなく、上限ではない

固定値:

| | |
|---|---|
| Bend Type | Angle（二面角を拘束する） |
| Bend Stiffness | 実効 0.0001 |
| Stretch / Compression Stiffness | 実効 1e10 / 1e3 |
| Damping Ratio | stretch 0.001 / bend 0.01 |
| Substeps / Constraint Iterations | 3 / 100 |
| 質量 / 厚み | Calculate Varying (density 0.1) / Calculate Uniform (Edge Length Scale 0.25) |
| Gravity / Velocity Damping | -9.80665 / 0 |
