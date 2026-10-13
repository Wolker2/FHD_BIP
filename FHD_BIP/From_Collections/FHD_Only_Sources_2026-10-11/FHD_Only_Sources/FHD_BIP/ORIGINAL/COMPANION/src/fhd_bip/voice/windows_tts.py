from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import wave
from pathlib import Path


class WindowsTTSError(RuntimeError):
    pass


class WindowsTTS:
    """Small dependency-free TTS fallback for non-Live brain providers.

    It uses Windows' installed System.Speech voice and renders directly to the
    same 24 kHz mono PCM format that AudioIO already plays. This is deliberately
    separate from the LLM provider so cloud/local brains can be swapped without
    changing the audio output layer.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)

    @staticmethod
    def _synthesize_sync(text: str) -> bytes:
        if os.name != "nt":
            raise WindowsTTSError("Windows TTS доступен только в Windows")
        clean = text.strip()
        if not clean:
            return b""
        with tempfile.TemporaryDirectory(prefix="FHD_BIP_TTS_") as tmp:
            root = Path(tmp)
            text_path = root / "text.txt"
            wav_path = root / "voice.wav"
            text_path.write_text(clean, encoding="utf-8")
            tp = str(text_path).replace("'", "''")
            wp = str(wav_path).replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech;"
                f"$t=[IO.File]::ReadAllText('{tp}',[Text.Encoding]::UTF8);"
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                "$fmt=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000,"
                "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
                "[System.Speech.AudioFormat.AudioChannel]::Mono);"
                f"$s.SetOutputToWaveFile('{wp}',$fmt);"
                "$s.Speak($t);$s.Dispose();"
            )
            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=45,
                check=False,
            )
            if proc.returncode != 0 or not wav_path.exists():
                detail = proc.stderr.decode("utf-8", errors="replace")[:300].strip()
                raise WindowsTTSError(detail or "System.Speech не смог синтезировать голос")
            with wave.open(str(wav_path), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 24000:
                    raise WindowsTTSError("Windows TTS вернул неожиданный аудиоформат")
                return wav.readframes(wav.getnframes())

    async def synthesize(self, text: str) -> bytes:
        if not self.enabled:
            return b""
        return await asyncio.to_thread(self._synthesize_sync, text)
