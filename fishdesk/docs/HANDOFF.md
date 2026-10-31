# 小肥鱼项目 交接说明（更新于 2026-10-31 深夜）

> 给下一个会话用：先读这一个文件，再按需读第 6 节的「按需文件」。不要全仓库乱翻。

## 1. 一句话现状

桌面控制台「小肥鱼控制台」(fishdesk) 已完成三页重构（控制台/人设/回复策略），配置统一写 `wechat-bridge/config.json`，微信桥热加载生效；新包已构建并替换 dist。**代码本地已提交（HUMAN_STYLE 移除等改动见 git log），远端推送需确认凭据 `git:https://github.com` 存在

## 2. 仓库结构

```
E:\useer\deepseek-harness\default-workspace\
├─ fishdesk\                     桌面控制台（CustomTkinter）
│  ├─ main.py                    入口：_boot_log()、_migrate_v2()、单实例、可选提权
│  ├─ fishdesk.spec              PyInstaller onefile（注意 runtime_tmpdir="."）
│  ├─ app\config_store.py        DEFAULTS + SCHEMA + _clean()（唯一配置读写口）
│  ├─ app\persona_profile.py     ★人设渲染 render_persona() / render_conflicts() / sync_persona()
│  ├─ app\ui\window.py           MainWindow 1180x812、1500ms _poll、懒加载页面
│  ├─ app\ui\widgets.py          BasePage / Card / theme 常量
│  ├─ app\ui\controls.py         ★Tooltip / help_dot / Banner / SpinRow / RangeRow / TextField
│  ├─ app\plugins\{console,persona,strategy,stickers,schedule,advanced,voice}.py
│  ├─ app\services\bridge.py     桥进程管理（pythonw + CREATE_NO_WINDOW）
│  ├─ shoot.py                   截图脚本（遍历 win.specs → screenshots\<key>.png）
│  ├─ dist\XiaoFeiYuConsole.exe  当前发布包 19,861,349 B（旧版 .prev）
│  └─ _backup\ui-refactor-20261031\   重构前备份（未入库）
├─ wechat-bridge\               微信桥（wxauto + pywin32）
│  ├─ wechat_bridge.py           主循环；_UI_STRATEGY_MAP 把 strategy.* 映射成常量
│  ├─ persona.md                 ★生成物（不要手改，见下）
│  ├─ config.json                ★唯一真源
│  └─ persona-backups\           每次重生成前的自动备份（未入库）
├─ devlog\2026-10-31.md          开发日志（历史坑）
├─ devlog\2026-10-31-ui-refactor.md  本轮重构：参考设计/前后对比
└─ tools\PortableGit\cmd\git.exe
```

## 3. 已完成（按时间）

1. **控制台落地**：CustomTkinter 6.0，左侧 7 页（控制台/人设/回复策略/表情库/作息/高级/语音扩展），插件式 `@register` + `build(parent, ctx)`。
2. **配置统一**：UI 只写 `wechat-bridge/config.json`；桥监控 mtime（≤2 s）调 `apply_ui_config()` 热加载；`power/config.json` 是旧子集，已镜像。
3. **打包与启动坑**：onefile `Could not create temporary directory` → `runtime_tmpdir="."`；UAC → `uac_admin=False`（asInvoker，桌面快捷方式不再弹）；双击无反应排查入口 = exe 同级 `app-launch.log`（逐行 tag: start/workspace/window-ready/crash）。
4. **去黑窗**：所有 spawn 走 `pythonw.exe` + `CREATE_NO_WINDOW|DETACHED|NEW_GROUP`；桥自愈走计划任务 `DSH-Bridge-Keepalive` → `pythonw keepalive_runner.py`（别再改回 cmd/bat）。
5. **本轮三页重构（commit 81520be / 359a394）**：
   - 控制台：启动/停止异步化（线程 + queue，150 ms 轮询，20 s 超时），按钮「正在启动…」→「已在运行」灰 +「停止桥」高亮，无 messagebox、无黑窗。
   - 人设：`persona.md` 变生成物；新增预设模板（正常陪伴/高冷毒舌/沙雕整活/温柔体贴）+ 恢复默认 + 表单式性格底色 + 只读预览；保存时原子写 + 自动备份。
   - 策略：一屏双列五卡（视口 703 px / 内容 613 px 不滚动），术语口语化，全部参数带 `?` 说明，双头范围/微调输入，每项 DIY 开关（关=内置默认并置灰），冲突红字提示，改完自动保存并重生成 persona.md。
   - `max_reply_chars` 默认 1200 → 400，旧配置 `_migrate_v2()` 一次性迁移（`strategy.v2_migrated`）。

## 4. 当前配置/运行态（实测）

- `config.json`：`preset=warm`、`auto_render=true`、`max_reply_chars=400`、`reply_chars_min=6`、`reply_chars_max=18`、`tease_every=10`、`v2_migrated=true`。
- 桥：`python.exe` pid 8196（11:43:31 启动），微信小号在线。控制台进程当前不在。
- 发包：`fishdesk\dist\XiaoFeiYuConsole.exe`（2026-10-31 06:02 构建）。

## 5. 铁律与坑（改动前必看）

- **persona.md 是生成物**：真源 = `persona.profile`（性格底色）+ `strategy.*`（6–18 字、毒舌频率等所有数值）。手改会被启动/改配置/人设页保存覆盖；覆盖前自动备份到 `wechat-bridge/persona-backups`。渲染入口 `app/persona_profile.py`。
- **第二处风格来源**：`wechat_bridge.py` 的 `HUMAN_STYLE`（145 字符）仍独立，改语气要两处都动（尚未合并，见 TODO）。
- **新增配置键必须同时进 `DEFAULTS`**，否则 `ConfigStore._clean()` 按顶层 section 把未知键静默丢掉。
- **页面插件靠 importlib 动态加载**：spec 里必须 `collect_submodules("app")`，否则冻结包侧栏空白。
- **Card 只能 mount 一个容器**：重复 `card.mount()` 会叠在一起；行内用 `pack` 排。
- **改完 `wxui.py`/`wechat_bridge.py` 必须重启桥**（内存里还是旧代码）。重启：`schtasks /End` → 杀 pid → `pythonw keepalive_runner.py`。
- **替换 dist 里的 exe 前要先关控制台**，否则 Copy-Item 报文件被占用。
- **从 shell 直接起 GUI 会立即退出**：用 `Start-Process explorer.exe "<exe>"` 或 `Start-Process -RedirectStandardOutput`。
- **PowerShell 脚本文件会被执行策略拒绝**（`无法加载文件…禁止运行脚本`）：用内联命令；必须落盘的 .ps1 要 UTF-8 **带 BOM**（5.1 否则按 GBK 解析成乱码）。
- **git**：`tools\PortableGit\cmd\git.exe`；push 要代理 `-c http.proxy=http://127.0.0.1:7890`（凭据在 Windows 凭据管理器 `git:https://github.com`）；`git config --global` 会锁失败，改配置直接编辑 `C:\Users\one\.gitconfig`；push 时 stderr 那行 "To https://…" 是正常输出，退出码 0 即成功。
- **打包环境**：`fishdesk\.venv-build\Scripts\python.exe`（3.12.14，含 customtkinter + pyinstaller）；wxauto venv 没有 GUI 依赖，不能打包。
- **UI 自测**跑构建 venv 并把输出重定向到日志再读尾部；截图用 `shoot.py`。

## 6. 按需文件（不要提前全读）

| 想干什么 | 读这个 |
|---|---|
| 改页面布局/控件 | `fishdesk/app/ui/controls.py` + 对应 `app/plugins/*.py` |
| 改配置项 | `fishdesk/app/config_store.py`（DEFAULTS/SCHEMA） |
| 改人设文案 | `fishdesk/app/persona_profile.py` 的 `render_persona()` |
| 桥怎么读配置 | `wechat-bridge/wechat_bridge.py` 的 `_UI_STRATEGY_MAP` / `apply_ui_config` |
| 起停桥逻辑 | `fishdesk/app/services/bridge.py` |
| 历史踩坑 | `devlog/2026-10-31.md`、`devlog/2026-10-31-ui-refactor.md` |
| 运维细节 | `docs/memory/wechat-bridge-ops.md`、`docs/memory/power-automation.md` |

## 7. 下一步 TODO（按优先级）

1. **把 `HUMAN_STYLE` 并入生成链路**（或在 `persona.md` 里统一表达），彻底消灭第二处风格源。
2. **冻结包逐页验收**：策略页/人设页在打包版里点一遍（本轮只目视了控制台页）。
3. **真实微信收发验证**：改策略数值后确认桥侧行为（长回/短回/毒舌频率）真的变了，而不只是日志里常量变了。
4. 表情库/作息/高级/语音四页仍是老样式，与新控件风格未统一。
5. 未接的功能：结果通知（企业微信群机器人 webhook 路线）、剪贴板发送后还原、语音扩展。
6. 开源准备：README、真实 wxid/群号敏感扫描（已做过一轮，提交前再扫）。

## 8. 常用命令

```powershell
# 打包
cd fishdesk; .venv-build\Scripts\python.exe -m PyInstaller fishdesk.spec --noconfirm --distpath dist4 --workpath build4

# 截图（会起真窗口，逐页截到 screenshots\）
cd fishdesk; .venv-build\Scripts\python.exe shoot.py

# 推代码
$env:HTTPS_PROXY='http://127.0.0.1:7890'
tools\PortableGit\cmd\git.exe -c http.proxy=http://127.0.0.1:7890 push origin main
```
