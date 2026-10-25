// Unity 2020.1 started serializing fields of generic types (Foo<T>); 2019.4
// skipped them. Data written by 2019.4 therefore lacks those fields, and a
// 2020.3 player reading it runs off the end of the object. This tool finds
// such fields in the game's assemblies and, with --patch, marks them
// [NonSerialized] so 2020.3 skips them exactly as 2019.4 did.
//
// usage: serialpatch <Managed dir> <assembly.dll>... [--patch <out dir>]
//        serialpatch mark <in.dll> <out.dll> <Type.FullName>::<field>...
//          marks the given fields [NonSerialized] (engine fields 2020.3 added,
//          e.g. PersistentCall.m_TargetAssemblyTypeName, absent from 2019.4 data)

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;

static class Program
{
    static readonly HashSet<string> Visited = new HashSet<string>();
    static readonly List<FieldDefinition> Offenders = new List<FieldDefinition>();

    // linkcheck <Managed dir> <asm>...: report member references that do not
    // resolve against the assemblies in the directory (API breaks).
    static int LinkCheck(string[] args)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(args[1]);
        int broken = 0;
        foreach (var name in args.Skip(2))
        {
            var asm = AssemblyDefinition.ReadAssembly(Path.Combine(args[1], name), new ReaderParameters { AssemblyResolver = resolver });
            var seen = new HashSet<string>();
            foreach (var mr in asm.MainModule.GetMemberReferences())
            {
                string key = mr.FullName;
                if (!seen.Add(key)) continue;
                bool ok;
                try { ok = mr.Resolve() != null; } catch { ok = false; }
                if (!ok) { broken++; Console.WriteLine($"{name}: {key}"); }
            }
            foreach (var tr in asm.MainModule.GetTypeReferences())
            {
                bool ok;
                try { ok = tr.Resolve() != null; } catch { ok = false; }
                if (!ok && seen.Add(tr.FullName)) { broken++; Console.WriteLine($"{name}: type {tr.FullName} ({tr.Scope})"); }
            }
        }
        Console.WriteLine($"{broken} unresolved reference(s)");
        return 0;
    }

    // fixapi <in.dll> <out.dll> <2020 Managed dir>: adapt an assembly compiled
    // against 2019.4 to 2020.3 API changes it uses:
    //  - UIVertex.uv0..uv3 became Vector4: retarget field refs, convert values
    //  - retarget types that moved namespace
    //  - drop interface implementations whose interface no longer exists
    static int FixApi(string[] args)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(args[3]);
        var asm = AssemblyDefinition.ReadAssembly(args[1], new ReaderParameters { AssemblyResolver = resolver, InMemory = true });
        var module = asm.MainModule;
        var core = resolver.Resolve(new AssemblyNameReference("UnityEngine.CoreModule", null)).MainModule;
        var v4 = core.GetType("UnityEngine.Vector4");
        var toV4 = module.ImportReference(v4.Methods.First(m => m.Name == "op_Implicit" && m.ReturnType.FullName == "UnityEngine.Vector4"
                                                            && m.Parameters[0].ParameterType.FullName == "UnityEngine.Vector2"));
        var toV2 = module.ImportReference(v4.Methods.First(m => m.Name == "op_Implicit" && m.ReturnType.FullName == "UnityEngine.Vector2"));
        var v4Ref = module.ImportReference(v4);
        int fixedRefs = 0, droppedIfaces = 0;
        // Types that only moved namespace between 2019.4 and 2020.3.
        var moved = new Dictionary<string, string> {
            { "UnityEngine.Experimental.Animations.IAnimationWindowPreview", "UnityEngine.Animations" },
        };
        foreach (var tr in module.GetTypeReferences())
            if (moved.TryGetValue(tr.FullName, out var ns))
            {
                Console.WriteLine($"  retarget {tr.FullName} -> {ns}.{tr.Name}");
                tr.Namespace = ns;
            }
        foreach (var type in module.GetTypes())
        {
            for (int i = type.Interfaces.Count - 1; i >= 0; i--)
            {
                TypeDefinition r = null;
                try { r = type.Interfaces[i].InterfaceType.Resolve(); } catch { }
                if (r == null)
                {
                    Console.WriteLine($"  {type.FullName}: drop interface {type.Interfaces[i].InterfaceType.FullName}");
                    type.Interfaces.RemoveAt(i);
                    droppedIfaces++;
                }
            }
            foreach (var method in type.Methods.Where(m => m.HasBody))
            {
                var il = method.Body.GetILProcessor();
                foreach (var ins in method.Body.Instructions.ToList())
                {
                    if (!(ins.Operand is FieldReference fr) || fr.DeclaringType.FullName != "UnityEngine.UIVertex"
                        || !fr.Name.StartsWith("uv") || fr.FieldType.FullName != "UnityEngine.Vector2") continue;
                    var nf = new FieldReference(fr.Name, v4Ref, fr.DeclaringType);
                    if (ins.OpCode == Mono.Cecil.Cil.OpCodes.Stfld)
                    {
                        il.InsertBefore(ins, il.Create(Mono.Cecil.Cil.OpCodes.Call, toV4));
                        ins.Operand = nf;
                    }
                    else if (ins.OpCode == Mono.Cecil.Cil.OpCodes.Ldfld)
                    {
                        ins.Operand = nf;
                        il.InsertAfter(ins, il.Create(Mono.Cecil.Cil.OpCodes.Call, toV2));
                    }
                    else
                        throw new Exception($"{method.FullName}: unsupported {ins.OpCode} on {fr.Name}");
                    fixedRefs++;
                }
            }
        }
        asm.Write(args[2]);
        Console.WriteLine($"fixapi {Path.GetFileName(args[1])}: {fixedRefs} UIVertex uv ref(s), {droppedIfaces} interface(s) dropped");
        return 0;
    }

    // mousemult <in.dll> <out.dll> <Managed dir>: multiply the mouse delta the game
    // reads each frame (BaseInputManager.UpdateMouse) by OWMacExtras.Extras.MouseMultiplier.
    static int MouseMult(string[] args)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(args[3]);
        var asm = AssemblyDefinition.ReadAssembly(args[1], new ReaderParameters { AssemblyResolver = resolver, InMemory = true });
        var module = asm.MainModule;
        var extras = resolver.Resolve(new AssemblyNameReference("OWMacExtras", null)).MainModule;
        var mult = module.ImportReference(extras.GetType("OWMacExtras.Extras").Fields.First(f => f.Name == "MouseMultiplier"));
        var core = resolver.Resolve(new AssemblyNameReference("UnityEngine.CoreModule", null)).MainModule;
        var v2 = core.GetType("UnityEngine.Vector2");
        var mul = module.ImportReference(v2.Methods.First(m => m.Name == "op_Multiply" && m.Parameters.Count == 2
            && m.Parameters[0].ParameterType.FullName == "UnityEngine.Vector2" && m.Parameters[1].ParameterType.FullName == "System.Single"));
        var bim = module.GetType("BaseInputManager");
        var delta = bim.Fields.First(f => f.Name == "MouseDelta");
        var method = bim.Methods.First(m => m.Name == "UpdateMouse");
        var il = method.Body.GetILProcessor();
        var ret = method.Body.Instructions.Last();
        if (ret.OpCode != Mono.Cecil.Cil.OpCodes.Ret) throw new Exception("UpdateMouse does not end with ret");
        // MouseDelta = MouseDelta * Extras.MouseMultiplier; (inserted before ret; branches to ret now go through it)
        var first = il.Create(Mono.Cecil.Cil.OpCodes.Ldsfld, delta);
        foreach (var ins in method.Body.Instructions)
            if (ins.Operand == ret) ins.Operand = first;
        il.InsertBefore(ret, first);
        il.InsertBefore(ret, il.Create(Mono.Cecil.Cil.OpCodes.Ldsfld, mult));
        il.InsertBefore(ret, il.Create(Mono.Cecil.Cil.OpCodes.Call, mul));
        il.InsertBefore(ret, il.Create(Mono.Cecil.Cil.OpCodes.Stsfld, delta));
        asm.Write(args[2]);
        Console.WriteLine($"mousemult {Path.GetFileName(args[1])}: patched BaseInputManager.UpdateMouse");
        return 0;
    }

    // skipintro <in.dll> <out.dll> <Managed dir>: at the end of TitleScreenAnimation.Awake,
    // if OWMacExtras.Extras.SkipIntro, start the title screen as when returning from
    // a game: no gamepad splash, no camera pan, 1 s fade.
    static int SkipIntro(string[] args)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(args[3]);
        var asm = AssemblyDefinition.ReadAssembly(args[1], new ReaderParameters { AssemblyResolver = resolver, InMemory = true });
        var module = asm.MainModule;
        var extras = resolver.Resolve(new AssemblyNameReference("OWMacExtras", null)).MainModule;
        var flag = module.ImportReference(extras.GetType("OWMacExtras.Extras").Fields.First(f => f.Name == "SkipIntro"));
        var type = module.GetType("TitleScreenAnimation");
        FieldDefinition F(string n) => type.Fields.First(f => f.Name == n);
        var method = type.Methods.First(m => m.Name == "Awake");
        var il = method.Body.GetILProcessor();
        var ret = method.Body.Instructions.Last();
        if (ret.OpCode != Mono.Cecil.Cil.OpCodes.Ret) throw new Exception("Awake does not end with ret");
        var first = il.Create(Mono.Cecil.Cil.OpCodes.Ldsfld, flag);
        foreach (var ins in method.Body.Instructions)
            if (ins.Operand == ret) ins.Operand = first;
        var seq = new[] {
            first,
            il.Create(Mono.Cecil.Cil.OpCodes.Brfalse, ret),
            il.Create(Mono.Cecil.Cil.OpCodes.Ldarg_0), il.Create(Mono.Cecil.Cil.OpCodes.Ldc_I4_0), il.Create(Mono.Cecil.Cil.OpCodes.Stfld, F("_gamepadSplash")),
            il.Create(Mono.Cecil.Cil.OpCodes.Ldarg_0), il.Create(Mono.Cecil.Cil.OpCodes.Ldc_I4_0), il.Create(Mono.Cecil.Cil.OpCodes.Stfld, F("_introPan")),
            il.Create(Mono.Cecil.Cil.OpCodes.Ldarg_0), il.Create(Mono.Cecil.Cil.OpCodes.Ldc_R4, 1f), il.Create(Mono.Cecil.Cil.OpCodes.Stfld, F("_fadeDuration")),
        };
        foreach (var ins in seq) il.InsertBefore(ret, ins);
        asm.Write(args[2]);
        Console.WriteLine($"skipintro {Path.GetFileName(args[1])}: patched TitleScreenAnimation.Awake");
        return 0;
    }

    static int Mark(string[] args)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(args[1])));
        var asm = AssemblyDefinition.ReadAssembly(args[1], new ReaderParameters { AssemblyResolver = resolver, InMemory = true });
        foreach (var spec in args.Skip(3))
        {
            var parts = spec.Split(new[] { "::" }, StringSplitOptions.None);
            var type = asm.MainModule.GetType(parts[0]) ?? throw new Exception($"type {parts[0]} not found");
            var field = type.Fields.First(f => f.Name == parts[1]);
            field.Attributes |= FieldAttributes.NotSerialized;
            Console.WriteLine($"marked {spec} [NonSerialized]");
        }
        asm.Write(args[2]);
        return 0;
    }

    static int Main(string[] args)
    {
        if (args[0] == "mark") return Mark(args);
        if (args[0] == "linkcheck") return LinkCheck(args);
        if (args[0] == "fixapi") return FixApi(args);
        if (args[0] == "mousemult") return MouseMult(args);
        if (args[0] == "skipintro") return SkipIntro(args);
        string managed = args[0];
        string outDir = null;
        var asmNames = new List<string>();
        for (int i = 1; i < args.Length; i++)
        {
            if (args[i] == "--patch") outDir = args[++i];
            else asmNames.Add(args[i]);
        }

        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(managed);
        var rp = new ReaderParameters { AssemblyResolver = resolver, ReadWrite = false, InMemory = true };
        var assemblies = asmNames.Select(n => AssemblyDefinition.ReadAssembly(Path.Combine(managed, n), rp)).ToList();

        foreach (var asm in assemblies)
            foreach (var t in asm.MainModule.GetTypes())
                if (IsUnityObjectScript(t))
                    VisitType(t);

        foreach (var f in Offenders.Distinct())
            Console.WriteLine($"{f.Module.Assembly.Name.Name}: {f.DeclaringType.FullName}.{f.Name} : {f.FieldType.FullName}");
        Console.WriteLine($"{Offenders.Distinct().Count()} generic serialized field(s)");

        if (outDir != null)
        {
            Directory.CreateDirectory(outDir);
            foreach (var f in Offenders.Distinct())
                f.Attributes |= FieldAttributes.NotSerialized;
            foreach (var asm in assemblies)
                asm.Write(Path.Combine(outDir, Path.GetFileName(asm.MainModule.FileName)));
        }
        return 0;
    }

    static TypeDefinition Resolve(TypeReference t)
    {
        try { return t.Resolve(); } catch { return null; }
    }

    static bool DerivesFrom(TypeDefinition t, string fullName)
    {
        while (t != null)
        {
            if (t.FullName == fullName) return true;
            if (t.BaseType == null) return false;
            t = Resolve(t.BaseType);
        }
        return false;
    }

    static bool IsUnityObjectScript(TypeDefinition t) =>
        DerivesFrom(t, "UnityEngine.MonoBehaviour") || DerivesFrom(t, "UnityEngine.ScriptableObject");

    static bool IsSerializedField(FieldDefinition f)
    {
        if (f.IsStatic || f.IsLiteral || f.IsInitOnly || f.IsNotSerialized) return false;
        bool serializeField = f.CustomAttributes.Any(a => a.AttributeType.Name == "SerializeField" || a.AttributeType.Name == "SerializeReference");
        return f.IsPublic || serializeField;
    }

    static void VisitType(TypeDefinition t)
    {
        if (t == null || !Visited.Add(t.FullName)) return;
        for (var cur = t; cur != null && cur.FullName != "UnityEngine.MonoBehaviour" && cur.FullName != "UnityEngine.ScriptableObject"
             && cur.FullName != "System.Object"; cur = cur.BaseType == null ? null : Resolve(cur.BaseType))
        {
            foreach (var f in cur.Fields)
            {
                if (!IsSerializedField(f)) continue;
                if (f.CustomAttributes.Any(a => a.AttributeType.Name == "SerializeReference")) continue;
                VisitFieldType(f, f.FieldType);
            }
        }
    }

    static void VisitFieldType(FieldDefinition owner, TypeReference ft)
    {
        if (ft is ArrayType at) { VisitFieldType(owner, at.ElementType); return; }
        if (ft is GenericInstanceType git)
        {
            if (git.ElementType.FullName == "System.Collections.Generic.List`1")
            {
                VisitFieldType(owner, git.GenericArguments[0]);
                return;
            }
            var gdef = Resolve(git.ElementType);
            if (gdef != null && IsSerializableUserType(gdef))
                Offenders.Add(owner);
            return;
        }
        var def = Resolve(ft);
        if (def == null || def.IsPrimitive || def.IsEnum || ft.FullName == "System.String") return;
        if (DerivesFrom(def, "UnityEngine.Object")) return;  // reference only
        if (IsSerializableUserType(def)) VisitType(def);
    }

    static bool IsSerializableUserType(TypeDefinition d) =>
        (d.Attributes & TypeAttributes.Serializable) != 0 && !d.Namespace.StartsWith("System") && !d.Namespace.StartsWith("UnityEngine");
}
