"""音频注入层：把一段音频送进"微信认为的麦克风"。

为什么单独一层：微信 4.x 只能"按住右 Alt 起录音"，录音源是微信设置里选的麦克风。
把 TTS 灌到 CABLE Input（虚拟扬声器），微信麦克风选 CABLE Output，就是纯数字直通。
换播放设备 / 换注入方式，只替换这里的实现，sender 的编排逻辑不动。
"""
from __future__ import annotations

import ctypes
import os
import time

_winmm = ctypes.WinDLL("winmm") if os.name == "nt" else None


class AudioFeeder:
    """把音频播出去（阻塞到播完）。"""

    name = "base"

    def available(self):
        return False, "抽象实现"

    def play_blocking(self, path: str, duration_s=None) -> float:
        raise NotImplementedError


class SpeakerLoopbackFeeder(AudioFeeder):
    """扬声器外放 + 实体麦克风拾音：零安装，代价是占用外放、有环境底噪。"""

    name = "speaker-loopback"
    alias = "xfyfeed"

    def available(self):
        if not os.name == "nt":
            return False, "仅 Windows"
        return True, "扬声器外放，麦克风拾音"

    def _cmd(self, command: str) -> str:
        buf = ctypes.create_unicode_buffer(256)
        err = _winmm.mciSendStringW(command, buf, 256, 0)
        if err:
            raise RuntimeError("MCI error %d: %s" % (err, command))
        return buf.value

    def _close_quiet(self) -> None:
        try:
            self._cmd("close %s" % self.alias)
        except Exception:  # noqa: BLE001
            pass

    def play_blocking(self, path: str, duration_s=None) -> float:
        self._close_quiet()
        started = time.time()
        self._cmd('open "%s" type mpegvideo alias %s' % (path, self.alias))
        try:
            self._cmd("set %s time format milliseconds" % self.alias)
            length = duration_s
            if not length:
                raw = self._cmd("status %s length" % self.alias) or "0"
                length = float(raw.strip() or 0) / 1000.0
            self._cmd("play %s from 0" % self.alias)
            deadline = started + max(0.3, float(length or 1.0))
            while time.time() < deadline:
                time.sleep(0.05)
            return round(time.time() - started, 3)
        finally:
            try:
                self._cmd("stop %s" % self.alias)
            except Exception:  # noqa: BLE001
                pass
            self._close_quiet()


class VirtualCableFeeder(AudioFeeder):
    """VB-CABLE 数字直通：TTS -> CABLE Input，微信麦克风 <- CABLE Output。

    不碰系统默认播放/录制设备，只按设备名定向输出，用户日常听歌画图不受影响。
    """

    name = "virtual-cable"
    PLAY_HINTS = ("CABLE INPUT", "CABLE IN", "CABLE")
    CAPTURE_HINTS = ("CABLE OUTPUT", "CABLE")

    def __init__(self):
        self.last_error = ""
        self.device_name = ""

    @staticmethod
    def _md():
        import miniaudio
        return miniaudio

    def _devices(self):
        md = self._md()
        return list(md.Devices().get_playbacks()), list(md.Devices().get_captures())

    def _pick(self, items, hints):
        for hint in hints:
            for item in items:
                if hint in (item.get("name") or "").upper():
                    return item
        return None

    def playback(self):
        plays, _ = self._devices()
        dev = self._pick(plays, self.PLAY_HINTS)
        if dev is None:
            raise RuntimeError("未找到 CABLE 播放端点（CABLE Input），VB-CABLE 是否已安装？")
        return dev

    def capture(self):
        _, caps = self._devices()
        dev = self._pick(caps, self.CAPTURE_HINTS)
        if dev is None:
            raise RuntimeError("未找到 CABLE 录制端点（CABLE Output）")
        return dev

    def available(self):
        try:
            dev = self.playback()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return False, str(exc)
        return True, "数字直通 -> %s" % dev.get("name")

    def _sd_output(self):
        """按名字找 CABLE 播放端点（sounddevice 下标），优先 WASAPI。"""
        import sounddevice as sd

        devs = sd.query_devices()
        apis = [h.get("name", "") for h in sd.query_hostapis()]
        fallback = None
        for hint in self.PLAY_HINTS:
            for idx, dev in enumerate(devs):
                if dev["max_output_channels"] <= 0:
                    continue
                if hint not in (dev.get("name") or "").upper():
                    continue
                api = apis[dev["hostapi"]] if dev["hostapi"] < len(apis) else ""
                if "WASAPI" in api.upper():
                    return idx, dev
                if fallback is None:
                    fallback = (idx, dev)
            if fallback is not None:
                return fallback
        raise RuntimeError("未找到可发声的 CABLE 播放端点（CABLE Input）")

    def _decode(self, path: str):
        """解码成 (float32 mono, sr)。soundfile 打头，miniaudio 只做兜底。

        别直接用 miniaudio.decode_file：miniaudio 1.71 遇到 CosyVoice 出的部分 mp3
        只解出第一个 MP3 帧（1152 帧 ≈ 0.03s）就返回，不报错。喂给 CABLE 的
        只剩一声「咔」，微信那头录到的全是静音 —— 这就是「空语音只有一声机械音」的根因。
        """
        import numpy as np

        try:
            import soundfile as sf

            data, sr = sf.read(path, dtype="float32", always_2d=True)
            if data is not None and len(data):
                return data.mean(axis=1).astype("float32"), int(sr)
        except Exception as exc:  # noqa: BLE001
            self.last_error = "soundfile 解码失败：%s: %s" % (type(exc).__name__, exc)

        md = self._md()
        dec = md.decode_file(path)
        arr = np.frombuffer(bytes(dec.samples), dtype=np.int16).astype(np.float32) / 32768.0
        nch = int(dec.nchannels or 1)
        if nch > 1 and arr.size:
            arr = arr.reshape(-1, nch)[:, 0]
        self.last_error = "已退回 miniaudio 解码（可能截断）"
        return arr, int(dec.sample_rate)

    def play_blocking(self, path: str, duration_s=None) -> float:
        pcm, sr = self._decode(path)
        expect = len(pcm) / float(sr or 1)
        # 解出来的比应有的短一大截 = 解码器只吐了个头，宁可报错让上层发文字，也别发空语音
        if duration_s and expect + 0.2 < float(duration_s):
            raise RuntimeError("音频只解出 %.2fs，短于应有的 %.2fs（解码截断），拒播"
                               % (expect, float(duration_s)))
        if expect < 0.2:
            raise RuntimeError("音频只解出 %.2fs，几乎为空，拒播" % expect)
        return self._play_sounddevice(pcm, sr, duration_s)

    def _play_sounddevice(self, pcm, sr: int, duration_s=None) -> float:
        import numpy as np
        import sounddevice as sd

        idx, dev = self._sd_output()
        out_sr = int(dev.get("default_samplerate") or sr)
        if out_sr != sr and len(pcm) > 1:
            n = max(1, int(len(pcm) * out_sr / float(sr)))
            pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
        if duration_s:
            want = int(float(duration_s) * out_sr)
            if 0 < want < len(pcm):
                pcm = pcm[:want]
        # 前后各垫 0.3s 静音：让 CABLE 端点先醒过来，别把语音头啃掉
        pad = np.zeros(max(1, int(out_sr * 0.3)), dtype=np.float32)
        data = np.concatenate([pad, pcm, pad])
        self.device_name = dev.get("name") or ""
        started = time.time()
        sd.play(data, out_sr, device=idx, blocking=True)
        return round(time.time() - started, 3)


_FEEDERS = {
    SpeakerLoopbackFeeder.name: SpeakerLoopbackFeeder,
    VirtualCableFeeder.name: VirtualCableFeeder,
}


def get_feeder(name=None) -> AudioFeeder:
    """默认优先 virtual-cable（装了 VB-CABLE 就用数字直通），否则回落外放拾音。"""
    explicit = (name or os.environ.get("WECHAT_FEEDER") or "").strip().lower()
    if explicit:
        cls = _FEEDERS.get(explicit)
        if cls is None:
            raise RuntimeError("未知音频注入方式: %r（可选: %s）" % (explicit, ", ".join(_FEEDERS)))
        return cls()
    vc = VirtualCableFeeder()
    ok, _ = vc.available()
    return vc if ok else SpeakerLoopbackFeeder()
