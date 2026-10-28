from __future__ import annotations

import os
import subprocess
import threading


class WindowsSapiTTS:
    """Small dependency-free TTS output for text-only LLM providers."""

    _SCRIPT = (
        "Add-Type -AssemblyName System.Speech;"
        "$t=[Console]::In.ReadToEnd();"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v=$env:FHD_BIP_TTS_VOICE;"
        "if($v){try{$s.SelectVoice($v)}catch{}};"
        "$s.Volume=[int]$env:FHD_BIP_TTS_VOLUME;"
        "$s.Rate=-1;"
        "$s.Speak($t);"
        "$s.Dispose()"
    )
    _LIST_SCRIPT = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.GetInstalledVoices() | ForEach-Object {$_.VoiceInfo.Name};"
        "$s.Dispose()"
    )

    def __init__(self):
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    @property
    def available(self) -> bool:
        return os.name == "nt"

    @classmethod
    def installed_voices(cls) -> list[str]:
        """Return installed Windows SAPI voice names, without extra packages."""
        if os.name != "nt":
            return []
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cls._LIST_SCRIPT],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        voices = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return sorted(voices, key=str.casefold)

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def speak(self, text: str, volume_percent: int, voice_name: str = "") -> bool:
        if not self.available or not text.strip():
            return False
        self.stop()
        volume = max(0, min(100, int(volume_percent)))

        def worker() -> None:
            env = os.environ.copy()
            env["FHD_BIP_TTS_VOLUME"] = str(volume)
            if voice_name.strip():
                env["FHD_BIP_TTS_VOICE"] = voice_name.strip()
            else:
                env.pop("FHD_BIP_TTS_VOICE", None)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process: subprocess.Popen | None = None
            try:
                process = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", self._SCRIPT],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    env=env,
                    creationflags=creationflags,
                )
                with self._lock:
                    self._process = process
                process.communicate(text)
            except Exception:
                pass
            finally:
                with self._lock:
                    if process is not None and self._process is process:
                        self._process = None

        threading.Thread(target=worker, name="FHD-BIP-SAPI-TTS", daemon=True).start()
        return True

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
