// Writes the public API of the game's assemblies as plain text, one member per line, so it can be
// searched with grep. Run on .NET Framework: apidump.exe <Managed dir> <out file> <assembly>...
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;

static class ApiDump
{
    static string Managed;

    static int Main(string[] args)
    {
        Managed = args[0];
        var resolving = new System.Collections.Generic.HashSet<string>();
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            var n = new AssemblyName(e.Name).Name;
            // The framework's own assemblies come from the framework, not the game's copies.
            if (n == "mscorlib" || n == "System" || n.StartsWith("System.") || !resolving.Add(n)) return null;
            var p = Path.Combine(Managed, n + ".dll");
            return File.Exists(p) ? Assembly.LoadFrom(p) : null;
        };
        var sb = new StringBuilder();
        foreach (var name in args.Skip(2))
        {
            var asm = Assembly.LoadFrom(Path.Combine(Managed, name + ".dll"));
            Type[] types;
            try { types = asm.GetTypes(); }
            catch (ReflectionTypeLoadException e) { types = e.Types.Where(t => t != null).ToArray(); }
            foreach (var t in types.Where(t => t.IsPublic || t.IsNestedPublic).OrderBy(t => t.FullName))
            {
                try { Dump(sb, name, t); } catch (Exception e) { sb.AppendLine("  (could not read: " + e.GetType().Name + ")"); }
            }
        }
        File.WriteAllText(args[1], sb.ToString());
        return 0;
    }

    static string N(Type t)
    {
        if (t == null) return "?";
        if (t.IsGenericParameter) return t.Name;
        if (t.HasElementType) return N(t.GetElementType()) + (t.IsArray ? "[]" : t.IsByRef ? "&" : "*");
        if (t.IsGenericType)
            return t.Name.Split('`')[0] + "<" + string.Join(", ", t.GetGenericArguments().Select(N)) + ">";
        return t.IsNested ? N(t.DeclaringType) + "." + t.Name : t.Name;
    }

    static void Dump(StringBuilder sb, string asm, Type t)
    {
        string kind = t.IsEnum ? "enum" : t.IsInterface ? "interface" : t.IsValueType ? "struct" : "class";
        string bases = t.BaseType != null && t.BaseType != typeof(object) && !t.IsEnum && !t.IsValueType ? " : " + N(t.BaseType) : "";
        sb.AppendLine(kind + " " + (t.Namespace != null ? t.Namespace + "." : "") + N(t) + bases + "    [" + asm + "]");
        if (t.IsEnum)
        {
            sb.AppendLine("  values " + string.Join(", ", Enum.GetNames(t)));
            return;
        }
        const BindingFlags F = BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        foreach (var c in t.GetConstructors(BindingFlags.Public | BindingFlags.Instance))
            sb.AppendLine("  ctor " + N(t) + "(" + Params(c.GetParameters()) + ")");
        foreach (var f in t.GetFields(F))
            sb.AppendLine("  field " + (f.IsStatic ? "static " : "") + N(f.FieldType) + " " + f.Name);
        foreach (var p in t.GetProperties(F))
            sb.AppendLine("  property " + (IsStatic(p) ? "static " : "") + N(p.PropertyType) + " " + p.Name
                          + " {" + (p.CanRead ? " get;" : "") + (p.CanWrite && p.GetSetMethod() != null ? " set;" : "") + " }");
        foreach (var e in t.GetEvents(F))
            sb.AppendLine("  event " + N(e.EventHandlerType) + " " + e.Name);
        foreach (var m in t.GetMethods(F).Where(m => !m.IsSpecialName))
            sb.AppendLine("  method " + (m.IsStatic ? "static " : "") + N(m.ReturnType) + " " + m.Name + "(" + Params(m.GetParameters()) + ")");
    }

    static bool IsStatic(PropertyInfo p)
    {
        var g = p.GetGetMethod() ?? p.GetSetMethod();
        return g != null && g.IsStatic;
    }

    static string Params(ParameterInfo[] ps)
    {
        return string.Join(", ", ps.Select(p => N(p.ParameterType) + " " + p.Name));
    }
}
