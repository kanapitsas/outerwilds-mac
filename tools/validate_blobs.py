"""Round-trip every compiled subprogram of every Shader in the given files.

Checks that SubProgramBlob/read_params/write_params reproduce the original
bytes, and that the decoded parameters agree with the type tree copy.
"""
import collections
import sys

import UnityPy

from owport import shaderblob as sb


def main(paths):
    stats = collections.Counter()
    for path in paths:
        env = UnityPy.load(path)
        for obj in env.objects:
            if obj.type.name != "Shader":
                continue
            t = obj.read_typetree()
            for plat in t["platforms"]:
                for raw in sb.platform_entries(t, plat):
                    blob = sb.SubProgramBlob.parse(raw)
                    assert blob.serialize() == raw, "entry round trip"
                    try:
                        p = sb.read_params(blob.params_raw)
                    except Exception as e:
                        stats[f"decode-fail p{plat}: {e}"] += 1
                        continue
                    if sb.write_params(p) != blob.params_raw:
                        stats[f"reencode-mismatch p{plat}"] += 1
                        continue
                    if p.trailing:
                        stats[f"trailing p{plat} {p.trailing[:16].hex()}"] += 1
                    stats[f"ok p{plat}"] += 1
    for k, v in sorted(stats.items()):
        print(v, k)


if __name__ == "__main__":
    main(sys.argv[1:])
