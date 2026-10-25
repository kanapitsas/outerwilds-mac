"""Translate every Shader in the given files and dump the MSL for checking."""
import os
import re
import sys
import time

import UnityPy

from owport import shaders, upgrade

out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)
stats = shaders.Stats()
t0 = time.time()


def sink(name, idx, msl):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", name)
    with open(os.path.join(out_dir, f"{safe}__{idx}.metal"), "w") as f:
        f.write(msl)


for path in sys.argv[2:]:
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.class_id != 48:
            continue
        old = obj.read_typetree(nodes=upgrade.tree(48, upgrade.OLD_VERSION))
        shaders.convert_shader(old, stats, sink)
print(f"translated {stats.translated} failed {stats.failed} dropped-stages {stats.dropped_stage} in {time.time()-t0:.0f}s")
for k, v in stats.errors.most_common(25):
    print(v, k)
