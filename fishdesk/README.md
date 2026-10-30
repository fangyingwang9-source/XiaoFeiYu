# 小肥鱼控制台（XiaoFeiYuConsole）

把小肥鱼微信桥（`wechat-bridge/`）包成一个**独立 Windows 桌面程序**：不用再手改 `wechat_bridge.py` 里的常量、不用记 bat 怎么跑，双击 exe 就能看状态、改人设、调参数，保存即生效。

---

## 一、框架选型：为什么是 CustomTkinter

| 方案 | exe 体积 | 依赖/坑 | 结论 |
| --- | --- | --- | --- |
| **CustomTkinter**（选中） | ≈ 22–30 MB | 纯 Python，只叠在 Python 自带 tkinter 上；无编译期依赖 | ✅ 体积最小、打包最稳，深色圆角控件够用 |
| PySide6 / PyQt6 | ≈ 90–130 MB | Qt 运行时 + 插件目录，onefile 解包慢，杀软误报多 | ❌ 为了 6 个设置页背 100 MB 不值 |
| Web（pywebview / Electron） | 40 MB–200 MB | 要带 WebView2/Chromium，前后端两套工程 | ❌ 违反「轻」和「打包不崩」 |
| 原生 tkinter | ≈ 12 MB | 控件丑、圆角/深色要手画 | ❌ 与「界面直观」冲突 |

补两条实际理由：
1. 桥本身就是 Python，控制台同栈 → 直接 `import` 复用配置读写逻辑，不用起 HTTP 服务；
2. tkinter 是 CPython 官方内置模块，PyInstaller 打包几乎不会漏 DLL，`build.ps1` 一次过。

## 二、目录与结构

```
fishdesk/
  main.py                 入口：单实例 → 管理员自提升 → 建窗口
  fishdesk.spec           PyInstaller 打包描述（onefile / uac_admin / 图标 / 资源）
  build.ps1               一键建 venv + 装依赖 + 打包 → dist\XiaoFeiYuConsole.exe
  shoot.py / smoke_test.py 渲染每页并截图 / 无头冒烟测试
  app/
    paths.py              路径解析（源码态 vs sys._MEIPASS）、%APPDATA% 设置文件
    config_store.py       统一配置的 schema / 深合并 / 保存热通知
    bootstrap.py          单实例互斥体、管理员检测与自提升、DPI
    hooks.py              消息钩子总线（语音等扩展挂这里）
    services/             bridge.py（起停/状态/日志尾巴）、power.py（唤醒时段/休眠）、stats.py
    plugins/              每个页面一个文件，register() 注册到左侧导航
    ui/                   theme.py 配色、widgets.py 通用控件、window.py 主窗口
```

**UI 结构**：左侧固定导航（7 项）+ 右侧内容区，每页独立滚动，互不干扰；底部状态栏显示工作区/桥进程/管理员。

| 页面 | 做什么 |
| --- | --- |
| 控制台 | 微信在线、桥运行两颗状态灯；启动/停止桥、拉起微信；紧凑实时日志（自动脱敏 wxid/群号） |
| 人设设置 | 直接编辑 `persona.md`，显示字数与保存状态 |
| 回复策略 | 安静窗口、冷却、气泡切分、群聊上限等滑杆，改动即写 config.json |
| 表情库 | `meta.json + tags25.json` 合并成标签库，缩略图网格分页预览 |
| 作息设置 | 多时段唤醒（增删改）、是否允许微信指令休眠、触发词、闲置阈值、「立即休眠」测试、「应用计划任务」重建 DSH-Power-Wake |
| 高级设置 | dry-run、日记开关、token 统计、解释器路径 |
| 语音（预留） | `voice_input/voice_output/voice_engine` 占位，开关先落配置 |

## 三、配置与热重载

- 唯一真源：`wechat-bridge/config.json`（模板见 `config.example.json`；真实文件含 owner wxid，已被 `.gitignore` 排除）。
- 控制台保存 → 写盘 → 桥在**下一轮循环**（≈1.5 s 内）通过 mtime 变化调用 `apply_ui_config()`，只重载白名单常量（策略/owner/触发词等）；DSH 路径、端口等危险项不热改。
- 作息页保存时同步镜像一份到 `power/config.json`（旧脚本仍读它），并可按新时段重建计划任务。
- 早安脚本（`morning_greeting.py` / `morning_wait.py`）不再写死 wxid，读 `config.json` 的 `identity.owner_wxid`。

## 四、扩展点（语音怎么接）

1. `config.json` → `voice{enabled, voice_input, voice_output, voice_engine, asr_model, tts_voice, vad_seconds}`；
2. `wechat-bridge/bridge_hooks.py` → 桥每轮 `import`，实现 `on_message_received(chat, sender, text, raw, is_group)` / `on_message_sent(chat, to, text, kind, local_id)` 即成为中间件；返回 dict 可改写 payload，异常只记日志不影响收发；
3. `fishdesk/app/hooks.py` → 控制台侧钩子总线（`on_message_received/on_message_sent/on_voice_input/on_voice_output/on_config_reload/on_persona_saved`），加语音面板时注册即可；
4. `fishdesk/app/plugins/__init__.py` 的 `PLUGIN_MODULES` → 新页面加一个文件 + 一行注册。

## 五、构建与运行

```powershell
cd fishdesk
.\build.ps1                 # 首次：建 venv、装依赖、打包
.\build.ps1 -SkipInstall    # 已装好依赖时
```

产物：`fishdesk\dist\XiaoFeiYuConsole.exe`（单文件；内嵌清单为 `asInvoker`，双击不弹 UAC，需要管理员的操作由代码 `ShellExecuteW(runas)` 单独提权；另有文件夹版 `dist_onedir\`）。

- 单实例：`Global\XiaoFeiYuConsole_9F3A1` 互斥体 + `127.0.0.1:47731` 兜底，重复启动会激活已有窗口；
- 资源路径：打包后从 `sys._MEIPASS` 取图标/主题资源，源码态回落到项目目录；
- 调试时设 `FISHDESK_NO_ELEVATE=1` 可跳过自提升，避免反复弹 UAC。

打包踩过的坑（均已修好）：

1. 页面插件由 `app/plugins/__init__.py` 用 `importlib.import_module` 动态加载，PyInstaller 静态分析看不到；`fishdesk.spec` 里必须 `collect_submodules("app")`，否则冻结后左侧导航空白、右侧没有任何内容（源码态却不报错）；
2. （FAQ）双击弹 `Could not create temporary directory!`：单文件包启动时要把内容自解压到 `%TEMP%` 下的 `_MEIxxxxxx`，受限沙箱、企业策略或杀软拦截该目录时会在 Python 代码执行前就失败——所以单纯在 `main.py` 里改 `os.environ["TEMP"]` 没用（bootloader 先解包）。
   - 已修：`fishdesk.spec` 里 `runtime_tmpdir="."`，解包目录改为 exe 同级（双击时 cwd 即 exe 目录），彻底不依赖 `%TEMP%`；
   - 验证：把 `TEMP`/`TMP` 指向不可写的 `C:\Windows` 启动，exe 同级仍生成 `_MEIxxxxxx`，`app-launch.log` 记 `temp=C:\Windows` 后约 1 s 出现 `tag=window-ready`；
   - 仍被拦（杀软隔离 `_MEI*`）：给 exe 所在目录加白名单，或改用文件夹版 `dist_onedir\`（无自解压，启动更快；`dist_onedir.zip` 约 18.9 MB 可直接分发）。

## 六、安全与已知限制

- 源码中不再出现真实 wxid / 群号；控制台读日志时用正则脱敏（`wxid_***`、`***@chatroom`）；`config.json`、`power/config.json`、日志、会话、备份、表情原图均被 git 忽略。
- 桥仍按原 `bridge.pid` 单实例运行，控制台只负责起停，不重写桥的自愈逻辑。
- 早安脚本、计划任务要管理员权限；控制台非管理员时会明确提示。

## 七、下一步

1. 语音接入：`voice_engine` 落地（本地 ASR/TTS），面板做成开关 + 音色选择；
2. 表情库可视化增删 + 标签编辑（当前只读预览）；
3. 桥日志分级过滤与搜索；
4. 安装器（Inno Setup）与开机自启开关。
