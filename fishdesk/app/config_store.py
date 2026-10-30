"""统一配置文件：读写 + 结构校验 + 热重载（基于 mtime 轮询，无第三方依赖）。

配置文件位置：<工作区>/wechat-bridge/config.json（微信桥与桌面端共用）。
首次启动时若不存在，会从 power/config.json 迁移旧值后生成。
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

DEFAULTS: dict[str, Any] = {
    "meta": {"schema": 1, "updated_by": "fishdesk"},
    "identity": {"owner_wxid": "wxid_owner_placeholder", "owner_name": "主人"},
    "bridge": {
        "python": r"C:\Users\one\.dsh\wxauto\venv\Scripts\python.exe",
        "auto_start": False,
        "log_lines": 60,
        "group_whitelist": ["小肥鱼测试群", "分不清大小王"],
    },
    "persona": {"file": "persona.md", "reload_every_turns": 20},
    "strategy": {
        "quiet_warm": 1.5,
        "quiet_cold": 2.5,
        "active_max": 6,
        "active_max_cold": 9,
        "cooldown": 1.2,
        "cooldown_cold": 3.0,
        "bubble_max": 18,
        "bubble_parts": 3,
        "max_reply_chars": 1200,
        "max_chain": 6,
        "active_window": 150,
        "batch_wait": 1.5,
        "sticker_enabled": True,
        "sticker_every_min": 5,
        "sticker_every_max": 8,
        "spam_trigger": 6,
        "mute": 45,
        "judge_cooldown": 180,
    },
    "schedule": {
        "wake_slots": ["09:00", "20:30"],
        "allow_wechat_sleep": True,
        "sleep_triggers": ["休眠", "睡觉", "睡吧", "去睡"],
        "owner_only": True,
        "idle_sleep_minutes": 30,
        "input_grace_minutes": 10,
        "max_defer_minutes": 120,
        "respect_input": True,
        "notify_on_wake": True,
        "greeting_morning": "早安",
        "greeting_evening": "晚上好",
    },
    "advanced": {
        "diary_enabled": True,
        "dryrun": False,
        "token_stats": True,
        "humanize_log": True,
        "session_reset_daily": True,
        "log_level": "info",
    },
    "voice": {
        "enabled": False,
        "voice_input": False,
        "voice_output": False,
        "voice_engine": "none",
        "asr_model": "",
        "tts_voice": "",
        "vad_seconds": 0.8,
    },
}

# 段 -> 允许的键，保存时用来过滤脏数据
SCHEMA: dict[str, set[str]] = {k: set(v.keys()) for k, v in DEFAULTS.items() if isinstance(v, dict)}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _clean(data: dict) -> dict:
    """丢掉未知字段，防止手改配置把桥喂坏。"""
    cleaned: dict[str, Any] = {}
    for section, keys in SCHEMA.items():
        src = data.get(section)
        if not isinstance(src, dict):
            continue
        cleaned[section] = {k: v for k, v in src.items() if k in keys}
    for section, val in data.items():
        if section not in SCHEMA and isinstance(val, dict):
            cleaned[section] = val
    return deep_merge(DEFAULTS, cleaned)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.error: str | None = None
        self._mtime_ns: int = 0
        self._listeners: list[Callable[[dict], None]] = []
        self._last_save_ts = 0.0

    # ---------- 生命周期 ----------
    def ensure(self) -> bool:
        """文件不存在时创建（并从旧 power/config.json 迁移）。返回是否新建。"""
        if self.path.exists():
            return False
        cfg = copy.deepcopy(DEFAULTS)
        legacy = self.path.parent / "power" / "config.json"
        if legacy.exists():
            try:
                old = json.loads(legacy.read_text(encoding="utf-8-sig"))
                s = cfg["schedule"]
                s["wake_slots"] = list(old.get("wake_slots") or s["wake_slots"])
                s["idle_sleep_minutes"] = int(old.get("idle_minutes", s["idle_sleep_minutes"]))
                s["respect_input"] = bool(old.get("respect_input", s["respect_input"]))
                s["input_grace_minutes"] = int(old.get("input_grace_minutes", s["input_grace_minutes"]))
                s["max_defer_minutes"] = int(old.get("max_defer_minutes", s["max_defer_minutes"]))
                s["notify_on_wake"] = bool(old.get("notify_on_wake", s["notify_on_wake"]))
                s["greeting_morning"] = old.get("greeting_morning") or s["greeting_morning"]
                s["greeting_evening"] = old.get("greeting_evening") or s["greeting_evening"]
                if old.get("owner_wxid"):
                    cfg["identity"]["owner_wxid"] = old["owner_wxid"]
            except Exception:
                pass
        self.data = cfg
        self.save()
        return True

    def load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            self.data = _clean(raw)
            self.error = None
        except FileNotFoundError:
            self.ensure()
        except Exception as exc:  # 坏文件：保留内存里的上一份，别把界面搞崩
            self.error = f"{type(exc).__name__}: {exc}"
        try:
            self._mtime_ns = self.path.stat().st_mtime_ns
        except Exception:
            self._mtime_ns = 0
        return self.data

    def save(self, data: dict | None = None) -> bool:
        if data is not None:
            self.data = _clean(data)
        payload = _clean(self.data)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            self.data = payload
            self._mtime_ns = self.path.stat().st_mtime_ns
            self._last_save_ts = time.time()
            self.error = None
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    # ---------- 读写 ----------
    def get(self, dotted: str, default=None):
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self.data
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = value

    def update_and_save(self, dotted: str, value: Any) -> bool:
        self.set(dotted, value)
        return self.save()

    # ---------- 热重载 ----------
    def add_listener(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def poll(self) -> bool:
        """mtime 变了就重载并通知监听者。返回是否发生重载。"""
        try:
            st = self.path.stat()
        except Exception:
            return False
        if st.st_mtime_ns == self._mtime_ns:
            return False
        # 自己刚写的：更新 mtime 即可，不触发 UI 重建
        if time.time() - self._last_save_ts < 1.5:
            self._mtime_ns = st.st_mtime_ns
            return False
        self._mtime_ns = st.st_mtime_ns
        self.load()
        for fn in list(self._listeners):
            try:
                fn(self.data)
            except Exception:
                pass
        return True

    # ---------- 兼容层 ----------
    def sync_power_config(self) -> bool:
        """把作息段回写成 power/*.ps1 仍在读的旧格式。"""
        p = self.path.parent / "power" / "config.json"
        s = self.data.get("schedule", {})
        ident = self.data.get("identity", {})
        legacy = {
            "ssid": s.get("ssid", "Xiaomi_9FE2"),
            "idle_minutes": s.get("idle_sleep_minutes", 30),
            "respect_input": bool(s.get("respect_input", True)),
            "input_grace_minutes": s.get("input_grace_minutes", 10),
            "max_defer_minutes": s.get("max_defer_minutes", 120),
            "notify_on_wake": bool(s.get("notify_on_wake", True)),
            "greeting_morning": s.get("greeting_morning", ""),
            "greeting_evening": s.get("greeting_evening", ""),
            "wake_slots": list(s.get("wake_slots") or []),
            "owner_wxid": ident.get("owner_wxid", ""),
        }
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, p)
            return True
        except Exception:
            return False
