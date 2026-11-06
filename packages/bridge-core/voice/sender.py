"""语音发送业务编排：文本 -> TTS -> 音频注入 -> 适配层按住/松开发送。

分层边界：
- 本文件不 import wxui / uiautomation，也不知道"右 Alt""窗口置前"的存在；
- 微信侧细节全部由 packages/wechat-adapter 的 WeChatAdapter 负责；
- 声道路由（外放拾音 / 虚拟声卡）由 voice/feeders.py 负责。
换微信版本 = 换 adapter；装虚拟声卡 = 换 feeder；改业务 = 只改这里。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.dirname(HERE)                    # packages/bridge-core
ROOT = os.path.dirname(os.path.dirname(CORE))   # 工作区根
OUT_DIR = os.path.join(CORE, "out")
CONFIG_PATH = os.path.join(ROOT, "wechat-bridge", "config.json")
ADAPTER_DIR = os.path.join(ROOT, "packages", "wechat-adapter")

try:
    from .tts import default_out_path, get_provider
except ImportError:  # pragma: no cover
    from voice.tts import default_out_path, get_provider

try:
    from .feeders import get_feeder
except ImportError:  # pragma: no cover
    from feeders import get_feeder

try:
    from . import defaults as audio_defaults
except ImportError:  # pragma: no cover
    import defaults as audio_defaults

# 桥自身的文本回复和语音发送都会动前台窗口，进程内串行化
_CLIP_DIAG = ""
_SEND_LOCK = threading.Lock()


# 质检门：合成完先让本地 ASR 回读一遍，听不懂/走样就整条作废（改发文字），
# 绝不换嗓（用户明确不要 Edge 晓晓那种人格分裂）。
_QC_ASR = None
_QC_LOCK = threading.Lock()


def _qc_cfg() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:  # noqa: BLE001
        cfg = {}
    voice = cfg.get("voice") or {}
    return {
        "enabled": bool(voice.get("qc_enabled", True)),
        "min_overlap": float(voice.get("qc_min_overlap") or 0.5),
        "asr_model": voice.get("asr_model") or "",
    }


def _norm_text(s: str) -> str:
    """归一化用于回读比对：数字统一成汉字（ASR 常把「一二三」听成「123」），只留字母汉字。"""
    import re

    s = (s or "").translate(str.maketrans("0123456789", "零一二三四五六七八九"))
    return "".join(re.findall(r"[A-Za-z\u4e00-\u9fff]", s)).lower()


def _qc_asr(model_dir: str = ""):
    global _QC_ASR
    with _QC_LOCK:
        if _QC_ASR is None:
            try:
                from .asr import SenseVoiceASR
            except ImportError:  # pragma: no cover
                from asr import SenseVoiceASR
            _QC_ASR = SenseVoiceASR(model_dir or None) if model_dir else SenseVoiceASR()
        return _QC_ASR


def _qc_good_overlap() -> float:
    """回读重叠到这个值就算「清楚」，不再重抽（默认 0.85，可被 voice.qc_good_overlap 覆盖）。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return float((cfg.get("voice") or {}).get("qc_good_overlap") or 0.85)
    except Exception:  # noqa: BLE001
        return 0.85


def qc_check(text: str, audio_path: str) -> dict:
    """回读校验：返回 {ok, overlap, heard, threshold, error?}。"""
    import difflib

    cfg = _qc_cfg()
    target = _norm_text(text)
    out = {"ok": True, "overlap": 1.0, "heard": "", "threshold": cfg["min_overlap"],
           "skipped": True}
    if not cfg["enabled"] or not target:
        return out
    try:
        heard = _qc_asr(cfg["asr_model"]).transcribe_file(audio_path) or ""
    except Exception as exc:  # noqa: BLE001
        out.update({"ok": False, "error": "asr_unavailable", "detail": "%s: %s" % (type(exc).__name__, exc)})
        return out
    heard_n = _norm_text(heard)
    ratio = difflib.SequenceMatcher(None, heard_n, target).ratio() if heard_n else 0.0
    out.update({"skipped": False, "heard": heard, "overlap": round(ratio, 3),
                "ok": ratio >= cfg["min_overlap"]})
    return out


def _tts_cfg() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:  # noqa: BLE001
        cfg = {}
    voice = cfg.get("voice") or {}
    return {
        "provider": voice.get("tts_provider") or "edge",
        "voice": voice.get("tts_voice") or "zh-CN-XiaoxiaoNeural",
        "rate": voice.get("tts_rate") or "+0%",
        "feeder": voice.get("feeder") or None,
    }


def _adapter():
    if ADAPTER_DIR not in sys.path:
        sys.path.insert(0, ADAPTER_DIR)
    from factory import get_adapter

    return get_adapter()


def _step(steps, name, ok=True, detail=""):
    steps.append({"step": name, "ok": bool(ok), "detail": detail})
    return ok


def _info(msg):
    print("[voice] %s" % msg, flush=True)


def _bubble_seconds(extra):
    """优先用界面气泡自己写的时长（「语音5"秒」是微信给的整数秒），读不到再解码 SILK。"""
    blob = " ".join(str(v) for v in (extra or {}).values())
    m = re.search(r"语音\s*(\d+)\s*[\"'\u2033]?\s*秒", blob)
    if m:
        return float(m.group(1)), "bubble"
    return None, ""


def _clip_seconds(getter, chat, since_ts):
    """读刚发出的语音气泡时长（秒）；读不到返回 None，绝不瞎猜。"""
    global _CLIP_DIAG
    _CLIP_DIAG = ""
    if not callable(getter):
        _CLIP_DIAG = "适配层无 newest_voice_clip"
        return None
    try:
        clip = getter(chat, since_ts, wait_s=3.0)
    except Exception as exc:  # noqa: BLE001
        _CLIP_DIAG = "取气泡失败：%s: %s" % (type(exc).__name__, exc)
        return None
    if not clip or not clip.get("path"):
        _CLIP_DIAG = "没找到新气泡(path 空)"
        return None
    p = clip["path"]
    try:  # 微信语音气泡是 SILK，先按它解码算时长
        from bridge_core.voice.audio import silk_to_pcm
        pcm, sr = silk_to_pcm(p, 24000)
        if pcm:
            return round(len(pcm) / 2.0 / float(sr or 24000), 2)
        _CLIP_DIAG = "silk 解码为空"
    except Exception as exc:  # noqa: BLE001
        _CLIP_DIAG = "silk 解码异常：%s" % type(exc).__name__
    try:
        import soundfile as _sf
        info = _sf.info(p)
        if info.frames and info.samplerate:
            return round(float(info.frames) / float(info.samplerate), 2)
    except Exception:  # noqa: BLE001
        pass
    try:
        import wave
        with wave.open(p, "rb") as fh:
            return round(fh.getnframes() / float(fh.getframerate() or 1), 2)
    except Exception:  # noqa: BLE001
        _CLIP_DIAG = (_CLIP_DIAG + "；wave 也读不了").strip("；")
    return None


def send_voice(who="文件传输助手", text=None, audio_path=None, rate=None,
               provider=None, adapter=None, feeder=None, hold_tail_s=0.6) -> dict:
    """把 text（或现成 audio_path）作为语音消息发给 who。

    返回 {ok, sent, who, audio_path, tts_duration_s, hold_s, elapsed_ms, adapter, feeder, steps}。
    """
    t0 = time.time()
    steps = []
    cfg = _tts_cfg()
    audio_path = (audio_path or "").strip() or None
    text = (text or "").strip() or None
    if not text and not (audio_path and os.path.isfile(audio_path)):
        return {"ok": False, "error": "need_text_or_path", "steps": steps}

    with _SEND_LOCK:
        # 1) TTS（也可以用外部传进来的现成音频）
        tts_duration_s = None
        if audio_path:
            _step(steps, "tts", True, "skip: 使用现成音频 %s" % os.path.basename(audio_path))
        else:
            try:
                prov = provider or get_provider(cfg["provider"], voice=cfg["voice"], rate=rate or cfg["rate"])
                res = prov.synthesize(text, default_out_path(OUT_DIR))
                audio_path, tts_duration_s = res.path, round(res.duration_ms / 1000.0, 2)
                _step(steps, "tts", True, "%s %s -> %.2fs" % (res.provider, res.voice, tts_duration_s))
            except Exception as exc:  # noqa: BLE001
                _step(steps, "tts", False, "%s: %s" % (type(exc).__name__, exc))
                return {"ok": False, "error": "tts_failed", "detail": str(exc), "steps": steps}

            # 2) 质检门：回读不够清楚就换个 seed 重抽（口齿含糊是随机采样出来的，
            #    同一句话换一版常常就清楚了），最多抽 3 版取最清楚的那条；
            #    都不行才整条作废交给调用方改发文字，不换嗓、不降级到别的引擎
            qc = qc_check(text, audio_path)
            if not qc.get("skipped"):
                good = _qc_good_overlap()
                if qc.get("overlap", 0.0) < good:
                    first_overlap = qc.get("overlap", 0.0)
                    for attempt in range(2):
                        try:
                            if hasattr(prov, "seed"):
                                prov.seed = int((time.time() * 1000 + attempt * 7919) % 2_000_000_000)
                            cand = prov.synthesize(text, default_out_path(OUT_DIR))
                            qc2 = qc_check(text, cand.path)
                        except Exception:  # noqa: BLE001
                            break
                        if qc2.get("overlap", 0.0) > qc.get("overlap", 0.0):
                            old = audio_path
                            audio_path, qc = cand.path, qc2
                            tts_duration_s = round(cand.duration_ms / 1000.0, 2)
                            try:
                                os.remove(old)
                            except OSError:
                                pass
                        else:
                            try:
                                os.remove(cand.path)
                            except OSError:
                                pass
                        if qc.get("overlap", 0.0) >= good:
                            break
                    _step(steps, "qc", qc.get("ok", False),
                          "回读 %.2f → %.2f（重抽 %d 次，听到：%s）" % (
                              first_overlap, qc.get("overlap", 0.0), attempt + 1,
                              (qc.get("heard") or "")[:30]))
            if qc.get("skipped"):
                _step(steps, "qc", True, "跳过（质检关闭或无文本）")
            elif qc.get("ok"):
                _step(steps, "qc", True, "回读一致 %.2f ≥ %.2f：%s" % (
                    qc["overlap"], qc["threshold"], (qc.get("heard") or "")[:40]))
            else:
                _step(steps, "qc", False, "回读 %.2f < %.2f（听到：%s）%s" % (
                    qc.get("overlap", 0.0), qc["threshold"], (qc.get("heard") or "")[:40],
                    qc.get("detail") or ""))
                return {"ok": False, "error": "tts_qc_failed", "detail": (qc.get("heard") or "")[:80],
                        "overlap": qc.get("overlap"), "audio_path": audio_path, "steps": steps}

        try:
            ad = adapter or _adapter()
        except Exception as exc:  # noqa: BLE001
            _step(steps, "adapter", False, str(exc))
            return {"ok": False, "error": "adapter_unavailable", "detail": str(exc), "steps": steps}

        try:
            feed = feeder or get_feeder(cfg.get("feeder"))
        except Exception as exc:  # noqa: BLE001
            _step(steps, "feeder", False, str(exc))
            return {"ok": False, "error": "feeder_unavailable", "detail": str(exc), "adapter": ad.name, "steps": steps}

        started_flag = False
        mic_route = None
        try:
            # 2) 拉窗口 + 打开会话
            info = ad.ensure_ready()
            _step(steps, "focus", True, "hwnd=%s" % info.get("hwnd"))
            ad.open_chat(who)
            time.sleep(0.6)
            _step(steps, "open_chat", True, who)

            # 3) 路由：虚拟声卡方案要把默认录制设备临时指向 CABLE Output（微信 4.x 无应用内麦克风选择）
            if getattr(feed, "name", "") == "virtual-cable":
                try:
                    before = audio_defaults.snapshot(audio_defaults.CAPTURE_FLOW)
                    cable_id, cable_name = audio_defaults.cable_capture()
                    if not cable_id:
                        raise RuntimeError("注册表里找不到 CABLE Output 端点")
                    audio_defaults.set_default(
                        cable_id, audio_defaults.CAPTURE_FLOW,
                        roles=(audio_defaults.ROLE_CONSOLE, audio_defaults.ROLE_MULTIMEDIA,
                               audio_defaults.ROLE_COMMUNICATIONS))
                    mic_route = {"switched": True, "from": before.get("name", ""), "to": cable_name,
                                 "id": cable_id, "snapshot": before, "restored": None}
                    _step(steps, "mic_route", True, "默认录制: %s -> %s" % (before.get("name"), cable_name))
                    _info("mic_route: %s -> %s (roles=console+multimedia+communications, id=%s)"
                          % (before.get("name"), cable_name, cable_id))
                except Exception as exc:  # noqa: BLE001
                    _step(steps, "mic_route", False, str(exc))
                    return {"ok": False, "error": "mic_route_failed", "detail": str(exc), "who": who,
                            "adapter": ad.name, "feeder": feed.name, "steps": steps,
                            "mic_route": mic_route}

            # 3b) 进入录音态（适配层内部处理右 Alt / 长按鼠标差异）
            play_s = float(tts_duration_s or 0.0)
            final = None
            for attempt in (1, 2):
                clip_ts = time.time()
                started_flag = ad.voice_start()
                _step(steps, "voice_start", started_flag,
                      ("录音态已确认(第%d次)" % attempt) if started_flag
                      else "未检测到录音浮层(%s)" % (getattr(ad, "last_error", "") or "无异常"))
                if not started_flag:
                    ad.voice_cancel()
                    return {"ok": False, "error": "voice_start_failed", "who": who,
                            "adapter": ad.name, "feeder": feed.name, "steps": steps,
                            "mic_route": mic_route}
                time.sleep(0.3)     # 起录后的稳定延迟：等微信真的开始采集，别把开头吞掉

                # 4) 边录边把音频放出来（声音由麦克风拾进微信）
                hold_t0 = time.time()
                try:
                    played = feed.play_blocking(audio_path, tts_duration_s)
                    _step(steps, "play", True, "%s 播放 %.2fs" % (feed.name, played))
                    play_s = float(played or play_s)
                except Exception as exc:  # noqa: BLE001
                    _step(steps, "play", False, str(exc))
                    ad.voice_cancel()
                    return {"ok": False, "error": "play_failed", "detail": str(exc),
                            "adapter": ad.name, "feeder": feed.name, "steps": steps, "mic_route": mic_route}

                # 4b) 只播了个头（解码截断/设备没开）=> 微信只会录到一声「咔」的空语音
                if tts_duration_s and played < float(tts_duration_s) * 0.6:
                    ad.voice_cancel()
                    _step(steps, "play_len", False,
                          "实际只播 %.2fs / TTS %.2fs（截断）" % (played, float(tts_duration_s)))
                    if attempt == 1:
                        _info("只播了 %.2fs（应 %.2fs），重播一次" % (played, float(tts_duration_s)))
                        time.sleep(0.5)
                        continue
                    return {"ok": False, "error": "play_truncated",
                            "detail": "只播 %.2fs / 应 %.2fs" % (played, float(tts_duration_s)),
                            "who": who, "adapter": ad.name, "feeder": feed.name,
                            "steps": steps, "mic_route": mic_route}

                time.sleep(max(0.0, float(hold_tail_s)))
                hold_s = round(time.time() - hold_t0, 2)

                # 5) 松开 -> 发送
                ad.voice_stop()
                confirmed = ad.wait_voice_sent(timeout=30.0)
                extra = dict(getattr(ad, "last_send", {}) or {})
                released = bool(extra.get("released", confirmed))
                if confirmed:
                    _step(steps, "send", True, "新语音气泡已确认: %s" % extra.get("bubble"))
                else:
                    _step(steps, "send", released,
                          "已离开录音态，未确认到新气泡" if released else "超时仍在录音态")

                # 5b) 兜底：气泡时长 < 播放时长 * 0.6 => 只录到一截/静音，自动重录一次
                clip_s, clip_src = _bubble_seconds(extra)
                if clip_s is None:
                    clip_s, clip_src = _clip_seconds(getattr(ad, "newest_voice_clip", None), who, clip_ts), "silk"
                short = bool(confirmed and clip_s is not None and play_s > 0.5 and clip_s < play_s * 0.6)
                if clip_s is None:
                    _step(steps, "bubble_check", True,
                          "读不到气泡时长，跳过兜底（%s）" % (_CLIP_DIAG or "无诊断"))
                else:
                    _step(steps, "bubble_check", not short,
                          "气泡 %.1fs(%s) / 播放 %.2fs%s" % (clip_s, clip_src, play_s, "（偏短，重录）" if short else ""))
                final = {"ok": released, "sent": confirmed, "released": released, "who": who,
                         "text": text, "audio_path": audio_path, "tts_duration_s": tts_duration_s,
                         "hold_s": hold_s, "elapsed_ms": int((time.time() - t0) * 1000),
                         "adapter": ad.name, "feeder": feed.name, "steps": steps,
                         "bubbles": extra.get("total_after"), "mic_route": mic_route,
                         "clip_s": clip_s}
                if short and attempt == 1:
                    _info("气泡 %.2fs < 播放 %.2fs*0.6，重录一次" % (clip_s, play_s))
                    time.sleep(0.5)
                    continue
                break
            return final

        except Exception as exc:  # noqa: BLE001
            _step(steps, "exception", False, "%s: %s" % (type(exc).__name__, exc))
            try:
                if started_flag or ad.recording():
                    ad.voice_cancel()
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "error": "exception", "detail": str(exc), "who": who,
                    "adapter": getattr(ad, "name", None), "steps": steps, "mic_route": mic_route}
        finally:
            # 6) 无论成败都把默认录制设备还原，绝不留给用户
            if mic_route and mic_route.get("switched") and mic_route.get("restored") is None:
                try:
                    actual = audio_defaults.restore(mic_route.get("snapshot"), audio_defaults.CAPTURE_FLOW)
                    mic_route["restored"] = True
                    _step(steps, "mic_restore", True, "还原默认录制 -> %s" % actual)
                    _info("mic_restore: 默认录制已还原 -> %s" % actual)
                except Exception as exc:  # noqa: BLE001
                    mic_route["restored"] = False
                    mic_route["restore_error"] = str(exc)
                    _step(steps, "mic_restore", False, str(exc))
