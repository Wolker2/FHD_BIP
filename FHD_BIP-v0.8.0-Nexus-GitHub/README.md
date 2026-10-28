# FHD_BIP 0.8.0 Alpha — review sources

Пакет исходников для GitHub и проверки модераторами, меньше 100 файлов. Удалены готовое окружение Python, кэши и установщики. Все 54 сохранённых исходных файла воспроизводятся с исходными SHA-256; код приложения не изменён. Для Windows EXE добавлена автоматическая сборка в GitHub Actions.

## What this upload contains

This is the standalone **Python BIP companion**, credited to **Prolog_D** in the supplied metadata. It is not a released in-process SRHD bridge. The archive contains 37 application Python modules, a launcher, one test module with 20 tests, batch launch/build scripts, requirements and documentation. The repository preserves the supplied application source rather than replacing it with reconstructed code.

The original archive held **1,434 files**. The cleaned original payload contains **54 files**; **1,380** virtual-environment/cache/installer files were excluded. The removed environment included installed third-party packages, interpreter executables, Windows extensions and compiled bytecode. The two external installers included a macOS Python package and a Windows Python Manager package. Neither is application source or needed for source review.

No game CFG, SCR, native DLL, C/C++ bridge source or RSON project was present. Some supplied documents refer to a DLL bridge source, but that source is absent from this actual upload. The code for local event/command queues and command validation is present. A queued command is not evidence that the game executed it. No placeholder bridge or RSON has been invented.

## Repository layout

- `COMPANION/src/fhd_bip/`: all 37 original application modules, including UI, audio, capture, providers, memory and bridge queues.
- `COMPANION/tests/test_core.py`: the 20 original tests.
- `COMPANION/launcher.py`, batch scripts and requirements: original entry/build inputs.
- `SOURCE/`: original user/developer documentation and event/command contracts; metadata source is `SOURCE/ModuleInfo.txt` in UTF-8.
- `build.py`: packages the cleaned original distribution and optionally a Windows EXE.
- `tools/verify.py`: source fidelity, syntax and supplied test verification.
- `original-file-sha256.json`, `build-verification.json`, `recovery-notes.json`: hashes, actual verification and exclusions.
- `.github/workflows/build.yml`: automatic Windows verification and PyInstaller build.

The original game-facing metadata is rebuilt as CP1251 at the original `ModuleInfo.txt` path. Repeated fields, language declarations and line endings are preserved. All other 53 retained files are copied byte-for-byte. Source/package fidelity does not claim that a new Windows EXE will match any original binary: **no application EXE was supplied outside the removed Python environment**.

## Automatic Windows build

GitHub Actions uses Windows and CPython 3.13, installs the supplied build requirements, runs verification, freezes the original launcher with PyInstaller and packages the result below `Mods/FHD_MODS/FHD_BIP/`. It starts automatically on push, pull request or manual workflow dispatch. Its downloadable artifact contains:

- `FHD_BIP-v0.8.0-Windows-install.zip`, containing the cleaned files plus `COMPANION/FHD_BIP_v0.8.0.exe`.
- `FHD_BIP-v0.8.0-source-install.zip`, containing the 54 original source/document/launcher files without a private interpreter environment.
- A fresh verification report from the Windows runner.

No compilation or hand-recovery on the user's PC is part of this workflow. The provided `constraints-windows.txt` records the dependency versions resolved for CPython 3.13 / Windows amd64 during preparation; the original requirements files are unchanged. Windows wheel resolution was checked against the package index. The Windows GitHub job itself has **not been run in this session**, so a successfully frozen Windows executable is not claimed as a completed local result.

For reviewers, the corresponding commands are:

```sh
python -m pip install -c constraints-windows.txt -r COMPANION/requirements-build.txt
python tools/verify.py --report dist/verification.json
python build.py --verify-original
python -m PyInstaller --noconfirm --clean --onefile --windowed --name FHD_BIP_v0.8.0 --paths COMPANION/src --distpath dist/windows --workpath build/pyinstaller --specpath build COMPANION/launcher.py
python build.py --verify-original --windows-exe dist/windows/FHD_BIP_v0.8.0.exe
```

The original `START_FHD_BIP.bat` already prefers that EXE. Its Python/bootstrap fallback and original batch build script remain included as supplied source. The source ZIP contains no installer, virtual environment, compiled bytecode, frozen EXE, generated install ZIP or git history.

## Verification performed here

`build-verification.json` records **15 preparation checks** and **20 passing supplied tests** on Linux/Python 3.12.14 with the original runtime package pins: websockets 16.0, Pillow 12.0.0 and sounddevice 0.5.2.

Verification reconstructs the cleaned original payload from a fresh temporary source tree and compares all 54 original hashes. All 39 original Python files parse. The supplied tests cover exact/unknown game context, state invalidation, command allowlists, catalog matching, integration requirements, command-result matching, audio volume and thresholds, protocol/message shapes, credential-string redaction, profile settings, mock voice selection, tool calling and provider fallback. One integration test uses an HTTP server bound only to `127.0.0.1`; other provider interactions are mocked. Real provider APIs and user keys were not used. Verification uses a temporary application-data directory.

No interactive Windows UI, microphone, speaker, screen capture, Credential Manager, game process or native bridge was exercised. Live provider/model availability and API compatibility were not validated; supplied model names, endpoints and provider code are preserved. Historical `SOURCE/VALIDATION_RU.txt` and changelog statements are retained as original documentation, while this repository's verification report records only checks actually repeated here.

The original `ModuleInfo.txt` explicitly marks the 11 telemetry functions and four game commands as not implemented in the released game layer. Queue/validator tests do not change that status. Optional FHD_Autopilot/FHD_Trade actions still require compatible game-side consumers that are absent from this upload.

## Provenance

Input: `FHD_BIP.rar`, RAR5, 128,830,723 bytes; SHA-256 `c16cc98422922bb31c30da2f9e3be07dcf28f3a671b332c8e548b211e11f0718`. The supplied archive remains unchanged.

The cleanup changes packaging, adds build/verification tooling and makes metadata readable in UTF-8 source form. It does not modify the supplied application modules, tests or behavior. No specific Nexus moderator checklist was attached and no approval is implied. No original mod license file was supplied; this packaging does not assign a new license to the original application.
