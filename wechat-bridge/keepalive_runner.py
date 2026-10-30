
"""保活：桥活着就什么都不做；死了就用 pythonw 无控制台直接拉起 wechat_bridge.py。

不走 cmd / run-bridge.bat：cmd.exe 是控制台程序，Windows Terminal 会为它弹一个窗口
（计划任务的 Hidden=True 挡不住），这是"五分钟一次黑窗闪一下"的真正来源。
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
PID = HERE / "bridge.pid"
LOG = HERE / "console.log"
CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008
NEW_GROUP = 0x00000200


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    p = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return str(pid) in p.stdout


def main() -> int:
    try:
        pid = int((PID.read_text(encoding="ascii").strip() or "0"))
    except Exception:
        pid = 0
    if alive(pid):
        return 0
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    env = dict(os.environ)
    env["STICKER_ENABLED"] = "1"
    with open(LOG, "ab") as f:
        f.write(f"\n==== keepalive_runner {time.strftime('%Y-%m-%d %H:%M:%S')} bridge dead, relaunching ====\n".encode("utf-8"))
        f.flush()
        subprocess.Popen([str(pyw), str(HERE / "wechat_bridge.py")], cwd=str(WS), env=env,
                         stdin=subprocess.DEVNULL, stdout=f, stderr=f,
                         creationflags=CREATE_NO_WINDOW | DETACHED | NEW_GROUP,
                         close_fds=True)
    time.sleep(2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
