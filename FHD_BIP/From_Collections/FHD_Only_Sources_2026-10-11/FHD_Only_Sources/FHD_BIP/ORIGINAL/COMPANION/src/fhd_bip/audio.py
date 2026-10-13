from __future__ import annotations

import array
import math
import queue
import sys
import threading
from collections import deque
from typing import Callable


class AudioError(RuntimeError):
    pass


MICROPHONE_THRESHOLDS_DBFS = {
    # Retained from v0.5.3.1 after regression tests: balanced pickup/noise presets.
    "low": -43.0,
    "medium": -49.0,
    "high": -55.0,
}


def microphone_threshold_dbfs(mode: str, trim_db: float = 0.0) -> float:
    """Return the local speech-gate threshold. Positive trim means more sensitive."""
    base = MICROPHONE_THRESHOLDS_DBFS.get(mode, MICROPHONE_THRESHOLDS_DBFS["low"])
    try:
        trim = float(trim_db)
    except (TypeError, ValueError):
        trim = 0.0
    trim = max(-6.0, min(10.0, trim))
    # Lower dBFS threshold = quieter speech is accepted.
    return max(-66.0, min(-30.0, base - trim))


def pcm16_dbfs(chunk: bytes) -> float:
    """Return RMS volume of little-endian PCM16 audio in dBFS."""
    if not chunk or len(chunk) % 2:
        return float("-inf")
    samples = array.array("h")
    samples.frombytes(chunk)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return float("-inf")
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 0:
        return float("-inf")
    return 20.0 * math.log10(math.sqrt(mean_square) / 32768.0)


def scale_pcm16(chunk: bytes, volume_percent: int | float) -> bytes:
    """Scale little-endian PCM16 without changing its format or duration."""
    if not chunk or len(chunk) % 2:
        return chunk
    percent = max(0, min(100, int(round(float(volume_percent)))))
    if percent == 100:
        return chunk
    if percent == 0:
        return bytes(len(chunk))
    gain = percent / 100.0
    samples = array.array("h")
    samples.frombytes(chunk)
    if sys.byteorder != "little":
        samples.byteswap()
    for index, sample in enumerate(samples):
        samples[index] = max(-32768, min(32767, int(round(sample * gain))))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


class SpeechGate:
    """Suppress short/quiet microphone noise before it reaches Gemini Live."""

    def __init__(
        self,
        threshold_dbfs: float = -40.0,
        start_chunks: int = 2,
        stop_chunks: int = 6,
        preroll_chunks: int = 4,
        noise_margin_db: float = 9.0,
        sensitivity_trim_db: float = 0.0,
    ):
        self.threshold_dbfs = float(threshold_dbfs)
        self.noise_margin_db = max(3.0, float(noise_margin_db))
        self.sensitivity_trim_db = max(-6.0, min(10.0, float(sensitivity_trim_db)))
        self._noise_floor_dbfs = -70.0
        self.last_level_dbfs = float("-inf")
        self.last_effective_threshold_dbfs = self.threshold_dbfs
        self.start_chunks = max(1, int(start_chunks))
        self.stop_chunks = max(1, int(stop_chunks))
        self._preroll: deque[bytes] = deque(maxlen=max(self.start_chunks, int(preroll_chunks)))
        self._voice_chunks = 0
        self._silent_chunks = 0
        self.is_active = False

    def process(self, chunk: bytes) -> tuple[list[bytes], bool]:
        if not chunk:
            return [], False
        level_dbfs = pcm16_dbfs(chunk)
        adaptive_threshold = self.effective_threshold_dbfs()
        self.last_level_dbfs = level_dbfs
        self.last_effective_threshold_dbfs = adaptive_threshold
        voiced = level_dbfs >= adaptive_threshold

        if not self.is_active:
            if not voiced and math.isfinite(level_dbfs):
                self._noise_floor_dbfs = (
                    0.8 * self._noise_floor_dbfs + 0.2 * level_dbfs
                )
            self._preroll.append(chunk)
            self._voice_chunks = self._voice_chunks + 1 if voiced else 0
            if self._voice_chunks < self.start_chunks:
                return [], False
            self.is_active = True
            self._silent_chunks = 0
            buffered = list(self._preroll)
            self._preroll.clear()
            return buffered, False

        self._silent_chunks = 0 if voiced else self._silent_chunks + 1
        if self._silent_chunks < self.stop_chunks:
            return [chunk], False

        self.is_active = False
        self._voice_chunks = 0
        self._silent_chunks = 0
        self._preroll.clear()
        return [chunk], True

    def configure_sensitivity(self, threshold_dbfs: float, trim_db: float = 0.0) -> None:
        """Apply sensitivity immediately to both fixed and adaptive noise thresholds."""
        self.threshold_dbfs = float(threshold_dbfs)
        self.sensitivity_trim_db = max(-6.0, min(10.0, float(trim_db)))
        self.last_effective_threshold_dbfs = self.effective_threshold_dbfs()

    def effective_threshold_dbfs(self) -> float:
        # The trim must affect the adaptive branch too. In v0.5.2 only the fixed
        # threshold changed, so a learned noise floor could mask the slider.
        adaptive_noise_threshold = (
            self._noise_floor_dbfs + self.noise_margin_db - self.sensitivity_trim_db
        )
        return max(self.threshold_dbfs, adaptive_noise_threshold)

    def reset(self) -> bool:
        """Reset buffered activity and report whether an open stream must end."""
        was_active = self.is_active
        self.is_active = False
        self._voice_chunks = 0
        self._silent_chunks = 0
        self._preroll.clear()
        return was_active


class AudioIO:
    INPUT_RATE = 16000
    OUTPUT_RATE = 24000
    CHANNELS = 1
    DTYPE = "int16"

    def __init__(self):
        self._sd = None
        self._input = None
        self._output = None
        self._out_q: queue.Queue[bytes | None] = queue.Queue(maxsize=100)
        self._out_thread: threading.Thread | None = None
        self._running = False
        self._volume_lock = threading.Lock()
        self._output_volume_percent = 100
        self._output_active = threading.Event()

    @property
    def output_volume_percent(self) -> int:
        with self._volume_lock:
            return self._output_volume_percent

    def set_output_volume(self, percent: int | float) -> None:
        value = max(0, min(100, int(round(float(percent)))))
        with self._volume_lock:
            self._output_volume_percent = value

    @property
    def is_output_active(self) -> bool:
        return self._output_active.is_set() or not self._out_q.empty()

    def _load(self):
        if self._sd is None:
            try:
                import sounddevice as sd
            except Exception as exc:
                raise AudioError("Не удалось загрузить sounddevice/PortAudio") from exc
            self._sd = sd
        return self._sd

    def start(self, on_input: Callable[[bytes], None], enable_input: bool = True) -> None:
        if self._running:
            return
        if self._out_thread and self._out_thread.is_alive():
            raise AudioError("Предыдущий поток вывода звука ещё не остановлен")
        sd = self._load()
        self._out_q = queue.Queue(maxsize=100)
        self._running = True
        try:
            self._output = sd.RawOutputStream(
                samplerate=self.OUTPUT_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                blocksize=0,
            )
            self._output.start()
        except Exception as exc:
            self._running = False
            self._output = None
            raise AudioError(f"Не удалось открыть устройство вывода звука: {exc}") from exc

        output = self._output
        out_q = self._out_q

        def output_worker():
            while self._running:
                chunk = out_q.get()
                if chunk is None:
                    break
                try:
                    self._output_active.set()
                    output.write(scale_pcm16(chunk, self.output_volume_percent))
                except Exception:
                    pass
                finally:
                    if out_q.empty():
                        self._output_active.clear()

        self._out_thread = threading.Thread(target=output_worker, name="FHD-AudioOut", daemon=True)
        self._out_thread.start()

        if enable_input:
            def callback(indata, frames, time_info, status):
                if not self._running:
                    return
                try:
                    on_input(bytes(indata))
                except Exception:
                    pass

            try:
                self._input = sd.RawInputStream(
                    samplerate=self.INPUT_RATE,
                    channels=self.CHANNELS,
                    dtype=self.DTYPE,
                    blocksize=1600,
                    callback=callback,
                )
                self._input.start()
            except Exception as exc:
                self.stop()
                raise AudioError(f"Не удалось открыть микрофон: {exc}") from exc

    def play(self, pcm16: bytes) -> None:
        if not pcm16 or not self._running:
            return
        try:
            self._out_q.put_nowait(pcm16)
        except queue.Full:
            try:
                self._out_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._out_q.put_nowait(pcm16)
            except queue.Full:
                pass

    def clear_output(self) -> None:
        """Drop buffered model speech after Gemini reports an interruption."""
        while True:
            try:
                self._out_q.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._running = False
        self._output_active.clear()
        if self._input is not None:
            try:
                self._input.stop()
                self._input.close()
            except Exception:
                pass
            self._input = None
        self.clear_output()
        self._out_q.put_nowait(None)
        if self._output is not None:
            try:
                self._output.stop()
                self._output.close()
            except Exception:
                pass
            self._output = None
        if self._out_thread:
            self._out_thread.join(timeout=1.5)
            if not self._out_thread.is_alive():
                self._out_thread = None
