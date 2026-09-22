"""Protect optional cookie credentials using the signed-in Windows user's DPAPI."""

import base64
import ctypes
import os
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def transform(data, decrypt=False):
    if os.name != "nt":
        raise ValueError(
            "Cookie credentials require Windows DPAPI; QR login can be used instead"
        )
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = Blob()
    operation = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not operation(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)
    ):
        raise ValueError(
            "Could not access cookie credentials for this Windows user; log in again"
        )
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.data)


def seal(text):
    return "dpapi:" + base64.b64encode(transform(text.encode("utf-8"))).decode("ascii")


def unseal(text):
    return transform(base64.b64decode(text.removeprefix("dpapi:")), True).decode(
        "utf-8"
    )
