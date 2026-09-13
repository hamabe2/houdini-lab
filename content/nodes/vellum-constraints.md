---
title: Vellum Constraints
node: vellumconstraints
context: SOP
houdini_version: "21.0.729"
tags: [vellum, cloth]
updated: 2026-09-13
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
