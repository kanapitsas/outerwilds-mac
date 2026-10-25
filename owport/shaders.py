"""Turn a 2019.4 D3D11-only Shader object into a 2020.3 Metal Shader object.

For every D3D11 vertex/pixel subprogram: rebuild the stripped RDEF from
Unity's bind info, translate the DXBC to MSL with HLSLcc (the translator
Unity itself used for Metal), recompute constant buffer offsets for Metal's
layout, and emit a Metal subprogram with the matching parameter tail.
"""

from __future__ import annotations

import collections
import re
import struct
from dataclasses import dataclass, field

from . import hlslcc, rdef, upgrade
from . import shaderblob as sb
from .mslayout import Layout

DX11_VS = {15, 16}
DX11_PS = {17, 18}
DX11_OTHER = {19, 20, 21, 22}  # geometry, hull, domain: no Metal equivalent here
METAL_VS, METAL_FS = 23, 24
PLATFORM_D3D11, PLATFORM_METAL = 4, 14

STAGES = ("progVertex", "progFragment", "progGeometry", "progHull", "progDomain")

# D3D11 vertex component (channel target) -> HLSL input semantic
_TARGET_SEMANTIC = {0: "POSITION0", 1: "NORMAL0", 2: "TANGENT0", 3: "COLOR0"}
_TARGET_SEMANTIC.update({5 + i: f"TEXCOORD{i}" for i in range(8)})
METAL_ATTRIB0 = 13

# HLSLCC_TEX_DIMENSION (TD_FLOAT, TD_INT, TD_2D, TD_3D, TD_CUBE, TD_2DSHADOW,
# TD_2DARRAY, TD_CUBEARRAY) -> Unity TextureDimension (2D 2, 3D 3, Cube 4,
# 2DArray 5, CubeArray 6). Only used when the D3D11 data has no binding for a texture.
_HLSLCC_DIM = {0: 2, 1: 2, 2: 2, 3: 3, 4: 4, 5: 2, 6: 5, 7: 6}

_METAL_HEADER = struct.pack("<11I", 0xF00DCAFE, 44, 4, 56, 0, 0, 0, 0, 0, 5, 15) + b"xlatMtlMain\0"


@dataclass
class Stats:
    translated: int = 0
    failed: int = 0
    dropped_stage: int = 0
    errors: collections.Counter = field(default_factory=collections.Counter)


def _dx_cb_for(metal_name: str, dx_cbs: dict[str, sb.CBuffer]) -> sb.CBuffer | None:
    if metal_name in dx_cbs:
        return dx_cbs[metal_name]
    if metal_name.endswith("Globals") and "$Globals" in dx_cbs:
        return dx_cbs["$Globals"]
    return None


def metal_params(dx: sb.Params, refl: dict, msl: str) -> sb.Params:
    layout = Layout(msl)
    dx_cbs = {cb.name: cb for cb in dx.cbuffers}
    out = sb.Params(source_map=dx.source_map)

    # Vertex inputs: DX channel target -> semantic -> Metal attribute index.
    locs = {i["name"]: i["loc"] for i in refl.get("inputs", [])}
    chans = []
    for source, target in dx.channels:
        loc = locs.get(_TARGET_SEMANTIC.get(target, ""))
        if loc is not None:
            chans.append((loc, source))
    out.channels = [(source, METAL_ATTRIB0 + loc) for loc, source in sorted(chans)]

    cb_slots = [(b["name"], b["slot"]) for b in refl.get("cbBindings", [])]
    for name, _slot in cb_slots:
        dxcb = _dx_cb_for(name, dx_cbs)
        if dxcb is None:
            raise ValueError(f"no D3D11 cbuffer for {name}")
        tname = name + "_Type"
        if tname in layout.structs:
            mems = layout.members(tname)
            size = layout.size(tname)
            cb = sb.CBuffer(name, (size + 15) // 16 * 16)
            for m in dxcb.members:
                key = m.name if m.name in mems else f"hlslcc_mtx{m.rows}x{m.cols}{m.name}"
                if key not in mems:
                    continue
                cb.members.append(sb.Member(m.name, m.type, m.rows, m.cols, m.is_matrix, m.array_size, mems[key][0]))
            for s in dxcb.structs:
                if s.name not in mems:
                    continue
                cb.structs.append(_metal_struct(s, mems[s.name][0], layout))
        else:
            # Flexible instancing buffer: `const constant Elem_Type* Name`, no wrapper struct.
            if len(dxcb.structs) != 1:
                raise ValueError(f"no MSL struct for cbuffer {name}")
            s = _metal_struct(dxcb.structs[0], 0, layout)
            cb = sb.CBuffer(name, s.struct_size * max(s.array_size, 1), structs=[s])
        out.cbuffers.append(cb)

    dx_tex = {b.name: b for b in dx.bindings if b.kind == sb.BIND_TEXTURE}
    for t in refl.get("textures", []):
        if t.get("uav"):
            raise ValueError(f"UAV texture {t['name']} not supported")
        src = dx_tex.get(t["name"])
        dimflag = src.extra[1] if src else (_HLSLCC_DIM.get(t["dim"], 2) << 1) | int(t["ms"])
        sampler = t["sampler"] if t["sampler"] >= 0 else -1
        out.bindings.append(sb.Binding(t["name"], sb.BIND_TEXTURE, t["slot"], [sampler, dimflag]))
    for name, slot in cb_slots:
        out.bindings.append(sb.Binding(name, sb.BIND_CBUFFER, slot, [0]))
    if refl.get("buffers"):
        raise ValueError("buffer bindings not supported yet")
    return out


def canonical_cbuffers(msl: str, dx: sb.Params, refl: dict) -> str:
    """Give each named cbuffer (UnityPerCamera, UnityPerDraw, Props...) its full D3D11
    layout in the MSL, padding the members HLSLcc dropped.

    Unity's Metal runtime identifies such a buffer by name, so the vertex and fragment
    stages share it. HLSLcc only keeps the members each stage reads, which gives two
    different layouts under one name: one stage then reads the other's values.
    $Globals is renamed per stage by HLSLcc and stays packed; instancing buffers
    (arrays of structs) are left alone, and so is a buffer whose D3D11 layout cannot
    be expressed with MSL alignment (a float3 at an offset that is not a multiple of
    16, as in UnityTerrain): it keeps the packed layout it had before.
    """
    layout = Layout(msl)
    dx_cbs = {cb.name: cb for cb in dx.cbuffers}
    for b in refl.get("cbBindings", []):
        name = b["name"]
        dxcb = dx_cbs.get(name)
        tname = name + "_Type"
        if dxcb is None or name.endswith("Globals") or dxcb.structs or tname not in layout.structs:
            continue
        m = re.search(r"struct %s\s*\{(.*?)\};" % re.escape(tname), msl, re.S)
        decls = {}
        for line in m.group(1).splitlines():
            mm = re.match(r"\s*(?:packed_)?\w+\s+(\w+)(?:\[\d+\])?\s*;", line)
            if mm:
                decls[mm.group(1)] = line.strip()
        sizes = layout.members(tname)
        items = []
        for dm in dxcb.members:
            key = dm.name if dm.name in decls else f"hlslcc_mtx{dm.rows}x{dm.cols}{dm.name}"
            if key in decls:
                items.append((dm.offset, key, decls.pop(key)))
        if decls:
            raise ValueError(f"{name}: MSL members without a D3D11 offset: {sorted(decls)}")
        items.sort()
        padded = _pad_struct(items, sizes, (dxcb.size + 15) // 16 * 16)
        if padded is not None:
            msl = msl[:m.start(1)] + "\n" + "\n".join(padded) + "\n" + msl[m.end(1):]
    return msl


def _pad_struct(items: list[tuple[int, str, str]], sizes: dict, total: int) -> list[str] | None:
    """Member declarations with padding so each lands at its D3D11 offset, or None
    if one would overlap the previous member under MSL alignment."""
    out: list[str] = []
    cur = 0

    def pad(to: int) -> None:
        nonlocal cur
        while cur < to and cur % 16:
            out.append(f"    float _owpad{len(out)};")
            cur += 4
        if (to - cur) // 16:
            out.append(f"    float4 _owpad{len(out)}[{(to - cur) // 16}];")
            cur += (to - cur) // 16 * 16
        while cur < to:
            out.append(f"    float _owpad{len(out)};")
            cur += 4

    for off, key, decl in items:
        _, elem, count = sizes[key]
        if off < cur or off % min(elem, 16):
            return None
        pad(off)
        out.append("    " + decl)
        cur = off + elem * count
    pad(total)
    return out


def _metal_struct(s: sb.Struct, offset: int, layout: Layout) -> sb.Struct:
    tname = s.name + "_Type"
    mems = layout.members(tname)
    out = sb.Struct(s.name, offset, s.array_size, layout.size(tname))
    for m in s.members:
        key = m.name if m.name in mems else f"hlslcc_mtx{m.rows}x{m.cols}{m.name}"
        if key in mems:
            out.members.append(sb.Member(m.name, m.type, m.rows, m.cols, m.is_matrix, m.array_size, mems[key][0]))
    return out


def translate_entry(raw: bytes) -> tuple[bytes, str, list[tuple[int, int]]]:
    """D3D11 subprogram blob entry -> Metal blob entry, its MSL, and its vertex channels."""
    blob = sb.SubProgramBlob.parse(raw)
    i = blob.code.find(b"DXBC")
    if i < 0:
        raise ValueError("no DXBC in entry")
    dxbc = blob.code[i:]
    dxp = sb.read_params(blob.params_raw)
    rdef_chunk = rdef.build_rdef(dxp, rdef.shader_version(dxbc), rdef.resource_return_types(dxbc))
    full = rdef.with_rdef(dxbc, rdef_chunk)
    msl, refl = hlslcc.to_metal(full)
    if not refl["ok"] or not msl:
        msgs = "; ".join(d["msg"] for d in refl.get("diagnostics", []))
        raise ValueError(f"HLSLcc failed: {msgs}")
    msl = canonical_cbuffers(msl, dxp, refl)
    mp = metal_params(dxp, refl, msl)
    ptype = METAL_VS if blob.program_type in DX11_VS else METAL_FS
    out = sb.SubProgramBlob(sb.BLOB_VERSION, ptype, blob.stats, blob.keywords, blob.local_keywords,
                            _METAL_HEADER + msl.encode("utf-8"), sb.write_params(mp))
    return out.serialize(), msl, mp.channels


def _new_subprogram(node, sp: dict, blob_index: int, ptype: int, channels: list[tuple[int, int]]) -> dict:
    out = upgrade.default_value(node)
    out["m_BlobIndex"] = blob_index
    # Same Metal attribute mapping as the blob tail (targets 13+), not the D3D11 one.
    out["m_Channels"] = {"m_Channels": [{"source": s, "target": t} for s, t in channels],
                         "m_SourceMap": sp["m_Channels"]["m_SourceMap"]}
    out["m_GlobalKeywordIndices"] = sp["m_GlobalKeywordIndices"]
    out["m_LocalKeywordIndices"] = sp["m_LocalKeywordIndices"]
    out["m_ShaderHardwareTier"] = sp["m_ShaderHardwareTier"]
    out["m_GpuProgramType"] = ptype
    out["m_ShaderRequirements"] = sp["m_ShaderRequirements"]
    return out


def _find(node, *path):
    for name in path:
        node = next(c for c in node.m_Children if c.m_Name == name)
    return node


def convert_shader(old: dict, stats: Stats, msl_sink=None) -> dict:
    new_root = upgrade.tree(48, upgrade.NEW_VERSION)
    new = upgrade.upgrade_object(48, old)
    sub_node = _find(new_root, "m_ParsedForm", "m_SubShaders", "Array", "data", "m_Passes", "Array", "data",
                     "progVertex", "m_SubPrograms", "Array", "data")
    name = old["m_ParsedForm"]["m_Name"]

    entries = sb.platform_entries(old, PLATFORM_D3D11) if PLATFORM_D3D11 in old["platforms"] else []
    metal_entries: list[bytes] = []
    blob_map: dict[int, int] = {}
    blob_channels: dict[int, list[tuple[int, int]]] = {}

    for si, ss in enumerate(old["m_ParsedForm"]["m_SubShaders"]):
        for pi, ps in enumerate(ss["m_Passes"]):
            npass = new["m_ParsedForm"]["m_SubShaders"][si]["m_Passes"][pi]
            gkw, lkw = set(), set()
            for stage in STAGES:
                subs = []
                for sp in ps[stage]["m_SubPrograms"]:
                    t = sp["m_GpuProgramType"]
                    if t in DX11_OTHER:
                        stats.dropped_stage += 1
                        continue
                    if t not in DX11_VS and t not in DX11_PS:
                        continue
                    bi = sp["m_BlobIndex"]
                    if bi not in blob_map:
                        try:
                            metal, msl, chans = translate_entry(entries[bi])
                        except Exception as e:  # keep going; a missing variant falls back at runtime
                            stats.failed += 1
                            stats.errors[f"{name}: {str(e)[:160]}"] += 1
                            blob_map[bi] = -1
                            continue
                        blob_map[bi] = len(metal_entries)
                        blob_channels[bi] = chans
                        metal_entries.append(metal)
                        stats.translated += 1
                        if msl_sink is not None:
                            msl_sink(name, len(metal_entries) - 1, msl)
                    if blob_map[bi] < 0:
                        continue
                    ptype = METAL_VS if t in DX11_VS else METAL_FS
                    subs.append(_new_subprogram(sub_node, sp, blob_map[bi], ptype, blob_channels[bi]))
                    gkw.update(sp["m_GlobalKeywordIndices"])
                    lkw.update(sp["m_LocalKeywordIndices"])
                npass[stage]["m_SubPrograms"] = subs
                for k in npass[stage]["m_CommonParameters"]:
                    npass[stage]["m_CommonParameters"][k] = []
            npass["m_Platforms"] = [PLATFORM_METAL]
            npass["m_EditorDataHash"] = []
            npass["m_GlobalKeywordMask"] = sorted(gkw)
            npass["m_LocalKeywordMask"] = sorted(lkw)
            npass["m_State"]["conservative"] = {"val": 0.0, "name": "<noninit>"}

    if metal_entries:
        comp, dlen = sb.pack_platform(metal_entries)
        new["platforms"] = [PLATFORM_METAL]
        new["offsets"] = [[0]]
        new["compressedLengths"] = [[len(comp)]]
        new["decompressedLengths"] = [[dlen]]
        new["compressedBlob"] = comp
    else:
        new["platforms"] = []
        new["offsets"] = []
        new["compressedLengths"] = []
        new["decompressedLengths"] = []
        new["compressedBlob"] = b""
    return new
