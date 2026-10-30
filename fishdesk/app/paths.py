"""路径解析：兼容源码运行与 PyInstaller 单文件打包（sys._MEIPASS）。

规则：
* 打包后资源（图标/内置插件）在 sys._MEIPASS，exe 所在目录可写、放配置。
* 源码运行时两者都等于 fishdesk/ 根目录。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "XiaoFeiYu"
APP_TITLE = "小肥鱼控制台"
APP_VERSION = "0.1.0"

DEFAULT_WORKSPACE = Path(r"E:\useer\deepseek-harness\default-workspace")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """资源目录：打包后是解压出来的临时目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """程序目录：打包后是 exe 所在目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    """取打包内资源；找不到时退回程序目录。"""
    p = bundle_dir().joinpath(*parts)
    if p.exists():
        return p
    return app_dir().joinpath(*parts)


SETTINGS_HOME = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
SETTINGS_FILE = SETTINGS_HOME / "app_settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_HOME.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_workspace(p: Path | str | None) -> bool:
    if not p:
        return False
    try:
        return (Path(p) / "wechat-bridge" / "wechat_bridge.py").is_file()
    except Exception:
        return False


def resolve_workspace(settings: dict | None = None) -> Path:
    """按优先级找工作区：环境变量 > 用户设置 > exe 附近 > 当前目录 > 默认路径。"""
    settings = settings or {}
    candidates = [
        os.environ.get("DSH_WORKSPACE"),
        settings.get("workspace"),
        app_dir(),
        app_dir().parent,
        Path.cwd(),
        DEFAULT_WORKSPACE,
    ]
    for c in candidates:
        if is_workspace(c):
            return Path(c).resolve()
    return DEFAULT_WORKSPACE


def bridge_dir(ws: Path) -> Path:
    return ws / "wechat-bridge"


def config_path(ws: Path) -> Path:
    return bridge_dir(ws) / "config.json"


def persona_path(ws: Path) -> Path:
    return bridge_dir(ws) / "persona.md"


def stickers_dir(ws: Path) -> Path:
    return bridge_dir(ws) / "stickers"
