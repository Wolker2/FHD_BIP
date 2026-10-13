from __future__ import annotations

import math
import queue
import copy
import tkinter as tk
from tkinter import messagebox, ttk

from .audio import microphone_threshold_dbfs
from .config import CURRENT_VERSION, ConfigStore, Settings, migrate_legacy_data
from .runtime import BIPRuntime
from .secrets import SecretStoreError, get_api_key, set_api_key
from .tts import WindowsSapiTTS

PERSONALITY_LABELS = {
    "Спокойный напарник": "balanced",
    "Саркастичный": "sarcastic",
    "Весёлый": "cheerful",
    "Военный ИИ": "military",
    "Гаальский учёный": "gaal_scientist",
    "Пеленг-напарник": "peleng_buddy",
}
INITIATIVE_LABELS = {
    "Низкая": "low", "Средняя": "medium", "Высокая": "high", "Болтун": "chatty"
}
MICROPHONE_SENSITIVITY_LABELS = {
    "Низкая — меньше ложных срабатываний": "low",
    "Средняя": "medium",
    "Высокая — для тихого микрофона": "high",
}
PROVIDER_LABELS = {
    "Автоматически": "auto",
    "Gemini Live": "gemini",
    "GPT (OpenAI)": "openai",
    "GigaChat": "gigachat",
    "YandexGPT": "yandex",
    "DeepSeek": "deepseek",
    "Qwen": "qwen",
    "Cloud.ru": "cloudru",
    "Локальная модель — LM Studio": "lmstudio",
    "Пользовательский OpenAI-compatible API": "openai_compatible",
}
PROFILE_PROVIDER_LABELS = {label: value for label, value in PROVIDER_LABELS.items() if value != "auto"}
FALLBACK_LABELS = {"Нет": "none", **PROFILE_PROVIDER_LABELS}
TTS_DEFAULT_LABEL = "По умолчанию"


def _label_for(mapping: dict[str, str], value: str) -> str:
    for label, internal in mapping.items():
        if internal == value:
            return label
    return next(iter(mapping))


class BIPUI:
    def __init__(self):
        migrated = migrate_legacy_data()
        self.root = tk.Tk()
        self.root.title(f"FHD_BIP v{CURRENT_VERSION}")
        self.root.geometry("940x980")
        self.root.minsize(720, 680)
        self.store = ConfigStore()
        self.settings = self.store.load()
        self._key_load_error = ""
        self._profiles = copy.deepcopy(self.settings.provider_profiles)
        self._editing_provider = self.settings.provider if self.settings.provider != "auto" else "gemini"
        self.runtime: BIPRuntime | None = None
        self._last_diagnostics: dict[str, object] = {}
        self._closing = False
        self.events: queue.Queue[tuple] = queue.Queue()
        self._build()
        if self._key_load_error:
            self._append(
                "SYSTEM",
                "Не удалось прочитать ключ из Windows Credential Manager: "
                + self._key_load_error,
            )
        if migrated:
            self._append("SYSTEM", "Перенесены пользовательские настройки из FHD_AICompanion v0.1: " + ", ".join(migrated))
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._drain_events)

    def _build(self):
        pad = {"padx": 10, "pady": 5}

        # Весь интерфейс находится внутри прокручиваемого Canvas.
        # Это позволяет пользоваться всеми настройками даже на небольшом экране.
        scroll_host = ttk.Frame(self.root)
        scroll_host.pack(fill="both", expand=True)

        self._scroll_canvas = tk.Canvas(
            scroll_host,
            highlightthickness=0,
            borderwidth=0,
            background=self.root.cget("background"),
        )
        self._main_scrollbar = ttk.Scrollbar(
            scroll_host, orient="vertical", command=self._scroll_canvas.yview
        )
        self._scroll_canvas.configure(yscrollcommand=self._main_scrollbar.set)
        self._main_scrollbar.pack(side="right", fill="y")
        self._scroll_canvas.pack(side="left", fill="both", expand=True)

        frm = ttk.Frame(self._scroll_canvas)
        self._scroll_frame = frm
        self._scroll_window = self._scroll_canvas.create_window(
            (0, 0), window=frm, anchor="nw"
        )
        frm.configure(padding=(12, 12, 12, 12))
        frm.bind("<Configure>", self._on_scroll_frame_configure)
        self._scroll_canvas.bind("<Configure>", self._on_scroll_canvas_configure)

        # bind_all нужен, потому что курсор почти всегда находится над дочерним виджетом,
        # а не непосредственно над Canvas. Журнал ниже обрабатывается отдельно.
        self.root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_mousewheel_linux, add="+")
        self.root.bind_all("<Button-5>", self._on_mousewheel_linux, add="+")

        ttk.Label(frm, text="FHD_BIP", font=("Segoe UI", 20, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(frm, text="Бортовой Интеллектуальный Помощник для Space Rangers HD", font=("Segoe UI", 10)).grid(row=1, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(frm, text="Самостоятельный режим: другие FHD-моды не требуются.", font=("Segoe UI", 9, "italic")).grid(row=2, column=0, columnspan=3, sticky="w", **pad)

        self.provider_label = tk.StringVar(value=_label_for(PROVIDER_LABELS, self.settings.provider))
        fallbacks = (self.settings.fallback_providers + ["none", "none", "none"])[:3]
        self.fallback_labels = [tk.StringVar(value=_label_for(FALLBACK_LABELS, item)) for item in fallbacks]
        self.profile_provider_label = tk.StringVar(value=_label_for(PROFILE_PROVIDER_LABELS, self._editing_provider))
        active_profile = self._profiles.get(self._editing_provider, {})
        self.model = tk.StringVar(value=str(active_profile.get("model") or ""))
        self.api_endpoint = tk.StringVar(value=str(active_profile.get("endpoint") or ""))
        self.provider_vision = tk.BooleanVar(value=bool(active_profile.get("vision")))
        self.auto_fallback = tk.BooleanVar(value=self.settings.auto_fallback)
        self.request_timeout = tk.DoubleVar(value=self.settings.request_timeout_sec)
        self.speak_text_providers = tk.BooleanVar(value=self.settings.speak_text_providers)
        self.gpt_voice = tk.StringVar(value=self.settings.gpt_voice)
        installed_tts_voices = WindowsSapiTTS.installed_voices()
        self.tts_voice_options = [TTS_DEFAULT_LABEL] + installed_tts_voices
        selected_tts_voice = self.settings.tts_voice if self.settings.tts_voice in installed_tts_voices else TTS_DEFAULT_LABEL
        self.tts_voice = tk.StringVar(value=selected_tts_voice)
        self.voice = tk.StringVar(value=self.settings.voice)
        self.assistant_name = tk.StringVar(value=self.settings.assistant_name)
        self.player_address = tk.StringVar(value=self.settings.player_address)
        self.personality_label = tk.StringVar(value=_label_for(PERSONALITY_LABELS, self.settings.personality))
        self.initiative_label = tk.StringVar(value=_label_for(INITIATIVE_LABELS, self.settings.initiative))
        self.window_title = tk.StringVar(value=self.settings.window_title_contains)
        self.capture_screen = tk.BooleanVar(value=self.settings.capture_screen)
        self.capture_mic = tk.BooleanVar(value=self.settings.capture_microphone)
        self.microphone_sensitivity_label = tk.StringVar(
            value=_label_for(MICROPHONE_SENSITIVITY_LABELS, self.settings.microphone_sensitivity)
        )
        self.microphone_trim = tk.DoubleVar(value=self.settings.microphone_sensitivity_trim_db)
        self.microphone_trim_label = tk.StringVar(value=f"{self.settings.microphone_sensitivity_trim_db:+.1f} dB")
        self.microphone_meter = tk.DoubleVar(value=0.0)
        initial_threshold = microphone_threshold_dbfs(
            self.settings.microphone_sensitivity, self.settings.microphone_sensitivity_trim_db
        )
        self.microphone_meter_label = tk.StringVar(
            value=f"Уровень: — dBFS   Порог: {initial_threshold:.1f} dBFS   Тишина"
        )
        self.only_when_game_active = tk.BooleanVar(value=self.settings.only_when_game_active)
        self.allow_voice_interruption = tk.BooleanVar(value=self.settings.allow_voice_interruption)
        self.output_volume = tk.DoubleVar(value=self.settings.output_volume_percent)
        self.output_volume_label = tk.StringVar(value=f"{self.settings.output_volume_percent}%")
        self.receive_events = tk.BooleanVar(value=self.settings.receive_game_events)
        self.key = tk.StringVar(value="")
        self.key_label = tk.StringVar(value="API key / токен")
        self._load_key_marker(self._editing_provider)

        row = 3
        ttk.Label(frm, text="Основная нейросеть").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.provider_label, state="readonly", values=list(PROVIDER_LABELS), width=38).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Резервы 1 → 2 → 3").grid(row=row, column=0, sticky="w", **pad)
        fallback_frame = ttk.Frame(frm)
        fallback_frame.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        for index, variable in enumerate(self.fallback_labels):
            ttk.Combobox(fallback_frame, textvariable=variable, state="readonly", values=list(FALLBACK_LABELS), width=20).grid(row=0, column=index, padx=(0, 4), sticky="ew")
            fallback_frame.columnconfigure(index, weight=1)
        row += 1
        ttk.Checkbutton(frm, text="Автоматически переключаться при ошибке API", variable=self.auto_fallback).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1
        ttk.Label(frm, text="Настроить профиль").grid(row=row, column=0, sticky="w", **pad)
        profile_combo = ttk.Combobox(frm, textvariable=self.profile_provider_label, state="readonly", values=list(PROFILE_PROVIDER_LABELS))
        profile_combo.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        row += 1
        ttk.Label(frm, text="Модель профиля").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.model).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="API endpoint").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.api_endpoint).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Checkbutton(frm, text="Модель поддерживает изображения экрана", variable=self.provider_vision).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1
        ttk.Label(frm, textvariable=self.key_label).grid(row=row, column=0, sticky="w", **pad)
        self.key_entry = ttk.Entry(frm, textvariable=self.key, show="•")
        self.key_entry.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Сохранить ключ", command=self._save_key).grid(row=row, column=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Таймаут API").grid(row=row, column=0, sticky="w", **pad)
        ttk.Spinbox(frm, from_=5, to=120, textvariable=self.request_timeout, width=10).grid(row=row, column=1, sticky="w", **pad)
        ttk.Label(frm, text="секунд").grid(row=row, column=2, sticky="w", **pad)
        row += 1
        ttk.Checkbutton(frm, text="Озвучивать текстовые AI через голос Windows", variable=self.speak_text_providers).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1
        ttk.Label(frm, text="Имя помощника").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.assistant_name).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Как обращаться к игроку").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.player_address).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Голос Gemini Live").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.voice, values=["Kore", "Puck", "Charon", "Fenrir", "Aoede"]).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Голос GPT (OpenAI)").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(
            frm,
            textvariable=self.gpt_voice,
            state="readonly",
            values=["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar"],
        ).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Голос TTS Windows").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(
            frm,
            textvariable=self.tts_voice,
            state="readonly",
            values=self.tts_voice_options,
        ).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Характер").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.personality_label, state="readonly", values=list(PERSONALITY_LABELS)).grid(row=row, column=1, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Инициативность").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.initiative_label, state="readonly", values=list(INITIATIVE_LABELS)).grid(row=row, column=1, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="Заголовок окна игры содержит").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.window_title).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1

        ttk.Checkbutton(frm, text="Передавать только окно игры (до 1 кадра/с)", variable=self.capture_screen).grid(row=row, column=0, columnspan=3, sticky="w", **pad); row += 1
        ttk.Checkbutton(frm, text="Использовать микрофон", variable=self.capture_mic).grid(row=row, column=0, columnspan=3, sticky="w", **pad); row += 1
        ttk.Label(frm, text="Чувствительность микрофона").grid(row=row, column=0, sticky="w", **pad)
        self.microphone_sensitivity_combo = ttk.Combobox(
            frm,
            textvariable=self.microphone_sensitivity_label,
            state="readonly",
            values=list(MICROPHONE_SENSITIVITY_LABELS),
        )
        self.microphone_sensitivity_combo.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        self.microphone_sensitivity_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_mic_sensitivity_live())
        row += 1
        ttk.Label(frm, text="Точная настройка микрофона").grid(row=row, column=0, sticky="w", **pad)
        ttk.Scale(
            frm,
            from_=-6,
            to=10,
            variable=self.microphone_trim,
            command=self._on_mic_trim_changed,
        ).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.microphone_trim_label, width=8).grid(
            row=row, column=2, sticky="w", **pad
        )
        row += 1
        ttk.Label(frm, text="Индикатор микрофона").grid(row=row, column=0, sticky="w", **pad)
        ttk.Progressbar(
            frm,
            variable=self.microphone_meter,
            maximum=70.0,
            mode="determinate",
        ).grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        ttk.Label(frm, textvariable=self.microphone_meter_label).grid(
            row=row, column=0, columnspan=3, sticky="w", **pad
        )
        row += 1
        ttk.Checkbutton(
            frm,
            text="Работать только когда активно окно игры",
            variable=self.only_when_game_active,
        ).grid(row=row, column=0, columnspan=3, sticky="w", **pad); row += 1
        ttk.Checkbutton(
            frm,
            text="Разрешать голосом прерывать реплику БИПа",
            variable=self.allow_voice_interruption,
        ).grid(row=row, column=0, columnspan=3, sticky="w", **pad); row += 1
        ttk.Label(frm, text="Громкость голоса БИПа").grid(row=row, column=0, sticky="w", **pad)
        ttk.Scale(
            frm,
            from_=0,
            to=100,
            variable=self.output_volume,
            command=self._on_volume_changed,
        ).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.output_volume_label, width=5).grid(
            row=row, column=2, sticky="w", **pad
        )
        row += 1
        ttk.Checkbutton(frm, text="Принимать расширенные события FHD_BIP из игры (необязательно)", variable=self.receive_events).grid(row=row, column=0, columnspan=3, sticky="w", **pad); row += 1

        buttons = ttk.Frame(frm)
        buttons.grid(row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=10); row += 1
        self.start_btn = ttk.Button(buttons, text="ЗАПУСТИТЬ БИП", command=self._start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(buttons, text="ОСТАНОВИТЬ", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(buttons, text="СОХРАНИТЬ НАСТРОЙКИ", command=self._save_settings).pack(side="left", padx=4)
        ttk.Button(buttons, text="ДИАГНОСТИКА", command=self._show_diagnostics).pack(side="left", padx=4)

        ttk.Label(frm, text="Диалог / журнал").grid(row=row, column=0, columnspan=3, sticky="w", **pad); row += 1
        log_row = row
        log_frame = ttk.Frame(frm)
        log_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=10, pady=4)
        self.log = tk.Text(log_frame, height=13, wrap="word", state="disabled", font=("Segoe UI", 10))
        self._log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=self._log_scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        self._log_scrollbar.pack(side="right", fill="y")
        row += 1

        send = ttk.Frame(frm)
        send.grid(row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=6)
        self.text_input = ttk.Entry(send)
        self.text_input.pack(side="left", fill="x", expand=True)
        self.text_input.bind("<Return>", lambda e: self._send())
        ttk.Button(send, text="Отправить", command=self._send).pack(side="left", padx=(6, 0))

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(log_row, weight=1)

    def _on_scroll_frame_configure(self, _event=None) -> None:
        if not hasattr(self, "_scroll_canvas"):
            return
        self._scroll_canvas.configure(scrollregion=self._scroll_canvas.bbox("all"))

    def _on_scroll_canvas_configure(self, event) -> None:
        if not hasattr(self, "_scroll_canvas"):
            return
        # Растягиваем внутренний Frame на всю доступную ширину Canvas.
        self._scroll_canvas.itemconfigure(self._scroll_window, width=event.width)

    @staticmethod
    def _is_descendant(widget, parent) -> bool:
        current = widget
        while current is not None:
            if current is parent:
                return True
            current = getattr(current, "master", None)
        return False

    def _on_mousewheel(self, event):
        # Над журналом оставляем штатную прокрутку самого Text.
        if hasattr(self, "log") and self._is_descendant(event.widget, self.log):
            return None
        delta = getattr(event, "delta", 0)
        if not delta:
            return None
        steps = -max(1, abs(int(delta / 120))) if delta > 0 else max(1, abs(int(delta / 120)))
        self._scroll_canvas.yview_scroll(steps * 3, "units")
        return "break"

    def _on_mousewheel_linux(self, event):
        # Linux/X11 посылает Button-4/5 вместо MouseWheel.
        if hasattr(self, "log") and self._is_descendant(event.widget, self.log):
            return None
        if getattr(event, "num", None) == 4:
            self._scroll_canvas.yview_scroll(-3, "units")
            return "break"
        if getattr(event, "num", None) == 5:
            self._scroll_canvas.yview_scroll(3, "units")
            return "break"
        return None

    def _settings_from_ui(self) -> Settings:
        self._save_profile_draft()
        provider = PROVIDER_LABELS.get(self.provider_label.get(), "gemini")
        gemini_model = str(self._profiles.get("gemini", {}).get("model") or "gemini-3.1-flash-live-preview")
        settings = Settings(
            provider=provider,
            model=gemini_model,
            provider_profiles=copy.deepcopy(self._profiles),
            fallback_providers=[FALLBACK_LABELS.get(item.get(), "none") for item in self.fallback_labels],
            auto_fallback=self.auto_fallback.get(),
            request_timeout_sec=self.request_timeout.get(),
            speak_text_providers=self.speak_text_providers.get(),
            tts_voice="" if self.tts_voice.get() == TTS_DEFAULT_LABEL else self.tts_voice.get().strip(),
            gpt_voice=self.gpt_voice.get().strip() or "coral",
            voice=self.voice.get().strip() or "Kore",
            assistant_name=self.assistant_name.get().strip() or "БИП",
            player_address=self.player_address.get().strip() or "рейнджер",
            personality=PERSONALITY_LABELS.get(self.personality_label.get(), "balanced"),
            initiative=INITIATIVE_LABELS.get(self.initiative_label.get(), "medium"),
            capture_screen=self.capture_screen.get(),
            capture_microphone=self.capture_mic.get(),
            microphone_sensitivity=MICROPHONE_SENSITIVITY_LABELS.get(
                self.microphone_sensitivity_label.get(), "low"
            ),
            microphone_sensitivity_trim_db=round(self.microphone_trim.get(), 1),
            allow_voice_interruption=self.allow_voice_interruption.get(),
            only_when_game_active=self.only_when_game_active.get(),
            output_volume_percent=round(self.output_volume.get()),
            receive_game_events=self.receive_events.get(),
            window_title_contains=self.window_title.get().strip() or "Space Rangers",
            frame_interval_sec=self.settings.frame_interval_sec,
            jpeg_quality=self.settings.jpeg_quality,
            max_frame_width=self.settings.max_frame_width,
            max_frame_height=self.settings.max_frame_height,
            remember_transcripts=self.settings.remember_transcripts,
        )
        return Settings.from_dict(vars(settings))

    def _save_profile_draft(self) -> None:
        if not self._editing_provider:
            return
        self._profiles[self._editing_provider] = {
            "model": self.model.get().strip(),
            "endpoint": self.api_endpoint.get().strip(),
            "vision": self.provider_vision.get(),
        }

    def _on_profile_selected(self, _event=None) -> None:
        self._save_profile_draft()
        self._editing_provider = PROFILE_PROVIDER_LABELS.get(self.profile_provider_label.get(), "gemini")
        profile = self._profiles.get(self._editing_provider, {})
        self.model.set(str(profile.get("model") or ""))
        self.api_endpoint.set(str(profile.get("endpoint") or ""))
        self.provider_vision.set(bool(profile.get("vision")))
        self._load_key_marker(self._editing_provider)

    def _load_key_marker(self, provider: str) -> None:
        suffix = " (не обязателен для LM Studio)" if provider == "lmstudio" else ""
        self.key_label.set("API key / токен" + suffix)
        try:
            self.key.set("••••••••••••" if get_api_key(provider) else "")
        except SecretStoreError as exc:
            self.key.set("")
            self._key_load_error = str(exc)

    def _apply_mic_sensitivity_live(self) -> None:
        mode = MICROPHONE_SENSITIVITY_LABELS.get(
            self.microphone_sensitivity_label.get(), "low"
        )
        trim = self.microphone_trim.get()
        threshold = microphone_threshold_dbfs(mode, trim)
        current = self.microphone_meter_label.get()
        level_text = "—"
        state_text = "Тишина"
        if current.startswith("Уровень:"):
            parts = current.split()
            if len(parts) >= 2:
                level_text = parts[1]
            if current.endswith("ГОЛОС"):
                state_text = "ГОЛОС"
        self.microphone_meter_label.set(
            f"Уровень: {level_text} dBFS   Порог: {threshold:.1f} dBFS   {state_text}"
        )
        if self.runtime:
            self.runtime.set_microphone_sensitivity(mode, trim)

    def _on_mic_trim_changed(self, value: str) -> None:
        trim = max(-6.0, min(10.0, float(value)))
        self.microphone_trim_label.set(f"{trim:+.1f} dB")
        self._apply_mic_sensitivity_live()

    def _on_volume_changed(self, value: str) -> None:
        percent = max(0, min(100, round(float(value))))
        self.output_volume_label.set(f"{percent}%")
        if self.runtime:
            self.runtime.audio.set_output_volume(percent)

    def _save_settings(self):
        self.settings = self._settings_from_ui()
        self.store.save(self.settings)
        self._append("SYSTEM", "Настройки сохранены")

    def _save_key(self):
        value = self.key.get().strip()
        if value.startswith("••"):
            messagebox.showinfo("FHD_BIP", "Ключ этого профиля уже находится в Windows Credential Manager.")
            return
        try:
            if not value and self._editing_provider == "lmstudio":
                messagebox.showinfo("FHD_BIP", "Для локального LM Studio ключ можно не указывать.")
                return
            set_api_key(self._editing_provider, value)
            self.key.set("••••••••••••")
            self._append("SYSTEM", f"Ключ профиля {self._editing_provider} сохранён в Windows Credential Manager")
        except SecretStoreError as exc:
            messagebox.showerror("Ошибка ключа", str(exc))

    def _start(self):
        self._save_settings()
        self.runtime = BIPRuntime(
            self.settings,
            on_status=lambda s: self.events.put(("status", s)),
            on_text=lambda r, t: self.events.put(("text", r, t)),
            on_state=lambda s: self.events.put(("state", s)),
            on_mic_level=lambda level, threshold, active: self.events.put(
                ("mic_level", level, threshold, active)
            ),
        )
        self.runtime.start()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

    def _stop(self):
        if self.runtime:
            self.stop_btn.configure(state="disabled")
            self.runtime.stop()
        else:
            self.start_btn.configure(state="normal")

    def _send(self):
        text = self.text_input.get().strip()
        if text and self.runtime:
            self.runtime.send_text(text)
            self._append("YOU", text)
            self.text_input.delete(0, "end")

    def _show_diagnostics(self):
        data = self.runtime.diagnostics() if self.runtime else (self._last_diagnostics or {
            "version": CURRENT_VERSION,
            "provider": PROVIDER_LABELS.get(self.provider_label.get(), "gemini"),
            "provider_chain": None,
            "model": self.model.get().strip(),
            "api_connection": "not started",
            "stt": "not started",
            "tts": "not started",
            "microphone": "not started",
            "voice_interruption": "enabled" if self.allow_voice_interruption.get() else "disabled",
            "game_active": False,
            "game_data": "waiting",
            "current_system": None,
            "current_planet": None,
            "game_tools": 15,
            "tool_calls": 0,
            "last_tool": None,
            "commands_queued": 0,
            "commands_pending": 0,
            "last_command": None,
            "last_command_id": None,
            "last_command_result": None,
            "last_api_error": None,
            "fallback_provider": None,
            "latency_ms": None,
            "endpoint": None,
            "last_failover_error": None,
        })
        labels = {
            "version": "Версия", "provider": "Провайдер", "model": "Модель",
            "api_connection": "API", "stt": "STT", "tts": "TTS",
            "microphone": "Микрофон", "voice_interruption": "Перебивание",
            "game_active": "Окно игры активно", "game_data": "Данные игры",
            "current_system": "Текущая система", "current_planet": "Текущая планета",
            "game_tools": "Игровых функций", "tool_calls": "Вызовов функций",
            "last_tool": "Последняя функция", "last_api_error": "Последняя ошибка API",
            "commands_queued": "Команд передано", "commands_pending": "Команд ожидает",
            "last_command": "Последняя команда", "last_command_id": "ID последней команды",
            "last_command_result": "Результат команды",
            "fallback_provider": "Резервный провайдер", "latency_ms": "Задержка подключения, мс",
            "provider_chain": "Цепочка AI", "endpoint": "Endpoint",
            "last_failover_error": "Последняя ошибка переключения",
        }
        lines = [f"{labels.get(key, key)}: {value if value is not None else 'нет данных'}" for key, value in data.items()]
        messagebox.showinfo("Диагностика FHD_BIP", "\n".join(lines))

    def _append(self, who: str, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{who}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "status":
                    self._append("SYSTEM", ev[1])
                elif ev[0] == "mic_level":
                    level, threshold, active = ev[1], ev[2], ev[3]
                    if math.isfinite(level):
                        meter = max(0.0, min(70.0, level + 70.0))
                        level_text = f"{level:.1f}"
                    else:
                        meter = 0.0
                        level_text = "—"
                    self.microphone_meter.set(meter)
                    self.microphone_meter_label.set(
                        f"Уровень: {level_text} dBFS   Порог: {threshold:.1f} dBFS   "
                        + ("ГОЛОС" if active else "Тишина")
                    )
                elif ev[0] == "state":
                    state = ev[1]
                    if state == "running":
                        self.start_btn.configure(state="disabled")
                        self.stop_btn.configure(state="normal")
                    elif state in ("stopped", "error"):
                        if self.runtime:
                            self._last_diagnostics = self.runtime.diagnostics()
                        self.runtime = None
                        self.start_btn.configure(state="normal")
                        self.stop_btn.configure(state="disabled")
                        self.microphone_meter.set(0.0)
                        mode = MICROPHONE_SENSITIVITY_LABELS.get(
                            self.microphone_sensitivity_label.get(), "low"
                        )
                        threshold = microphone_threshold_dbfs(mode, self.microphone_trim.get())
                        self.microphone_meter_label.set(
                            f"Уровень: — dBFS   Порог: {threshold:.1f} dBFS   Тишина"
                        )
                else:
                    role = "YOU" if ev[1] == "user" else (self.settings.assistant_name or "БИП")
                    self._append(role, ev[2])
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(100, self._drain_events)

    def _close(self):
        self._closing = True
        self._stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
