import sys, json, UnityPy
from owport import shaderblob as sb, hlslcc, rdef
env = UnityPy.load(sys.argv[1])
want = sys.argv[2]
idxs = [int(x) for x in sys.argv[3].split(",")]
for o in env.objects:
    if o.type.name != "Shader":
        continue
    t = o.read_typetree()
    if t["m_ParsedForm"]["m_Name"] != want:
        continue
    dx = sb.platform_entries(t, 4)
    for idx in idxs:
        b = sb.SubProgramBlob.parse(dx[idx])
        i = b.code.find(b"DXBC")
        dxbc = b.code[i:]
        p = sb.read_params(b.params_raw)
        r = rdef.build_rdef(p, rdef.shader_version(dxbc))
        src, refl = hlslcc.to_metal(rdef.with_rdef(dxbc, r))
        print("=== entry", idx, "type", b.program_type, "ok", refl["ok"])
        print(src)
        print(json.dumps({k: v for k, v in refl.items() if v}, indent=None)[:3000])
