"""Rebuild the RDEF (reflection) chunk Unity strips from shipped DXBC.

Unity keeps only ISGN/OSGN/SHDR in player builds and stores bind info in its
own parameter tail. HLSLcc needs RDEF to name constant buffers, their members
and resources, so we synthesize one from the tail and splice it back in.
Layout follows what HLSLcc's reflect.cpp reads.
"""

from __future__ import annotations

import struct

from .shaderblob import BIND_CBUFFER, BIND_TEXTURE, Member, Params

# D3D_SHADER_INPUT_TYPE
SIT_CBUFFER, SIT_TEXTURE, SIT_SAMPLER = 0, 2, 3
# D3D_SHADER_VARIABLE_CLASS / TYPE
SVC_SCALAR, SVC_VECTOR, SVC_MATRIX_COLUMNS, SVC_STRUCT = 0, 1, 3, 5
SVT_VOID, SVT_BOOL, SVT_INT, SVT_FLOAT, SVT_UINT = 0, 1, 2, 3, 19
# Unity ShaderParamType -> D3D variable type
_UNITY_TYPE = {0: SVT_FLOAT, 1: SVT_INT, 2: SVT_BOOL, 3: SVT_FLOAT, 4: SVT_INT, 5: SVT_UINT}
# Unity TextureDimension -> D3D_SRV_DIMENSION
_SRV_DIM = {2: 4, 3: 8, 4: 9, 5: 5, 6: 10}
RETURN_FLOAT = 5

_FILTERS = {0: "point", 1: "linear", 2: "trilinear"}
_WRAPS = {0: "repeat", 1: "clamp", 2: "mirror", 3: "mirroronce"}


def inline_sampler_name(state: int) -> str:
    """Name HLSLcc's Metal backend turns into the same constexpr sampler."""
    flt = _FILTERS.get(state & 3, "linear")
    wu, wv, ww = (state >> 2) & 3, (state >> 4) & 3, (state >> 6) & 3
    if wu == wv == ww:
        wrap = _WRAPS[wu]
    else:
        wrap = f"{_WRAPS[wu]}u_{_WRAPS[wv]}v_{_WRAPS[ww]}w"
    compare = "_compare" if state & 0x100 else ""
    return f"inlinesampler_{flt}_{wrap}{compare}"


def _hlsl_size(m: Member) -> int:
    if m.is_matrix:
        one = (m.cols - 1) * 16 + m.rows * 4  # column_major: cols registers of rows floats
        stride = m.cols * 16
    else:
        one = m.cols * 4
        stride = 16
    n = max(m.array_size, 1)
    return (n - 1) * stride + one


class _Chunk:
    def __init__(self):
        self.b = bytearray()
        self.strings: dict[str, int] = {}

    def here(self) -> int:
        return len(self.b)

    def u32(self, *vals: int) -> None:
        for v in vals:
            self.b += struct.pack("<I", v & 0xFFFFFFFF)

    def patch(self, pos: int, val: int) -> None:
        struct.pack_into("<I", self.b, pos, val & 0xFFFFFFFF)


def _type_of(m: Member) -> tuple:
    vtype = _UNITY_TYPE.get(m.type, SVT_FLOAT)
    if m.is_matrix:
        return (SVC_MATRIX_COLUMNS, vtype, m.rows, m.cols, m.array_size, ())
    cls = SVC_SCALAR if m.cols == 1 else SVC_VECTOR
    return (cls, vtype, 1, m.cols, m.array_size, ())


OPCODE_DCL_RESOURCE = 88


def resource_return_types(dxbc: bytes) -> dict[int, int]:
    """t# -> D3D resource return type (x component) from SHDR dcl_resource tokens."""
    for fourcc, off, size in chunks(dxbc):
        if fourcc not in (b"SHDR", b"SHEX"):
            continue
        toks = struct.unpack_from(f"<{size // 4}I", dxbc, off)
        out = {}
        i = 2  # version, length
        while i < len(toks):
            op = toks[i] & 0x7FF
            n = toks[i + 1] if op == 0x35 else (toks[i] >> 24) & 0x7F  # customdata carries its length
            if n == 0:
                break
            if op == OPCODE_DCL_RESOURCE and n >= 4:
                out[toks[i + 2]] = toks[i + 3] & 0xF  # operand index, then return type token
            i += n
        return out
    return {}


def build_rdef(params: Params, shader_model: int, return_types: dict[int, int] | None = None) -> bytes:
    """shader_model: SHDR version token (program type << 16 | major << 4 | minor)."""
    return_types = return_types or {}
    major = (shader_model >> 4) & 0xF

    # Resources: names per bind point.
    textures = [b for b in params.bindings if b.kind == BIND_TEXTURE]
    samplers: dict[int, str] = {}
    for b in params.bindings:
        if b.kind == 4:  # inline sampler: index = bind point, extra[0] = state
            samplers.setdefault(b.index, inline_sampler_name(b.extra[0]))
    for t in textures:
        s = t.extra[0]
        if s >= 0 and s not in samplers:
            samplers[s] = "sampler" + t.name
    cb_bind = {b.name: b.index for b in params.bindings if b.kind == BIND_CBUFFER}

    c = _Chunk()
    c.u32(0, 0, 0, 0, shader_model, 0)  # header, patched below

    # Plan all records first; strings and types go after them.
    resources = []  # (name, type, ret, dim, samples, bindpoint)
    for cb in params.cbuffers:
        resources.append((cb.name, SIT_CBUFFER, 0, 0, 0, cb_bind.get(cb.name, 0)))
    for t in textures:
        dimflag = t.extra[1]
        dim = dimflag >> 1
        ms = dimflag & 1
        srv = _SRV_DIM.get(dim, 4)
        if ms:
            srv = 6 if srv == 4 else 7
        ret = return_types.get(t.index, RETURN_FLOAT) or RETURN_FLOAT
        resources.append((t.name, SIT_TEXTURE, ret, srv, 0xFFFFFFFF, t.index))
    for slot, name in sorted(samplers.items()):
        resources.append((name, SIT_SAMPLER, 0, 0, 0, slot))

    bind_off = c.here()
    res_pos = []
    for _ in resources:
        res_pos.append(c.here())
        c.u32(0, 0, 0, 0, 0, 0, 1, 0)

    cb_off = c.here()
    cb_pos = []
    for _ in params.cbuffers:
        cb_pos.append(c.here())
        c.u32(0, 0, 0, 0, 0, 0)

    var_words = 10 if major >= 5 else 6
    var_tables = []
    for cb in params.cbuffers:
        start = c.here()
        rows = []
        for m in cb.members:
            rows.append(("member", m, c.here()))
            c.u32(*([0] * var_words))
        for s in cb.structs:
            rows.append(("struct", s, c.here()))
            c.u32(*([0] * var_words))
        var_tables.append((start, rows))

    def string(s: str) -> int:
        if s not in c.strings:
            c.strings[s] = c.here()
            c.b += s.encode() + b"\0"
            while len(c.b) % 4:
                c.b.append(0)
        return c.strings[s]

    def type_desc(cls, vtype, rows, cols, elements, members) -> int:
        # members: list of (name, type tuple, offset)
        member_entries = []
        for name, ttuple, off in members:
            member_entries.append((string(name), type_desc(*ttuple), off))
        mem_off = 0
        if member_entries:
            mem_off = c.here()
            for n, t, o in member_entries:
                c.u32(n, t, o)
        pos = c.here()
        c.b += struct.pack("<6H", cls, vtype, rows, cols, elements, len(members))
        c.u32(mem_off)
        return pos

    # Fill resources
    for pos, (name, typ, ret, dim, samples, bp) in zip(res_pos, resources):
        n = string(name)
        struct.pack_into("<8I", c.b, pos, n, typ, ret, dim, samples & 0xFFFFFFFF, bp, 1, 0)

    # Fill constant buffers and variables
    for pos, cb, (vstart, rows) in zip(cb_pos, params.cbuffers, var_tables):
        struct.pack_into("<6I", c.b, pos, string(cb.name), len(rows), vstart, cb.size, 0, 0)
        for kind, item, vpos in rows:
            if kind == "member":
                m = item
                t = type_desc(*_type_of(m))
                vals = [string(m.name), m.offset, _hlsl_size(m), 2, t, 0]
            else:
                s = item
                mems = [(m.name, _type_of(m), m.offset) for m in s.members]
                t = type_desc(SVC_STRUCT, SVT_VOID, 1, s.struct_size // 4, s.array_size, mems)
                vals = [string(s.name), s.index, s.struct_size * max(s.array_size, 1), 2, t, 0]
            if major >= 5:
                vals += [0xFFFFFFFF, 0, 0xFFFFFFFF, 0]
            struct.pack_into(f"<{len(vals)}I", c.b, vpos, *vals)

    struct.pack_into("<4I", c.b, 0, len(params.cbuffers), cb_off, len(resources), bind_off)
    return bytes(c.b)


def shader_version(dxbc: bytes) -> int:
    for fourcc, off, size in chunks(dxbc):
        if fourcc in (b"SHDR", b"SHEX"):
            return struct.unpack_from("<I", dxbc, off)[0]
    raise ValueError("no SHDR/SHEX chunk")


def chunks(dxbc: bytes):
    n = struct.unpack_from("<I", dxbc, 28)[0]
    for i in range(n):
        o = struct.unpack_from("<I", dxbc, 32 + 4 * i)[0]
        yield dxbc[o:o + 4], o + 8, struct.unpack_from("<I", dxbc, o + 4)[0]


def with_rdef(dxbc: bytes, rdef: bytes) -> bytes:
    parts = [(b"RDEF", rdef)]
    for fourcc, off, size in chunks(dxbc):
        if fourcc != b"RDEF":
            parts.append((fourcc, dxbc[off:off + size]))
    header_len = 32 + 4 * len(parts)
    offsets = []
    body = bytearray()
    for fourcc, data in parts:
        offsets.append(header_len + len(body))
        body += fourcc + struct.pack("<I", len(data)) + data
    total = header_len + len(body)
    head = bytearray(dxbc[:32])
    struct.pack_into("<I", head, 24, total)
    struct.pack_into("<I", head, 28, len(parts))
    head += b"".join(struct.pack("<I", o) for o in offsets)
    return bytes(head + body)
