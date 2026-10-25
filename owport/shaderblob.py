"""Unity 2019.4/2020.3 compiled shader blob format.

A Shader object stores, per graphics API ("platform"), one LZ4 block holding
every compiled subprogram of that API:

    u32 count; count x (u32 offset, u32 length, u32 segment)
    entry: i32 version (201806140), i32 gpuProgramType, 4 x u32 stats,
           string[] keywords, string[] localKeywords,
           u8[] programCode (aligned), parameter tail

The parameter tail mirrors SerializedSubProgram's bind info; 2020.3 players
read parameters only from here (the type tree copy is left empty).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import lz4.block

BLOB_VERSION = 201806140


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def align(self) -> None:
        self.p = (self.p + 3) & ~3

    def bytes_(self) -> bytes:
        n = self.u32()
        v = self.d[self.p:self.p + n]
        self.p += n
        self.align()
        return v

    def string(self) -> str:
        return self.bytes_().decode("utf-8")


class Writer:
    def __init__(self):
        self.b = bytearray()

    def u32(self, v: int) -> None:
        self.b += struct.pack("<I", v & 0xFFFFFFFF)

    def align(self) -> None:
        while len(self.b) % 4:
            self.b.append(0)

    def bytes_(self, v: bytes) -> None:
        self.u32(len(v))
        self.b += v
        self.align()

    def string(self, s: str) -> None:
        self.bytes_(s.encode("utf-8"))


@dataclass
class Member:
    name: str
    type: int  # ShaderParamType: 0 float, 1 int, 2 bool, 3 half, 4 short, 5 uint
    rows: int
    cols: int
    is_matrix: bool
    array_size: int
    offset: int


@dataclass
class Struct:
    name: str
    index: int
    array_size: int
    struct_size: int
    members: list[Member] = field(default_factory=list)


@dataclass
class CBuffer:
    name: str
    size: int
    members: list[Member] = field(default_factory=list)
    structs: list[Struct] = field(default_factory=list)


# Binding record kinds in the tail.
BIND_TEXTURE, BIND_CBUFFER = 0, 1


@dataclass
class Binding:
    name: str
    kind: int
    index: int
    extra: list[int] = field(default_factory=list)  # texture: [sampler, dim<<1|ms]; cbuffer: [0]


@dataclass
class Params:
    source_map: int = 0
    channels: list[tuple[int, int]] = field(default_factory=list)
    globals_: CBuffer = field(default_factory=lambda: CBuffer("", 0))
    cbuffers: list[CBuffer] = field(default_factory=list)
    bindings: list[Binding] = field(default_factory=list)
    trailing: bytes = b""  # anything after the bindings we do not model


_BIND_EXTRA = {BIND_TEXTURE: 2, BIND_CBUFFER: 1, 4: 1}  # 4: inline sampler (bindPoint, sampler state)


def _read_member(r: Reader) -> Member:
    name = r.string()
    t, rows, cols, mat, arr, off = (r.i32() for _ in range(6))
    return Member(name, t, rows, cols, bool(mat), arr, off)


def _write_member(w: Writer, m: Member) -> None:
    w.string(m.name)
    for v in (m.type, m.rows, m.cols, int(m.is_matrix), m.array_size, m.offset):
        w.u32(v)


def _read_cb(r: Reader) -> CBuffer:
    cb = CBuffer(r.string(), r.i32())
    cb.members = [_read_member(r) for _ in range(r.u32())]
    for _ in range(r.u32()):
        s = Struct(r.string(), r.i32(), r.i32(), r.i32())
        s.members = [_read_member(r) for _ in range(r.u32())]
        cb.structs.append(s)
    return cb


def _write_cb(w: Writer, cb: CBuffer) -> None:
    w.string(cb.name)
    w.u32(cb.size)
    w.u32(len(cb.members))
    for m in cb.members:
        _write_member(w, m)
    w.u32(len(cb.structs))
    for s in cb.structs:
        w.string(s.name)
        for v in (s.index, s.array_size, s.struct_size, len(s.members)):
            w.u32(v)
        for m in s.members:
            _write_member(w, m)


def read_params(data: bytes) -> Params:
    r = Reader(data)
    p = Params(source_map=r.u32())
    p.channels = [(r.i32(), r.i32()) for _ in range(r.u32())]
    n_cb = r.u32()
    p.globals_ = _read_cb(r)
    p.cbuffers = [_read_cb(r) for _ in range(n_cb - 1)]
    for _ in range(r.u32()):
        name = r.string()
        kind = r.i32()
        if kind not in _BIND_EXTRA:
            raise ValueError(f"unknown binding kind {kind} for {name}")
        index = r.i32()
        p.bindings.append(Binding(name, kind, index, [r.i32() for _ in range(_BIND_EXTRA[kind])]))
    p.trailing = data[r.p:]
    return p


def write_params(p: Params) -> bytes:
    w = Writer()
    w.u32(p.source_map)
    w.u32(len(p.channels))
    for s, t in p.channels:
        w.u32(s)
        w.u32(t)
    w.u32(len(p.cbuffers) + 1)
    _write_cb(w, p.globals_)
    for cb in p.cbuffers:
        _write_cb(w, cb)
    w.u32(len(p.bindings))
    for b in p.bindings:
        w.string(b.name)
        w.u32(b.kind)
        w.u32(b.index)
        for v in b.extra:
            w.u32(v)
    w.b += p.trailing
    return bytes(w.b)


@dataclass
class SubProgramBlob:
    version: int
    program_type: int
    stats: tuple[int, int, int, int]
    keywords: list[str]
    local_keywords: list[str]
    code: bytes
    params_raw: bytes

    @classmethod
    def parse(cls, data: bytes) -> "SubProgramBlob":
        r = Reader(data)
        version, ptype = r.i32(), r.i32()
        stats = tuple(r.u32() for _ in range(4))
        kws = [r.string() for _ in range(r.u32())]
        lkws = [r.string() for _ in range(r.u32())]
        code = r.bytes_()
        return cls(version, ptype, stats, kws, lkws, code, data[r.p:])

    def serialize(self) -> bytes:
        w = Writer()
        w.u32(self.version)
        w.u32(self.program_type)
        for s in self.stats:
            w.u32(s)
        w.u32(len(self.keywords))
        for k in self.keywords:
            w.string(k)
        w.u32(len(self.local_keywords))
        for k in self.local_keywords:
            w.string(k)
        w.bytes_(self.code)
        w.b += self.params_raw
        return bytes(w.b)


def unpack_platform(compressed: bytes, decompressed_len: int) -> list[bytes]:
    b = lz4.block.decompress(compressed, uncompressed_size=decompressed_len)
    n = struct.unpack_from("<I", b, 0)[0]
    out = []
    for k in range(n):
        o, l, _seg = struct.unpack_from("<III", b, 4 + 12 * k)
        out.append(b[o:o + l])
    return out


def pack_platform(entries: list[bytes]) -> tuple[bytes, int]:
    header = 4 + 12 * len(entries)
    w = bytearray(struct.pack("<I", len(entries)))
    off = header
    for e in entries:
        w += struct.pack("<III", off, len(e), 0)
        off += len(e)
    for e in entries:
        w += e
    raw = bytes(w)
    return lz4.block.compress(raw, store_size=False, mode="high_compression"), len(raw)


def platform_entries(shader: dict, platform: int) -> list[bytes]:
    i = shader["platforms"].index(platform)
    off = shader["offsets"][i][0]
    cl = shader["compressedLengths"][i][0]
    dl = shader["decompressedLengths"][i][0]
    return unpack_platform(bytes(shader["compressedBlob"])[off:off + cl], dl)
