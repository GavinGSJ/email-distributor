"""Encrypt the saved SMTP password. On Windows this uses DPAPI, so the stored value
can only be decrypted by the same Windows user on the same machine."""

import base64
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _call(func, data):
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("DPAPI 调用失败")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(blob_out.pbData)

    def encrypt(text):
        return "dpapi:" + base64.b64encode(_call(_crypt32.CryptProtectData, text.encode())).decode()

    def _decrypt_dpapi(raw):
        return _call(_crypt32.CryptUnprotectData, raw).decode()

else:

    def encrypt(text):  # no OS keystore available without extra dependencies
        return "plain:" + base64.b64encode(text.encode()).decode()

    def _decrypt_dpapi(raw):
        raise OSError("该密码是在 Windows 上加密保存的，无法在本系统解密，请重新输入")


def decrypt(stored):
    if not stored:
        return ""
    kind, _, payload = stored.partition(":")
    raw = base64.b64decode(payload)
    if kind == "dpapi":
        return _decrypt_dpapi(raw)
    return raw.decode()
