"""Compute Metal constant-buffer layouts from HLSLcc's generated MSL structs.

Unity's Metal runtime copies parameter values packed by type (float 4 bytes,
float2 8, float3/float4 16, matrix 64) at the offsets given in the subprogram
metadata, i.e. it expects the MSL struct's own layout. HLSLcc declares
members in RDEF order without padding, so the offsets differ from D3D11's and
are recomputed here with MSL size/alignment rules.
"""

from __future__ import annotations

import re

_SCALAR = {
    "float": 4, "int": 4, "uint": 4, "bool": 1, "half": 2, "short": 2, "ushort": 2,
}

_STRUCT_RE = re.compile(r"struct (\w+)\s*\{(.*?)\};", re.S)
_MEMBER_RE = re.compile(r"^\s*(?:packed_)?(\w+?)(\d?)(?:x(\d))?\s+(\w+)(?:\[(\d+)\])?\s*;", re.M)


def _vec(base: str, n: int) -> tuple[int, int]:
    s = _SCALAR[base]
    if n <= 1:
        return s, s
    if n == 2:
        return 2 * s, 2 * s
    return 4 * s, 4 * s  # 3- and 4-component vectors: size and alignment of 4


class Layout:
    def __init__(self, source: str):
        self.structs: dict[str, list[tuple[str, str, int, int, int]]] = {}
        self.sizes: dict[str, tuple[int, int]] = {}
        raw = {m.group(1): m.group(2) for m in _STRUCT_RE.finditer(source)}
        self._raw = raw
        for name in raw:
            self._layout(name)

    def _layout(self, name: str) -> tuple[int, int]:
        if name in self.sizes:
            return self.sizes[name]
        members = []
        off = 0
        max_align = 4
        for m in _MEMBER_RE.finditer(self._raw[name]):
            base, digits, cols, mname, arr = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            if base in _SCALAR:
                n = int(digits) if digits else 1
                if cols:  # matrix type floatNxM: M columns of N-vectors
                    csize, calign = _vec(base, n)
                    size, align = csize * int(cols), calign
                else:
                    size, align = _vec(base, n)
            elif (base + digits) in self._raw:
                size, align = self._layout(base + digits)
            else:
                continue
            count = int(arr) if arr else 1
            off = (off + align - 1) // align * align
            members.append((mname, base + digits, off, size, count))
            off += size * count
            max_align = max(max_align, align)
        size = (off + max_align - 1) // max_align * max_align
        self.structs[name] = members
        self.sizes[name] = (size, max_align)
        return self.sizes[name]

    def members(self, struct_name: str) -> dict[str, tuple[int, int, int]]:
        """name -> (offset, element size, count)"""
        return {m[0]: (m[2], m[3], m[4]) for m in self.structs.get(struct_name, [])}

    def size(self, struct_name: str) -> int:
        return self.sizes.get(struct_name, (0, 0))[0]
