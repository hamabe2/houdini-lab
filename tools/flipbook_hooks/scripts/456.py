"""Houdini が hip を読むたびに実行するフック。

flipbook.py が HOUDINI_PATH にこのディレクトリを差し込んで houdini.exe を
起動する。Houdini は起動直後の空シーンでも 456.py を走らせるので、
「GUI セッションの中に入り込む」入口としてこれを使う。

環境変数 HOUDINI_LAB_FLIPBOOK_JOB が無ければ何もしない。普段の対話起動で
このディレクトリが HOUDINI_PATH に残っていても、勝手に動き出さないため。

ここは薄く保つ。456.py は毎回 exec され直すのでグローバルが残らず、
再入防止のフラグを置けない。実処理と状態は _hou_flipbook モジュールに置く。
"""

import os
import sys
import traceback

_job = os.environ.get("HOUDINI_LAB_FLIPBOOK_JOB", "")
_tools = os.environ.get("HOUDINI_LAB_TOOLS", "")

if _job and _tools:
    try:
        if _tools not in sys.path:
            sys.path.insert(0, _tools)
        import _hou_flipbook

        _hou_flipbook.kick(_job)
    except Exception:
        # ここで例外を漏らすとエラーダイアログが出て GUI が固まり、親からは
        # 「終わらない」としか見えない。理由をログに残して確実に落とす。
        try:
            with open(_job + ".hook.log", "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except OSError:
            pass
        import hou

        hou.exit(suppress_save_prompt=True)
