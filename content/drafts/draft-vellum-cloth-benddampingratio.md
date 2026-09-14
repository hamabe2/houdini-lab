---
title: Vellum Constraints / benddampingratio
node: vellumconstraints
context: SOP
houdini_version: "21.0.729"
tags: [vellum, cloth]
updated: 2026-09-14
description: Damping Ratio を段階的に振った比較動画（下書き）。
# **公開されない。** config.SHOW_DRAFTS はローカルの serve.py しか立てない。
draft: true
scene_node: /obj/SUBJECT/CONSTRAINTS
merge_into: vellum-constraints.md
---
## benddampingratio

曲げ拘束の減衰。拘束を解くときにエネルギーを抜いて、硬い拘束が起こす振動やジッタを抑える。強すぎると拘束そのものが満たされなくなるので 1 未満で使う。

UI では **Bend > Damping Ratio**。

:::compare vellum-cloth-benddampingratio

<!-- ここから下は機械が書いた。動画を見て直すこと。 -->

**まだ人が見ていない。** 以下は 1 フレームの数値比較だけで、動画で何が起きているかは誰も確認していない。

| 実効値 | 既定との差 | 隣との差 |
|---|---|---|
| `0` | 46.39 dB | — |
| `0.01` **既定** | — | 46.39 dB |
| `0.1` | 42.04 dB | 42.04 dB |
| `1` | 37.48 dB | 39.1 dB |

- **測った有効域は `0` 〜 `1`。**この外はどの値を入れても同じ絵になる（1フレーム比較）
- Houdini の UI が示す範囲の目安は `0` 〜 `1`（強制ではない）

公式の説明:

> Stiff constraints tend to vibrate or jitter unacceptably.   Damping reduces this by bleeding energy when evaluating the constraint.  Too much damping can prevent the constraint from being satisfied, however.   Values less than 1 should be used.

### ここは人が書く

- [ ] **何を見るか。** どこに注目すると段階の違いが分かるか（`bendstiffness` なら「下端と左下の角」）
- [ ] **固定値の表。** この動画で固定した他のパラメータ
- [ ] **実務でどう効くか。** 既定値が有効域のどのあたりにあるか
- [ ] 書けたら `content/nodes/` の本体へ移して、front matter の `draft: true` を消す
