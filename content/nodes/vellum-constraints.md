---
title: Vellum Constraints
node: vellumconstraints
context: SOP
houdini_version: "21.0.729"
tags: [vellum, cloth]
updated: 2026-09-10
description: Vellum Constraints の各パラメータを、値を段階的に変えた比較動画で確認する。
---

検証シーン: 2.4 x 1.6 の布（30x40）を上端の一辺で固定して吊るし、面に垂直な初速を与えて振らせたもの。衝突なし。

## bendstiffness

曲げに対する硬さ。既定は `1`。

:::compare vellum-cloth-bend

- 小さいほど下端が細かく丸まる。`10` では布というより薄い板になる
- **振れの速さ**に注目。高いほど布全体が一体で動き、速く単純な動きになる
- 上げて不安定になったら、値ではなく `substeps` 不足を疑う
- 固定値: `stretchstiffness` 1 / `thickness` 0.01 / `substeps` 3

| 素材 | 目安 |
|---|---|
| シルク・薄手 | 0 〜 0.1 |
| 一般的な衣服 | 0.5 〜 2 |
| 革・厚手のコート | 3 〜 10 |
