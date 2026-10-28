from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

AudioCallback = Callable[[bytes], None]
TextCallback = Callable[[str, str], None]
StatusCallback = Callable[[str], None]
VoidCallback = Callable[[], None]


@dataclass
class ProviderCallbacks:
    on_audio: AudioCallback
    on_text: TextCallback
    on_status: StatusCallback
    on_interrupted: VoidCallback = lambda: None


class LiveProvider(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def send_audio(self, pcm16: bytes) -> None: ...

    async def end_audio_stream(self) -> None: ...

    @abstractmethod
    async def send_frame(self, jpeg: bytes) -> None: ...

    @abstractmethod
    async def send_text(self, text: str) -> None: ...
