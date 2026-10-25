"""Rewrite one serialized file (globalgamemanagers, levelN, *.assets) for 2020.3."""

from __future__ import annotations

import argparse
import collections
import sys
import time

import UnityPy

from . import shaders, upgrade


def convert(src: str, dst: str, shader_stats: shaders.Stats | None = None, verbose: bool = True) -> dict:
    t0 = time.time()
    env = UnityPy.load(src)
    sf = next(iter(env.files.values()))
    stats = collections.Counter()
    for obj in sf.objects.values():
        cid = obj.class_id
        if cid == 48:
            old = obj.read_typetree(nodes=upgrade.tree(48, upgrade.OLD_VERSION))
            new = shaders.convert_shader(old, shader_stats if shader_stats is not None else shaders.Stats())
            obj.save_typetree(new, nodes=upgrade.tree(48, upgrade.NEW_VERSION))
            stats["Shader"] += 1
            continue
        if not upgrade.layout_changed(cid):
            continue
        old_nodes = upgrade.tree(cid, upgrade.OLD_VERSION)
        new_nodes = upgrade.tree(cid, upgrade.NEW_VERSION)
        data = obj.read_typetree(nodes=old_nodes)
        obj.save_typetree(upgrade.upgrade_object(cid, data), nodes=new_nodes)
        stats[obj.type.name] += 1
    sf.unity_version = upgrade.NEW_VERSION_STRING
    with open(dst, "wb") as f:
        f.write(sf.save())
    if verbose:
        print(f"{src.split('/')[-1]}: {dict(stats)} in {time.time() - t0:.1f}s", file=sys.stderr)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    a = ap.parse_args()
    convert(a.src, a.dst)


if __name__ == "__main__":
    main()
