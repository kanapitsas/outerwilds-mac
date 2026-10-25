"""ctypes wrapper around build/libhlslcc_bridge.dylib (Unity HLSLcc, DXBC -> MSL)."""

from __future__ import annotations

import ctypes
import json
import os

_LIB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "libhlslcc_bridge.dylib")

# Flags from include/hlslcc.h
FLAG_TRANSLATE_MATRICES = 0x20000
FLAG_METAL_SHADOW_SAMPLER_LINEAR = 0x80000

DEFAULT_FLAGS = FLAG_TRANSLATE_MATRICES | FLAG_METAL_SHADOW_SAMPLER_LINEAR

_lib = None


def _load():
    global _lib
    if _lib is None:
        _lib = ctypes.CDLL(_LIB_PATH)
        _lib.hlslcc_to_metal.argtypes = [ctypes.c_char_p, ctypes.c_uint,
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        _lib.hlslcc_to_metal.restype = ctypes.c_int
        _lib.hlslcc_free.argtypes = [ctypes.c_void_p]
    return _lib


def to_metal(dxbc: bytes, flags: int = DEFAULT_FLAGS) -> tuple[str, dict]:
    lib = _load()
    src = ctypes.c_void_p()
    js = ctypes.c_void_p()
    lib.hlslcc_to_metal(dxbc, flags, ctypes.byref(src), ctypes.byref(js))
    try:
        source = ctypes.string_at(src).decode("utf-8")
        refl = json.loads(ctypes.string_at(js).decode("utf-8"))
    finally:
        lib.hlslcc_free(src)
        lib.hlslcc_free(js)
    return source, refl
