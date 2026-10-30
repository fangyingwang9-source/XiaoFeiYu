# 控制台 UI 重构（2026-10-31）

范围：控制台页交互 / 人设页重构 / 回复策略页改版 / DIY 开关 / 一屏布局。

## 1. 参考设计（网上查到的做法）

- Persona 预设选择器（AI UX Playground，`persona-selector` 模式）
  <https://aiuxplayground.com/pattern/persona-selector/>
  要点：先给 3–5 个「人格卡片/下拉预设」，一键填充全部字段，再允许手改；空表单先给值，降低首屏压力。
- Convai Playground · Personality Traits
  <https://docs.convai.com/staging/convai-playground/character-customization/personality-traits>
  要点：性格（traits）与行为参数（verbosity / response length）分开成两处设置，不混在同一份人设文本里。
- 麦麦（maibot）防抖预设：固定消息防抖只有「关闭 / 快速 / 自然 / 沉稳」几档，而不是裸露毫秒数；数值藏在档位后面。
- AutoGPT PR #13739（persona 配置与提示词分离，生成物不手改）
  <https://github.com/Significant-Gravitas/AutoGPT/pull/13739>
- Voxta Studio · Scenarios
  <https://doc.voxta.ai/docs/server/studio/scenarios>
  要点：场景/人设是受控表单，运行时参数（字数、冷却、上下文长度）独立成策略面板。

共性结论：**可量化参数（字数、频率、冷却、上下文轮数）从人设文本里抽出来单独放策略页；人设只留性格底色；人设文件由程序生成，用户不再手改。**

## 2. 改前 → 改后

### 控制台
- 改前：启动桥用 `python.exe` + `CREATE_NO_WINDOW`，部分环境下仍闪黑窗；点击后无反馈，按钮状态不联动。
- 改后：优先同目录 `pythonw.exe`（GUI 子系统，Windows 保证无控制台），退路才是 python.exe + `DETACHED_PROCESS|CREATE_NO_WINDOW`；按钮点击立刻进入 `正在启动…`（禁用），完成后「启动桥」变灰「已在运行」、「停止桥」高亮；状态 1.5 s 轮询刷新，异步结果经队列回主线程，无 messagebox 弹窗。

### 人设页
- 改前：一整块 Markdown 文本框（persona.md 原文），普通用户不敢改，改字数又要同时改策略页 → 双写。
- 改后：模板下拉（正常陪伴 / 高冷毒舌 / 沙雕整活 / 温柔体贴）+「恢复默认」+「套用推荐节奏」；表单只有昵称 / 身份 / 性格底色 / 口头禅 / 示例 / 红线；底部是只读预览 + 「保存并生成」+「打开 persona.md」。
- **关键决定：`persona.md` 是生成物**，由 `app/persona_profile.py` 从 `persona.profile` + `strategy.*` 渲染，手改会被覆盖（每次覆盖前备份到 `persona-backups/`）。数字只存在 config.json 一处 → 双写彻底消失。

### 策略页
- 改前：术语（裁判冷却 / 链长上限 / 气泡数）直接暴露；每个参数一个滑块，精确值调不准。
- 改后：2 列 5 张卡一屏放下（1180×812 实测不滚动）；术语改白话（防刷屏冷却时间 / 上下文记忆轮数 / 一条回复最多拆成…）；每个参数带 `?` 悬停说明；区间参数用双数值 + `− / +` 微调框（可直接输入，失焦校正、越界夹取）；每行一个 DIY 开关，关闭时用内置安全默认并置灰，旁边显示「用默认 X」；逻辑冲突（区间反向、短回 > 上限、冷场比热聊还急、冷却远大于间隔）在页面顶部红条里列出，改对即消失；右上「全部恢复内置默认」。

## 3. 实现要点
- `app/ui/controls.py`：`Tooltip` / `help_dot` / `Banner` / `SpinRow` / `RangeRow` / `TextField` / `AreaField` / `segmented`。`SpinRow` 关闭时装态禁用，commit 用 Return / FocusOut，越界自动夹取并回写。
- `app/persona_profile.py`：`DEFAULT_PROFILE`、4 套 `PRESETS`（含推荐节奏）、`render_persona`、`render_conflicts`、`sync_persona`（内容相同则跳过写盘与备份）、`backup_persona`。
- `app/ui/window.py`：启动时与每次 config 变更后调 `sync_persona(force=False)` → 改策略页数值即自动重生成 persona.md，无需保存按钮。
- `main.py` 一次性迁移 `_migrate_v2`：历史默认 `max_reply_chars=1200` 与 6–18 字模型自相矛盾，首次启动降到 400 并写 `strategy.v2_migrated`。
- 新增配置项都进 `DEFAULTS`（`ConfigStore._clean` 按顶层 section 白名单丢弃未知键）。

## 4. 自测（build venv Python 3.12.14 + customtkinter）
- 截图：console / persona / strategy 均 1180×812；策略页内容高 613 px < 视口 703 px，`yview()==(0.0, 1.0)` → 一屏不滚动（红条出现时约 +84 px，可滚动，属于提示态）。
- 策略页：改值后 0.5 s 内写盘，`config.json` 24 个策略键与内存一致；DIY 关→开→关往返后 `tuning` 为空；「全部恢复默认」回 6/18/{}。
- 冲突：把「被怼时只回」调到 40 > 上限 18 → 红条出现并给出两条文案；恢复后红条文本为空。
- 人设页：套用「高冷毒舌」后昵称/底色被填充，保存生成 790 字 persona.md；重启后自动重生成一致。
- 控制台：桥在运行时「启动桥」为 disabled「已在运行」，「停止桥」为 ACCENT 可点；触发启动瞬间三个按钮进入 `正在启动…`/disabled。

## 5. 未验证 / 已知取舍
- 打包后的 exe 只做了启动与页面截图冒烟，未在真机 WeChat 上跑完整收发链路。
- 红条出现时页面会短暂出现纵向滚动条（提示内容挤压，非布局 bug）。
- 人设页仍是表单长页（有滚动），只要求策略页一屏。
