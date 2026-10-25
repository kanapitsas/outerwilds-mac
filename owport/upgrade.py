"""Upgrade Unity 2019.4 player data to the 2020.3 serialized layout.

Player builds carry no type trees, so a 2020.3 player reads every object with
its own native layout and refuses files stamped with another version. Most
classes Outer Wilds uses did not change between the two versions; their bytes
are kept as is. For the classes that did change, each object is read with the
2019.4 type tree (from UnityPy's TPK database), mapped field by field onto the
2020.3 tree, and written back.
"""

from __future__ import annotations

from typing import Any, Callable

import UnityPy.helpers.Tpk as Tpk
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

OLD_VERSION = (2019, 4, 39, 0)
NEW_VERSION = (2020, 3, 48, 0)
NEW_VERSION_STRING = "2020.3.48f1"

_INT_TYPES = {
    "SInt8", "UInt8", "char", "short", "SInt16", "unsigned short", "UInt16",
    "int", "SInt32", "unsigned int", "UInt32", "Type*", "long long", "SInt64",
    "unsigned long long", "UInt64", "FileSize",
}

_node_cache: dict[tuple[int, tuple], TypeTreeNode] = {}


def tree(class_id: int, version: tuple) -> TypeTreeNode:
    key = (class_id, version)
    if key not in _node_cache:
        _node_cache[key] = Tpk.get_typetree_node(class_id, version)
    return _node_cache[key]


def _signature(node: TypeTreeNode) -> tuple:
    return (node.m_Name, node.m_Type, node.m_MetaFlag,
            tuple(_signature(c) for c in node.m_Children or []))


def layout_changed(class_id: int) -> bool:
    return _signature(tree(class_id, OLD_VERSION)) != _signature(tree(class_id, NEW_VERSION))


def is_vector(node: TypeTreeNode) -> bool:
    return bool(node.m_Children) and node.m_Children[0].m_Type == "Array"


def default_value(node: TypeTreeNode) -> Any:
    t = node.m_Type
    if t in _INT_TYPES:
        return 0
    if t in ("float", "double"):
        return 0.0
    if t == "bool":
        return False
    if t == "string":
        return ""
    if t == "TypelessData":
        return b""
    if t == "pair":
        return (default_value(node.m_Children[0]), default_value(node.m_Children[1]))
    if is_vector(node):
        return []
    return {c.m_Name: default_value(c) for c in node.m_Children or []}


def convert_value(value: Any, old: TypeTreeNode | None, new: TypeTreeNode) -> Any:
    """Map a value read with `old` onto the layout described by `new`."""
    if old is None:
        return default_value(new)
    t = new.m_Type
    if t in _INT_TYPES:
        if isinstance(value, bool):
            return int(value)
        return int(value) if isinstance(value, (int, float)) else 0
    if t in ("float", "double"):
        return float(value) if isinstance(value, (int, float)) else 0.0
    if t == "bool":
        return bool(value)
    if t in ("string", "TypelessData"):
        return value
    if t == "pair":
        oc = old.m_Children or [None, None]
        return (convert_value(value[0], oc[0], new.m_Children[0]),
                convert_value(value[1], oc[1], new.m_Children[1]))
    if is_vector(new):
        if not is_vector(old):
            return []
        old_elem = old.m_Children[0].m_Children[1]
        new_elem = new.m_Children[0].m_Children[1]
        if _signature(old_elem) == _signature(new_elem):
            return value
        return [convert_value(v, old_elem, new_elem) for v in value]
    # struct
    if not isinstance(value, dict):
        return default_value(new)
    old_children = {c.m_Name: c for c in old.m_Children or []}
    out = {}
    for c in new.m_Children or []:
        oc = old_children.get(c.m_Name)
        out[c.m_Name] = convert_value(value.get(c.m_Name), oc, c) if oc else default_value(c)
    return out


# Per-class fix-ups applied after the generic mapping: (old_dict, new_dict) -> None.
FIXUPS: dict[int, Callable[[dict, dict], None]] = {}


def fixup(class_id: int):
    def register(fn):
        FIXUPS[class_id] = fn
        return fn
    return register


@fixup(95)  # Animator
def _animator(old: dict, new: dict) -> None:
    new["m_KeepAnimatorStateOnDisable"] = old.get("m_KeepAnimatorControllerStateOnDisable", False)
    new["m_WriteDefaultValuesOnDisable"] = False


@fixup(198)  # ParticleSystem: CollisionModule.plane0..5 became m_Planes
def _particles(old: dict, new: dict) -> None:
    oc = old.get("CollisionModule", {})
    planes = [oc[f"plane{i}"] for i in range(6) if f"plane{i}" in oc]
    while planes and planes[-1]["m_PathID"] == 0 and planes[-1]["m_FileID"] == 0:
        planes.pop()
    new["CollisionModule"]["m_Planes"] = planes


@fixup(19)  # Physics2DSettings
def _physics2d(old: dict, new: dict) -> None:
    # SimulationMode2D: 0 FixedUpdate, 2 Script
    new["m_SimulationMode"] = 0 if old.get("m_AutoSimulation", True) else 2


@fixup(28)  # Texture2D
def _texture(old: dict, new: dict) -> None:
    new["m_MipsStripped"] = 0


FIXUPS[89] = _texture  # Cubemap


@fixup(129)  # PlayerSettings
def _player_settings(old: dict, new: dict) -> None:
    # 2019.4's two booleans became activeInputHandler (0 old, 1 new Input System, 2 both).
    new_input = old.get("enableNativePlatformBackendsForNewInputSystem", False)
    old_input = not old.get("disableOldInputManagerSupport", False)
    new["activeInputHandler"] = (2 if old_input else 1) if new_input else 0


@fixup(141)  # BuildSettings
def _build_settings(old: dict, new: dict) -> None:
    new["m_GraphicsAPIs"] = [16]  # GraphicsDeviceType.Metal; the player refuses APIs not listed


@fixup(30)  # GraphicsSettings
def _graphics_settings(old: dict, new: dict) -> None:
    # Builtin shader defines are stored per shader compiler platform; the Windows
    # build only has D3D11 (4). Our Metal (14) programs are translated from those
    # D3D11 variants, so Metal gets the same defines (the enum did not change).
    entries = [e for e in new.get("m_ShaderDefinesPerShaderCompiler", []) if e["shaderPlatform"] != 14]
    d3d = next((e for e in entries if e["shaderPlatform"] == 4), None)
    if d3d is not None:
        entries.append(dict(d3d, shaderPlatform=14))
    new["m_ShaderDefinesPerShaderCompiler"] = entries


@fixup(55)  # PhysicsManager
def _physics(old: dict, new: dict) -> None:
    new["m_DefaultMaxDepenetrationVelocity"] = 10.0  # Unity's default; 0 is rejected


def upgrade_object(class_id: int, data: dict) -> dict:
    old_root = tree(class_id, OLD_VERSION)
    new_root = tree(class_id, NEW_VERSION)
    new = convert_value(data, old_root, new_root)
    fn = FIXUPS.get(class_id)
    if fn:
        fn(data, new)
    return new
