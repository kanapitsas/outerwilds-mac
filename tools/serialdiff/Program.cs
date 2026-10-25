// Compare the serialized field lists of [Serializable] types between two sets
// of Unity engine assemblies (e.g. 2019.4 vs 2020.3). Prints types whose
// managed serialization layout changed.
// usage: serialdiff <old Managed dir> <new Managed dir> [file pattern]
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;

static class Program
{
    static bool IsScript(TypeDefinition t)
    {
        for (var cur = t; cur != null; )
        {
            if (cur.FullName == "UnityEngine.MonoBehaviour" || cur.FullName == "UnityEngine.ScriptableObject") return true;
            if (cur.BaseType == null) return false;
            try { cur = cur.BaseType.Resolve(); } catch { return false; }
        }
        return false;
    }

    static string Pattern = "UnityEngine*.dll";

    static Dictionary<string, string> Layouts(string dir)
    {
        var res = new DefaultAssemblyResolver();
        res.AddSearchDirectory(dir);
        var map = new Dictionary<string, string>();
        foreach (var path in Directory.GetFiles(dir, Pattern))
        {
            AssemblyDefinition asm;
            try { asm = AssemblyDefinition.ReadAssembly(path, new ReaderParameters { AssemblyResolver = res }); }
            catch { continue; }
            foreach (var t in asm.MainModule.GetTypes())
            {
                bool ser = (t.Attributes & TypeAttributes.Serializable) != 0 || IsScript(t);
                if (!ser) continue;
                var fields = t.Fields.Where(f => !f.IsStatic && !f.IsLiteral && !f.IsInitOnly && !f.IsNotSerialized &&
                    (f.IsPublic || f.CustomAttributes.Any(a => a.AttributeType.Name == "SerializeField")))
                    .Select(f => $"{f.Name}:{f.FieldType.FullName}");
                map[t.FullName] = string.Join(", ", fields);
            }
        }
        return map;
    }

    static int Main(string[] args)
    {
        if (args.Length > 2) Pattern = args[2];
        var a = Layouts(args[0]);
        var b = Layouts(args[1]);
        foreach (var k in a.Keys.Intersect(b.Keys).OrderBy(x => x))
            if (a[k] != b[k])
                Console.WriteLine($"{k}\n  2019: {a[k]}\n  2020: {b[k]}");
        return 0;
    }
}
