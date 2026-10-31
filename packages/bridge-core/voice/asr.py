"""Local ASR via sherpa-onnx SenseVoiceSmall (int8 ONNX, CPU)."""
from __future__ import annotations

import os
import threading

from .audio import decode_voice_file, load_wav_16k

DEFAULT_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


class SenseVoiceASR:
    """Lazy-loaded, thread-safe SenseVoice recognizer."""

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR, threads: int = 2, language: str = "auto"):
        self.model_dir = model_dir
        self.threads = threads
        self.language = language
        self._rec = None
        self._lock = threading.Lock()

    @property
    def model_path(self) -> str:
        return os.path.join(self.model_dir, "model.int8.onnx")

    @property
    def tokens_path(self) -> str:
        return os.path.join(self.model_dir, "tokens.txt")

    def ready(self) -> bool:
        return os.path.isfile(self.model_path) and os.path.isfile(self.tokens_path)

    def _ensure(self):
        if self._rec is not None:
            return self._rec
        import sherpa_onnx

        if not self.ready():
            raise FileNotFoundError(f"ASR model missing in {self.model_dir} (need model.int8.onnx + tokens.txt)")
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self.model_path,
            tokens=self.tokens_path,
            num_threads=self.threads,
            use_itn=True,
            language=self.language,
            debug=False,
        )
        return self._rec

    def transcribe_samples(self, samples) -> str:
        with self._lock:
            rec = self._ensure()
            stream = rec.create_stream()
            stream.accept_waveform(16000, samples)
            rec.decode_stream(stream)
        return (stream.result.text or "").strip()

    def transcribe_file(self, path: str) -> str:
        wav = decode_voice_file(path)
        return self.transcribe_samples(load_wav_16k(wav))
