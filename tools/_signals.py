"""Ctrl+C と Ctrl+Break を同じ「片付けてから終わる」経路に乗せる。

Windows のコンソールは2種類のシグナルを投げる:

  Ctrl+C      SIGINT    Python が KeyboardInterrupt にする
  Ctrl+Break  SIGBREAK  **既定ハンドラがプロセスを即座に殺す**

後者は `try/finally` も `except KeyboardInterrupt` も通らない（実測で終了
コード 0xC000013A = STATUS_CONTROL_C_EXIT）。長時間走るものを人が止める手段が
2つあるなら、**どちらで止めても同じ後始末**にしないと、片方だけ Houdini が
残ってライセンスを掴んだままになる。

  from _signals import handle_break
  handle_break()

呼ぶのは実行の入口（`main()` の頭）だけでよい。子プロセスは同じコンソールに
いるので同じシグナルを受け取るが、**ハンドラは継承されない**ので、止まって
ほしいプロセスそれぞれが呼ぶ。
"""

from __future__ import annotations

import signal
import sys


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def handle_break() -> bool:
    """Ctrl+Break を KeyboardInterrupt にする。効いたかを返す。

    Windows 以外では何もしない（SIGBREAK が無い）。
    """
    if sys.platform != "win32" or not hasattr(signal, "SIGBREAK"):
        return False
    signal.signal(signal.SIGBREAK, _raise_interrupt)
    return True
