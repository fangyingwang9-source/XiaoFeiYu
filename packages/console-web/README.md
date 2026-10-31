# @xfy/console-web

小肥鱼控制台的 DSH Web 插件。宿主侧注册同源路由 `/api/dsh/xfy`，把前端的动作转发给本地
`packages/bridge-core/service.py`（默认 `http://127.0.0.1:8787`，随微信桥进程启动）。

## 动作

| action | method | 说明 |
| --- | --- | --- |
| status | GET | 桥状态 + ASR 就绪 + TTS provider；桥离线时返回 offline 快照 |
| tts | POST {text} | 文本 → 语音文件（Edge TTS） |
| asr | POST {path} | 语音文件 → 文本（SenseVoice int8） |
| loopback | POST {text} | 闭环自检：TTS 生成 → SILK/WAV 解码 → ASR 识别 |
| config | GET | persona/strategy/voice 快照 |
| config.save | POST {persona,strategy} | 写回 wechat-bridge/config.json（带备份） |
| bridge.start / bridge.stop / bridge.restart | POST | 进程控制（宿主侧执行，走 keepalive 与 bridge.pid） |

环境变量：`XFY_WORKSPACE`（工作区根，默认 `E:\useer\deepseek-harness\default-workspace`）、
`XFY_BRIDGE_CORE`（bridge-core 地址）。

## 装载

```
dsh plugin --profile desktop add E:\useer\deepseek-harness\default-workspace\packages\console-web
```
