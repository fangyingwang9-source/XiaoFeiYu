# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件 + asInvoker 清单（只在写计划任务/休眠时按需提权）。"""
import os

import customtkinter
from PyInstaller.utils.hooks import collect_submodules

ctk_dir = os.path.dirname(customtkinter.__file__)
datas = [(os.path.join(ctk_dir, "assets"), "customtkinter/assets")]
if os.path.isdir("assets"):
    datas.append(("assets", "assets"))

# 页面插件是 importlib 动态导入的（app/plugins/__init__.py 的 PLUGIN_MODULES），
# PyInstaller 静态分析看不到，必须显式收集，否则冻结后左侧导航空白、右侧无内容。
hidden = (
    ["PIL._tkinter_finder"]
    + collect_submodules("customtkinter")
    + collect_submodules("app")
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "scipy", "PyQt5", "PySide6", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="XiaoFeiYuConsole",
    debug=False,
    strip=False,
    upx=False,
    # %TEMP% 受限（ACL/杀软）时 onefile 会报 Could not create temporary directory；
    # 指到 exe 所在目录（双击时 cwd=exe 目录）可绕开系统 TEMP 的权限问题。
    runtime_tmpdir=".",
    console=False,
    icon="assets/app.ico",
    uac_admin=False,  # asInvoker：普通启动，需要管理员的操作走 ShellExecuteW(runas)
)
