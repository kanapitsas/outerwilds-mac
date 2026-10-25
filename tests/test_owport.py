import unittest

from owport import rdef, shaderblob as sb
from owport.mslayout import Layout


class MslLayout(unittest.TestCase):
    def test_metal_alignment_rules(self):
        src = """struct VGlobals_Type
{
    float3 _A;
    float _B;
    float _Arr[3];
    float2 _C;
    float4 hlslcc_mtx4x4_M[4];
};
"""
        m = Layout(src).members("VGlobals_Type")
        self.assertEqual(m["_A"][0], 0)
        self.assertEqual(m["_B"][0], 16)  # float3 takes 16 bytes in MSL, unlike HLSL packing
        self.assertEqual(m["_Arr"][0], 20)  # scalar arrays have a 4-byte stride
        self.assertEqual(m["_C"][0], 32)
        self.assertEqual(m["hlslcc_mtx4x4_M"][0], 48)
        self.assertEqual(Layout(src).size("VGlobals_Type"), 112)

    def test_nested_struct_array(self):
        src = """struct Inner_Type
{
    float4 hlslcc_mtx4x4a[4];
    float4 hlslcc_mtx4x4b[4];
};
struct Outer_Type
{
    Inner_Type Inner[2];
};
"""
        lay = Layout(src)
        self.assertEqual(lay.size("Inner_Type"), 128)
        self.assertEqual(lay.size("Outer_Type"), 256)


class Blob(unittest.TestCase):
    def test_params_round_trip(self):
        p = sb.Params(source_map=25, channels=[(0, 13), (3, 14)])
        cb = sb.CBuffer("VGlobals", 160, [sb.Member("_Color", 0, 1, 4, False, 0, 144),
                                           sb.Member("unity_MatrixVP", 0, 4, 4, True, 0, 64)])
        cb.structs.append(sb.Struct("s", 0, 2, 128, [sb.Member("m", 0, 4, 4, True, 0, 0)]))
        p.cbuffers.append(cb)
        p.bindings = [sb.Binding("_MainTex", sb.BIND_TEXTURE, 0, [0, 4]),
                      sb.Binding("VGlobals", sb.BIND_CBUFFER, 0, [0]),
                      sb.Binding("", 4, 1, [85])]
        raw = sb.write_params(p)
        self.assertEqual(sb.write_params(sb.read_params(raw)), raw)


class PlayerSettingsUpgrade(unittest.TestCase):
    def test_new_input_system_only(self):
        from owport import upgrade
        new = {"activeInputHandler": 0}
        upgrade.FIXUPS[129]({"enableNativePlatformBackendsForNewInputSystem": True,
                             "disableOldInputManagerSupport": True}, new)
        self.assertEqual(new["activeInputHandler"], 1)

    def test_both_input_systems(self):
        from owport import upgrade
        new = {}
        upgrade.FIXUPS[129]({"enableNativePlatformBackendsForNewInputSystem": True,
                             "disableOldInputManagerSupport": False}, new)
        self.assertEqual(new["activeInputHandler"], 2)


class GraphicsSettingsUpgrade(unittest.TestCase):
    def test_metal_gets_d3d11_shader_defines(self):
        from owport import upgrade
        new = {"m_ShaderDefinesPerShaderCompiler": [
            {"shaderPlatform": 4, "defines_Tier1": [7], "defines_Tier2": [7], "defines_Tier3": [7]}]}
        upgrade.FIXUPS[30]({}, new)
        plats = {e["shaderPlatform"]: e for e in new["m_ShaderDefinesPerShaderCompiler"]}
        self.assertEqual(sorted(plats), [4, 14])
        self.assertEqual(plats[14]["defines_Tier3"], [7])


class InlineSampler(unittest.TestCase):
    def test_state_names(self):
        self.assertEqual(rdef.inline_sampler_name(84), "inlinesampler_point_clamp")
        self.assertEqual(rdef.inline_sampler_name(85), "inlinesampler_linear_clamp")
        self.assertEqual(rdef.inline_sampler_name(1), "inlinesampler_linear_repeat")


class NamedCBufferLayout(unittest.TestCase):
    # Unity's Metal runtime shares a named cbuffer (UnityPerCamera...) between the
    # vertex and fragment stages. HLSLcc keeps only the members each stage uses, so
    # both stages must keep the full D3D11 layout or they read each other's values
    # (this made the sun's fog draw through the ground).
    def test_named_cbuffer_keeps_d3d11_offsets(self):
        from owport import shaders
        msl = """struct UnityPerCamera_Type
{
    float4 _ProjectionParams;
    float4 _ZBufferParams;
};
"""
        cb = sb.CBuffer("UnityPerCamera", 144, [sb.Member("_ProjectionParams", 0, 1, 4, False, 0, 80),
                                                 sb.Member("_ZBufferParams", 0, 1, 4, False, 0, 112)])
        dx = sb.Params(cbuffers=[cb])
        refl = {"cbBindings": [{"name": "UnityPerCamera", "slot": 1}]}
        mp = shaders.metal_params(dx, refl, shaders.canonical_cbuffers(msl, dx, refl))
        out = mp.cbuffers[0]
        self.assertEqual({m.name: m.offset for m in out.members}, {"_ProjectionParams": 80, "_ZBufferParams": 112})
        self.assertEqual(out.size, 144)

    def test_globals_left_packed(self):
        from owport import shaders
        msl = "struct VGlobals_Type\n{\n    float4 _Color;\n};\n"
        dx = sb.Params(cbuffers=[sb.CBuffer("$Globals", 64, [sb.Member("_Color", 0, 1, 4, False, 0, 48)])])
        refl = {"cbBindings": [{"name": "VGlobals", "slot": 0}]}
        self.assertEqual(shaders.canonical_cbuffers(msl, dx, refl), msl)

    def test_unaligned_float3_keeps_packed_layout(self):
        # UnityTerrain has a float3 at offset 196: no MSL layout can match D3D11 there.
        from owport import shaders
        msl = "struct UnityTerrain_Type\n{\n    float3 _Right;\n    float4 _Up;\n};\n"
        cb = sb.CBuffer("UnityTerrain", 272, [sb.Member("_Right", 0, 1, 3, False, 0, 196),
                                               sb.Member("_Up", 0, 1, 4, False, 0, 208)])
        dx = sb.Params(cbuffers=[cb])
        refl = {"cbBindings": [{"name": "UnityTerrain", "slot": 2}]}
        self.assertEqual(shaders.canonical_cbuffers(msl, dx, refl), msl)



class BuildHelpers(unittest.TestCase):
    def test_clone_to_relative_path(self):
        # README builds "-o 'Outer Wilds.app'": a path with no directory part.
        import os, tempfile
        from owport import build
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.txt")
            open(src, "w").write("x")
            cwd = os.getcwd()
            os.chdir(d)
            try:
                build.clone(src, "out.txt")
                build.clone_tree(src, "tree.txt")
            finally:
                os.chdir(cwd)
            self.assertTrue(os.path.exists(os.path.join(d, "out.txt")))


class TextureDimensions(unittest.TestCase):
    def test_hlslcc_enum_to_unity(self):
        from owport import shaders
        # TD_3D, TD_CUBE, TD_2DARRAY, TD_CUBEARRAY
        self.assertEqual([shaders._HLSLCC_DIM[i] for i in (3, 4, 6, 7)], [3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
