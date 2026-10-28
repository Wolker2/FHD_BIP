from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

TARGET_PREFIX = "FHD_BIP"
LEGACY_TARGET_PREFIX = "FHD_AICompanion"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class SecretStoreError(RuntimeError):
    pass


if os.name == "nt":
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]

    PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL


def _target(provider: str, prefix: str = TARGET_PREFIX) -> str:
    return f"{prefix}:{provider}"


def _read_target(target: str) -> str | None:
    if os.name != "nt":
        return None
    pcred = PCREDENTIALW()
    if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None
        raise SecretStoreError(f"CredReadW failed: {err}")
    try:
        cred = pcred.contents
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return raw.decode("utf-16le")
    finally:
        advapi32.CredFree(pcred)


def set_api_key(provider: str, api_key: str) -> None:
    if not api_key.strip():
        raise SecretStoreError("API key is empty")
    if os.name != "nt":
        raise SecretStoreError("Windows Credential Manager is only available on Windows")

    blob = api_key.strip().encode("utf-16le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIALW()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = _target(provider)
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = "FHD_BIP"
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise SecretStoreError(f"CredWriteW failed: {ctypes.get_last_error()}")


def get_api_key(provider: str) -> str | None:
    env_names = [f"FHD_BIP_{provider.upper()}_KEY", f"FHD_AICOMPANION_{provider.upper()}_KEY"]
    for name in env_names:
        if os.environ.get(name):
            return os.environ[name]
    if os.name != "nt":
        return None

    value = _read_target(_target(provider))
    if value:
        return value
    # Seamless migration from the old project name. The secret is copied only
    # after it was successfully read from the user's own Windows vault.
    legacy = _read_target(_target(provider, LEGACY_TARGET_PREFIX))
    if legacy:
        try:
            set_api_key(provider, legacy)
        except SecretStoreError:
            pass
        return legacy
    return None


def delete_api_key(provider: str) -> None:
    if os.name != "nt":
        return
    if not advapi32.CredDeleteW(_target(provider), CRED_TYPE_GENERIC, 0):
        err = ctypes.get_last_error()
        if err != ERROR_NOT_FOUND:
            raise SecretStoreError(f"CredDeleteW failed: {err}")
