// Kilix Windows desktop worker. Loaded by desktop.ps1 on .NET Framework 4.x.
// This is a one-request process: the transport owns task lifetime and timeout.
using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Automation;

public sealed class KilixDesktopException : Exception
{
    public string Code { get; private set; }
    public KilixDesktopException(string code, string message) : base(message) { Code = code; }
}

public static class KilixDesktop
{
    public const string Schema = "kilix.desktop/v1";
    public const int MaxCaptureBytes = 32 * 1024 * 1024;
    private static readonly string[] Operations = {
        "status", "windows", "find", "wait", "inspect", "screenshot", "capture", "focus", "move", "minimize",
        "maximize", "restore", "close", "launch", "type", "key", "click", "scroll", "invoke", "set-value"
    };
    private static readonly CultureInfo Invariant = CultureInfo.InvariantCulture;

    private static KilixDesktopException Fail(string code, string message)
    { return new KilixDesktopException(code, message); }

    private static Dictionary<string, object> Map(params object[] pairs)
    {
        Dictionary<string, object> value = new Dictionary<string, object>(StringComparer.Ordinal);
        for (int i = 0; i < pairs.Length; i += 2) value.Add((string)pairs[i], pairs[i + 1]);
        return value;
    }

    // PowerShell 5.1's JSON conversion can collapse duplicate/case-insensitive keys.
    // Keep JSON field identity and integer types intact at this native boundary.
    public static IDictionary ReadRequest(string json)
    { return ObjectValue(new JsonReader(json).Read(), "request"); }

    private sealed class JsonReader
    {
        private readonly string text;
        private int index;
        public JsonReader(string json)
        {
            if (json == null || json.Length > 1048576) throw Fail("invalid_request", "Request JSON exceeds its size limit.");
            text = json;
        }
        private void Space() { while (index < text.Length && (text[index] == ' ' || text[index] == '\t' || text[index] == '\r' || text[index] == '\n')) index++; }
        private KilixDesktopException Invalid() { return Fail("invalid_request", "Malformed JSON request or duplicate object field."); }
        public object Read()
        { object result = Value(0); Space(); if (index != text.Length) throw Invalid(); return result; }
        private bool Take(char c)
        { Space(); if (index < text.Length && text[index] == c) { index++; return true; } return false; }
        private object Value(int depth)
        {
            Space(); if (depth > 32 || index >= text.Length) throw Invalid();
            char c = text[index];
            if (c == '"') return Text();
            if (c == '{')
            {
                index++; Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.Ordinal);
                if (Take('}')) return result;
                do
                {
                    Space(); string key = Text();
                    if (result.ContainsKey(key) || !Take(':')) throw Invalid();
                    result.Add(key, Value(depth + 1));
                    if (Take('}')) return result;
                } while (Take(','));
                throw Invalid();
            }
            if (c == '[')
            {
                index++; List<object> result = new List<object>();
                if (Take(']')) return result.ToArray();
                do { result.Add(Value(depth + 1)); if (Take(']')) return result.ToArray(); } while (Take(','));
                throw Invalid();
            }
            foreach (string literal in new string[] { "true", "false", "null" })
            {
                if (index + literal.Length <= text.Length && String.CompareOrdinal(text, index, literal, 0, literal.Length) == 0)
                { index += literal.Length; return literal == "null" ? null : (object)(literal == "true"); }
            }
            int start = index;
            if (c == '-') index++;
            if (index >= text.Length || text[index] < '0' || text[index] > '9') throw Invalid();
            if (text[index] == '0') index++;
            else while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
            bool floating = false;
            if (index < text.Length && text[index] == '.')
            {
                floating = true; index++; int digits = index;
                while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
                if (index == digits) throw Invalid();
            }
            if (index < text.Length && (text[index] == 'e' || text[index] == 'E'))
            {
                floating = true; index++;
                if (index < text.Length && (text[index] == '+' || text[index] == '-')) index++;
                int digits = index;
                while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
                if (index == digits) throw Invalid();
            }
            string number = text.Substring(start, index - start); long integer; double real;
            if (!floating && Int64.TryParse(number, NumberStyles.AllowLeadingSign, Invariant, out integer)) return integer;
            if (!Double.TryParse(number, NumberStyles.Float, Invariant, out real) || Double.IsInfinity(real) || Double.IsNaN(real)) throw Invalid();
            return real; // Integer fields will reject fractional/exponent or overflowing tokens.
        }
        private string Text()
        {
            if (index >= text.Length || text[index++] != '"') throw Invalid();
            StringBuilder result = new StringBuilder();
            while (index < text.Length)
            {
                char c = text[index++];
                if (c == '"') return StringValue(result.ToString(), "JSON string", 1048576, true);
                if (c < 32) throw Invalid();
                if (c != '\\') { result.Append(c); continue; }
                if (index >= text.Length) throw Invalid();
                switch (text[index++])
                {
                    case '"': result.Append('"'); break; case '\\': result.Append('\\'); break; case '/': result.Append('/'); break;
                    case 'b': result.Append('\b'); break; case 'f': result.Append('\f'); break;
                    case 'n': result.Append('\n'); break; case 'r': result.Append('\r'); break; case 't': result.Append('\t'); break;
                    case 'u':
                        ushort code;
                        if (index + 4 > text.Length || !UInt16.TryParse(text.Substring(index, 4), NumberStyles.AllowHexSpecifier, Invariant, out code)) throw Invalid();
                        result.Append((char)code); index += 4; break;
                    default: throw Invalid();
                }
            }
            throw Invalid();
        }
    }

    // Deliberately use ordinal field names even when a caller passes a PowerShell hashtable.
    private static Dictionary<string, object> ObjectValue(object value, string name)
    {
        IDictionary input = value as IDictionary;
        if (input == null) throw Fail("invalid_request", name + " must be an object.");
        Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.Ordinal);
        foreach (DictionaryEntry entry in input)
        {
            string key = entry.Key as string;
            if (key == null || result.ContainsKey(key)) throw Fail("invalid_request", "Invalid object field.");
            result.Add(key, entry.Value);
        }
        return result;
    }

    private static void Fields(Dictionary<string, object> value, string required, string optional)
    {
        HashSet<string> allowed = new HashSet<string>(StringComparer.Ordinal);
        foreach (string key in (required + " " + optional).Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries))
            allowed.Add(key);
        foreach (string key in value.Keys)
            if (!allowed.Contains(key)) throw Fail("invalid_request", "Unknown field in request object.");
        foreach (string key in required.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries))
            if (!value.ContainsKey(key)) throw Fail("invalid_request", "Missing required field: " + key + ".");
    }

    private static string StringValue(object value, string name, int max, bool empty)
    {
        string text = value as string;
        if (text == null || (!empty && text.Length == 0) || text.Length > max || text.IndexOf('\0') >= 0)
            throw Fail("invalid_request", name + " has an invalid string value or length.");
        for (int i = 0; i < text.Length; i++)
        {
            if (Char.IsHighSurrogate(text[i]))
            {
                if (++i >= text.Length || !Char.IsLowSurrogate(text[i]))
                    throw Fail("invalid_request", name + " contains invalid Unicode.");
            }
            else if (Char.IsLowSurrogate(text[i])) throw Fail("invalid_request", name + " contains invalid Unicode.");
        }
        return text;
    }

    private static string InputText(object value)
    { return StringValue(value, "text", 16384, true); }

    private static int Integer(object value, string name, int min, int max)
    {
        if (!(value is int || value is long || value is uint || value is short || value is ushort || value is byte || value is sbyte || value is ulong))
            throw Fail("invalid_request", name + " must be an integer.");
        decimal n = Convert.ToDecimal(value, Invariant);
        if (n < min || n > max) throw Fail("invalid_request", name + " is out of range.");
        return (int)n;
    }

    private static int Number(Dictionary<string, object> value, string key, int fallback, int min, int max)
    { return value.ContainsKey(key) ? Integer(value[key], key, min, max) : fallback; }

    private static uint ProcessId(object value)
    {
        if (!(value is int || value is long || value is uint || value is short || value is ushort || value is byte || value is sbyte || value is ulong))
            throw Fail("invalid_request", "pid must be an integer.");
        decimal number = Convert.ToDecimal(value, Invariant);
        if (number < 1 || number > UInt32.MaxValue) throw Fail("invalid_request", "pid is out of range.");
        return (uint)number;
    }

    private static ulong DecimalId(object value, string name)
    {
        string text = StringValue(value, name, 20, false);
        ulong number;
        if (!Regex.IsMatch(text, "^[0-9]+$", RegexOptions.CultureInvariant) ||
            !UInt64.TryParse(text, NumberStyles.None, Invariant, out number) || number == 0)
            throw Fail("invalid_request", name + " must be a positive decimal string.");
        return number;
    }

    private static string AbsolutePath(object value, string name)
    {
        string path = StringValue(value, name, 32760, false);
        bool drive = Regex.IsMatch(path, @"^[A-Za-z]:[\\/]", RegexOptions.CultureInvariant);
        bool unc = Regex.IsMatch(path, @"^\\\\[^\\/:*?""<>|]+\\[^\\/:*?""<>|]+(?:[\\/]|$)", RegexOptions.CultureInvariant);
        if ((!drive && !unc) || path.StartsWith(@"\\.\", StringComparison.Ordinal) || path.StartsWith(@"\\?\", StringComparison.Ordinal) ||
            path.IndexOfAny(new char[] { '*', '?', '"', '<', '>', '|' }) >= 0 || path.IndexOf(':', drive ? 2 : 0) >= 0)
            throw Fail("invalid_request", name + " must be an absolute Windows filesystem path.");
        foreach (char c in path) if (c < 32) throw Fail("invalid_request", name + " contains a control character.");
        return path;
    }

    private static IList ArrayValue(object value, string name, int min, int max)
    {
        IList list = value as IList;
        if (list == null || list.Count < min || list.Count > max)
            throw Fail("invalid_request", name + " must be an array of the supported length.");
        return list;
    }

    private sealed class Request
    {
        public string Operation;
        public Dictionary<string, object> Params;
        public Target Target;
        public int Timeout;
    }

    private sealed class Target
    {
        public ulong Hwnd;
        public uint Pid;
        public ulong Started;
        public IntPtr Handle { get { return new IntPtr(unchecked((long)Hwnd)); } }
        public Dictionary<string, object> Json()
        { return Map("hwnd", Hwnd.ToString(Invariant), "pid", Pid, "started", Started.ToString(Invariant)); }
    }

    private static bool WindowFilters(Dictionary<string, object> p)
    { return p.ContainsKey("app") || p.ContainsKey("title") || p.ContainsKey("pid"); }

    private static void ValidateWindowFilters(Dictionary<string, object> p)
    {
        if (p.ContainsKey("title")) StringValue(p["title"], "title", 32767, true);
        if (p.ContainsKey("pid")) ProcessId(p["pid"]);
        if (!p.ContainsKey("app")) return;
        string app = StringValue(p["app"], "app", 32760, false);
        if (app.IndexOfAny(new char[] { '\\', '/', ':' }) >= 0)
        {
            AbsolutePath(app, "app");
            if (app.StartsWith(@"\\", StringComparison.Ordinal) && app.Replace('/', '\\').Substring(2).Split('\\').Length < 3)
                throw Fail("invalid_request", "app must include a filename after the UNC share.");
            string basename = app.Replace('/', '\\'); basename = basename.Substring(basename.LastIndexOf('\\') + 1);
            if (basename.Length == 0 || basename == "." || basename == "..") throw Fail("invalid_request", "app must name an executable, not a directory.");
        }
        else if (app == "." || app == ".." || app.IndexOfAny(new char[] { '*', '?', '"', '<', '>', '|' }) >= 0)
            throw Fail("invalid_request", "app must be an exact executable basename or absolute Windows path.");
        foreach (char c in app) if (c < 32) throw Fail("invalid_request", "app contains a control character.");
    }

    private static void ValidateElementSelector(object value)
    {
        Dictionary<string, object> selector = ObjectValue(value, "selector");
        Fields(selector, "", "name automation_id control_type");
        if (selector.Count == 0) throw Fail("invalid_request", "selector needs name, automation_id or control_type.");
        foreach (string key in selector.Keys) StringValue(selector[key], key, key == "control_type" ? 128 : 32767, true);
        if (selector.ContainsKey("control_type") && !Regex.IsMatch((string)selector["control_type"], @"\A[A-Za-z][A-Za-z0-9]*\z", RegexOptions.CultureInvariant))
            throw Fail("invalid_request", "control_type must use the exact UIA name, e.g. Button or Edit.");
    }

    private static void ValidateSearchLimits(Dictionary<string, object> p)
    { Number(p, "depth", 32, 0, 64); Number(p, "limit", 2000, 1, 20000); }

    private static Request Parse(IDictionary input)
    {
        Dictionary<string, object> r = ObjectValue(input, "request");
        Fields(r, "schema operation params", "target timeout");
        if (StringValue(r["schema"], "schema", 64, false) != Schema)
            throw Fail("invalid_request", "Unsupported request schema.");
        string op = StringValue(r["operation"], "operation", 32, false);
        if (Array.IndexOf(Operations, op) < 0) throw Fail("invalid_request", "Unsupported desktop operation.");
        Dictionary<string, object> p = ObjectValue(r["params"], "params");
        Request req = new Request { Operation = op, Params = p, Timeout = Number(r, "timeout", 20, 1, 60) };
        bool untargeted = op == "status" || op == "windows" || op == "launch";
        if (r.ContainsKey("target"))
        {
            if (untargeted) throw Fail("invalid_request", "This operation does not accept a target.");
            Dictionary<string, object> t = ObjectValue(r["target"], "target");
            Fields(t, "hwnd pid started", "");
            req.Target = new Target { Hwnd = DecimalId(t["hwnd"], "hwnd"), Pid = ProcessId(t["pid"]), Started = DecimalId(t["started"], "started") };
        }
        else if (!untargeted && op != "screenshot" && op != "capture" && op != "find" && op != "wait") throw Fail("invalid_request", "This operation requires hwnd, pid and started in target.");
        switch (op)
        {
            case "windows":
                Fields(p, "", "app title pid"); ValidateWindowFilters(p);
                break;
            case "find": case "wait":
                Fields(p, op == "wait" ? "state" : "", op == "wait" ? "app title pid selector element depth limit wait_ms poll_ms" : "app title pid selector depth limit include_hidden");
                ValidateWindowFilters(p);
                if (req.Target != null && WindowFilters(p)) throw Fail("invalid_request", "Use target or app/title/pid, not both.");
                bool elementSearch = p.ContainsKey("selector") || p.ContainsKey("element");
                if (p.ContainsKey("selector") && p.ContainsKey("element")) throw Fail("invalid_request", "Use element or selector, not both.");
                if (p.ContainsKey("selector")) ValidateElementSelector(p["selector"]);
                if (p.ContainsKey("element"))
                {
                    ParseRuntimeId(StringValue(p["element"], "element", 2048, false));
                    if (req.Target == null) throw Fail("invalid_request", "A runtime element ID requires an exact target.");
                }
                if ((op == "wait" || elementSearch) && req.Target == null && !WindowFilters(p)) throw Fail("invalid_request", "Waits and element searches need target or app/title/pid.");
                if (op == "find" && req.Target != null && !elementSearch) throw Fail("invalid_request", "find with target needs an element selector; use inspect for the tree.");
                if (elementSearch) ValidateSearchLimits(p);
                else if (p.ContainsKey("depth") || p.ContainsKey("limit")) throw Fail("invalid_request", "depth/limit apply only to UIA searches.");
                if (p.ContainsKey("include_hidden") && (elementSearch || !(p["include_hidden"] is bool))) throw Fail("invalid_request", "include_hidden is a boolean for window searches only.");
                if (op == "wait")
                {
                    string state = StringValue(p["state"], "state", 16, false);
                    if (state != "present" && state != "visible" && state != "enabled" && state != "closed") throw Fail("invalid_request", "state must be present, visible, enabled or closed.");
                    Number(p, "wait_ms", Math.Min(10000, (req.Timeout - 1) * 1000), 0, (req.Timeout - 1) * 1000);
                    Number(p, "poll_ms", 100, 20, 1000);
                }
                break;
            case "inspect":
                Fields(p, "", "depth limit"); Number(p, "depth", 3, 0, 8); Number(p, "limit", 200, 1, 500); break;
            case "screenshot":
                Fields(p, "path", "overwrite");
                if (!AbsolutePath(p["path"], "path").EndsWith(".png", StringComparison.OrdinalIgnoreCase))
                    throw Fail("invalid_request", "Screenshot path must end in .png.");
                if (p.ContainsKey("overwrite") && !(p["overwrite"] is bool)) throw Fail("invalid_request", "overwrite must be a boolean.");
                break;
            case "move":
                Fields(p, "x y width height", "");
                Integer(p["x"], "x", Int32.MinValue, Int32.MaxValue); Integer(p["y"], "y", Int32.MinValue, Int32.MaxValue);
                int width = Integer(p["width"], "width", 1, 16384), height = Integer(p["height"], "height", 1, 16384);
                if ((long)Convert.ToInt32(p["x"], Invariant) + width > Int32.MaxValue || (long)Convert.ToInt32(p["y"], Invariant) + height > Int32.MaxValue)
                    throw Fail("invalid_request", "Window rectangle exceeds the signed coordinate range.");
                break;
            case "launch":
                Fields(p, "argv", "cwd");
                IList argv = ArrayValue(p["argv"], "argv", 1, 64);
                string program = StringValue(argv[0], "argv[0]", 32760, false);
                if (program.Trim() != program || program.IndexOf('"') >= 0 || program.EndsWith(".cmd", StringComparison.OrdinalIgnoreCase) || program.EndsWith(".bat", StringComparison.OrdinalIgnoreCase))
                    throw Fail("invalid_request", "argv[0] must name an executable, without shell quoting.");
                int commandLength = QuoteArgument(program).Length;
                for (int i = 1; i < argv.Count; i++) commandLength += 1 + QuoteArgument(StringValue(argv[i], "argv", 32760, true)).Length;
                if (commandLength > 32766) throw Fail("invalid_request", "Windows command line exceeds 32766 UTF-16 code units.");
                if (p.ContainsKey("cwd")) AbsolutePath(p["cwd"], "cwd");
                break;
            case "type": Fields(p, "text", ""); InputText(p["text"]); break;
            case "key":
                Fields(p, "keys", ""); IList keys = ArrayValue(p["keys"], "keys", 1, 16);
                HashSet<ushort> unique = new HashSet<ushort>();
                foreach (object key in keys)
                    if (!unique.Add(KeyCode(StringValue(key, "keys", 16, false)))) throw Fail("invalid_request", "Duplicate key in chord.");
                break;
            case "click":
                Fields(p, "x y", "button count"); Coordinates(p);
                if (p.ContainsKey("button"))
                {
                    string button = StringValue(p["button"], "button", 6, false);
                    if (button != "left" && button != "right" && button != "middle") throw Fail("invalid_request", "Unsupported mouse button.");
                }
                Number(p, "count", 1, 1, 2); break;
            case "scroll":
                Fields(p, "x y delta", "");
                Integer(p["delta"], "delta", -1200, 1200);
                Coordinates(p); break;
            case "invoke": case "set-value":
                Fields(p, op == "invoke" ? "" : "text", "element selector depth limit");
                if (p.ContainsKey("element") == p.ContainsKey("selector")) throw Fail("invalid_request", "Require exactly one of element or selector.");
                if (p.ContainsKey("element")) ParseRuntimeId(StringValue(p["element"], "element", 2048, false));
                else ValidateElementSelector(p["selector"]);
                ValidateSearchLimits(p);
                if (op == "set-value") InputText(p["text"]);
                break;
            default: Fields(p, "", ""); break;
        }
        return req;
    }

    private static void Coordinates(Dictionary<string, object> p)
    { Integer(p["x"], "x", 0, Int32.MaxValue); Integer(p["y"], "y", 0, Int32.MaxValue); }

    public static void Validate(IDictionary request) { Parse(request); }

    // Windows CRT / CommandLineToArgvW quoting, including empty strings and trailing slashes.
    // No shell, environment expansion, interpolation, or PowerShell command construction.
    public static string QuoteArgument(string argument)
    {
        if (argument == null || argument.IndexOf('\0') >= 0) throw Fail("invalid_request", "Invalid argument.");
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in argument)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c); slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }

    public static object Execute(IDictionary request)
    {
        Request parsed = Parse(request); // No OS calls before validation is complete.
        if (Environment.OSVersion.Platform != PlatformID.Win32NT || IntPtr.Size != 8)
            throw Fail("unsupported_platform", "Desktop automation requires Windows x64.");
        return ExecuteWindows(parsed);
    }

    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
    private static object ExecuteWindows(Request parsed)
    {
        object result = null; Exception error = null;
        Runner runner = new Runner(parsed);
        Thread worker = new Thread(delegate() {
            try { using (DpiScope dpi = new DpiScope()) result = runner.Run(); }
            catch (Exception exception) { error = exception; }
        });
        // UIA must run on an MTA thread, including when PowerShell itself runs STA.
        worker.IsBackground = true; worker.SetApartmentState(ApartmentState.MTA); worker.Start();
        int budget = parsed.Timeout * 1000;
        if (parsed.Operation == "wait") budget = Math.Min(budget, Number(parsed.Params, "wait_ms", Math.Min(10000, (parsed.Timeout - 1) * 1000), 0, 59000) + 1000);
        if (!worker.Join(budget))
        {
            runner.Cancelled = true;
            if (parsed.Operation == "wait") throw Fail("wait_timeout", "Condition '" + (string)parsed.Params["state"] + "' was not confirmed within " + budget.ToString(Invariant) + " ms; a desktop/UIA observation did not return in time. No mutation was attempted.");
            throw Fail("timeout", "Desktop operation timed out; completion is unknown. Do not automatically retry mutations.");
        }
        if (error != null)
        {
            string partial = runner.MutationStarted ? " The operation may already have changed application state." : "";
            KilixDesktopException known = error as KilixDesktopException;
            if (known != null)
            {
                if (runner.MutationStarted) throw Fail(known.Code, known.Message + partial);
                throw known;
            }
            if (error is ElementNotAvailableException) throw Fail("stale_element", "The UI Automation element is no longer available." + partial);
            if (error is UnauthorizedAccessException) throw Fail("access_denied", "Windows denied access to this operation." + partial);
            if (error is Win32Exception) throw Fail("native_error", "Windows error " + ((Win32Exception)error).NativeErrorCode.ToString(Invariant) + "." + partial);
            if (error is IOException) throw Fail("io_error", "The requested filesystem operation failed." + partial);
            throw Fail("native_error", "Desktop operation failed (" + error.GetType().Name + "); completion may be unknown.");
        }
        return result;
    }

    private sealed class Runner
    {
        private readonly Request req;
        private readonly Stopwatch clock = Stopwatch.StartNew();
        public volatile bool Cancelled;
        public volatile bool MutationStarted;
        public Runner(Request request) { req = request; }
        private int Remaining { get { return Math.Max(0, req.Timeout * 1000 - (int)clock.ElapsedMilliseconds); } }
        private void Check()
        { if (Cancelled || Remaining == 0) throw Fail(req.Operation == "wait" ? "wait_timeout" : "timeout", "Operation deadline reached; the requested condition/completion was not confirmed."); }
        private void Available() { Check(); EnsureDesktop(); }

        public object Run()
        {
            Check();
            if (req.Operation == "status") return Status();
            bool mutating = req.Operation != "windows" && req.Operation != "find" && req.Operation != "wait" && req.Operation != "inspect" && req.Operation != "screenshot" && req.Operation != "capture";
            using (MutationLock gate = mutating ? new MutationLock() : null) return RunAvailable();
        }

        private object RunAvailable()
        {
            Available();
            if (req.Operation == "windows") return Windows();
            if (req.Operation == "find") return Find();
            if (req.Operation == "wait") return Wait();
            if (req.Operation == "launch") return Launch();
            if (req.Target == null) return req.Operation == "capture" ? Capture(null) : Screenshot(null);
            using (ProcessLease lease = new ProcessLease(req.Target))
            {
                Check();
                switch (req.Operation)
                {
                    case "inspect": return Inspect();
                    case "screenshot": return Screenshot(req.Target);
                    case "capture": return Capture(req.Target);
                    case "focus": case "move": case "minimize": case "maximize": case "restore": case "close":
                        lease.EnsureWritable(); return WindowAction();
                    case "type": case "key": case "click": case "scroll":
                        lease.EnsureWritable(); return Input();
                    case "invoke": case "set-value":
                        lease.EnsureWritable(); return ElementAction();
                    default: throw Fail("invalid_request", "Unsupported operation.");
                }
            }
        }

        private sealed class WindowSearch
        {
            public readonly List<Dictionary<string, object>> Rows = new List<Dictionary<string, object>>();
            public int Skipped;
            public object Json() { return Map("windows", Rows.ToArray(), "skipped", Skipped, "incomplete", Skipped != 0); }
        }

        private WindowSearch SearchWindows(bool includeHidden)
        {
            WindowSearch result = new WindowSearch();
            string title = req.Params.ContainsKey("title") ? (string)req.Params["title"] : null;
            string app = req.Params.ContainsKey("app") ? (string)req.Params["app"] : null;
            uint pid = req.Params.ContainsKey("pid") ? ProcessId(req.Params["pid"]) : 0;
            EnumerateWindows(delegate(IntPtr hwnd) {
                Check();
                if (!includeHidden && !Native.IsWindowVisible(hwnd)) return true;
                uint actual; Native.GetWindowThreadProcessId(hwnd, out actual);
                if (pid != 0 && actual != pid) return true;
                string caption = WindowTitle(hwnd);
                if (title != null && caption.IndexOf(title, StringComparison.OrdinalIgnoreCase) < 0) return true;
                try
                {
                    Dictionary<string, object> row = WindowInfo(hwnd);
                    // The handle/title can change between the cheap filter and identity acquisition.
                    if (pid != 0 && RowTarget(row).Pid != pid) return true;
                    if (title != null && ((string)row["title"]).IndexOf(title, StringComparison.OrdinalIgnoreCase) < 0) return true;
                    if (app != null)
                    {
                        string path = row["process_path"] as string;
                        if (path == null) { result.Skipped++; return true; }
                        if (!MatchesApp(app, path)) return true;
                    }
                    result.Rows.Add(row);
                }
                catch (KilixDesktopException) { result.Skipped++; }
                return true;
            });
            Available();
            return result;
        }

        private object Windows() { return SearchWindows(false).Json(); }

        private static Target RowTarget(Dictionary<string, object> row)
        {
            Dictionary<string, object> t = (Dictionary<string, object>)row["target"];
            return new Target { Hwnd = DecimalId(t["hwnd"], "hwnd"), Pid = ProcessId(t["pid"]), Started = DecimalId(t["started"], "started") };
        }

        private static void UniqueWindowSearch(WindowSearch search)
        {
            if (search.Rows.Count > 1) throw Fail("ambiguous_target", "Multiple windows match app/title/pid; narrow the selector.");
            if (search.Skipped != 0) throw Fail("search_incomplete", "Window search could not establish uniqueness/absence; some candidate identities or executable paths were unavailable.");
        }

        private object Find()
        {
            if (!req.Params.ContainsKey("selector"))
                return SearchWindows(req.Params.ContainsKey("include_hidden") && (bool)req.Params["include_hidden"]).Json();
            if (req.Target == null)
            {
                WindowSearch windows = SearchWindows(false); UniqueWindowSearch(windows);
                if (windows.Rows.Count == 0) throw Fail("window_not_found", "No visible window matches app/title/pid.");
                req.Target = RowTarget(windows.Rows[0]);
            }
            using (ProcessLease lease = new ProcessLease(req.Target))
            {
                ElementSearch elements = SearchElements();
                Available(); VerifyTarget(req.Target);
                return Map("target", req.Target.Json(), "elements", elements.Rows.ToArray(), "visited", elements.Visited,
                    "incomplete", elements.Incomplete, "truncated", elements.Truncated);
            }
        }

        private object Wait()
        {
            string state = (string)req.Params["state"];
            int wait = Number(req.Params, "wait_ms", Math.Min(10000, (req.Timeout - 1) * 1000), 0, 59000);
            int interval = Number(req.Params, "poll_ms", 100, 20, 1000), polls = 0;
            long start = clock.ElapsedMilliseconds, end = start + wait;
            string last = "not_observed";
            bool elementSearch = req.Params.ContainsKey("selector") || req.Params.ContainsKey("element");
            do
            {
                Available(); polls++;
                object window = null, element = null;
                bool complete = true, present = false, visible = false, enabled = false;
                try
                {
                    if (req.Target == null)
                    {
                        WindowSearch windows = SearchWindows(true);
                        if (windows.Rows.Count > 1) throw Fail("ambiguous_target", "Multiple windows match the wait selector; narrow app/title/pid.");
                        if (windows.Skipped != 0) { complete = false; last = "window_search_incomplete"; }
                        else if (windows.Rows.Count == 1) req.Target = RowTarget(windows.Rows[0]);
                    }
                    if (complete && req.Target != null)
                    {
                        if (Native.IsWindow(req.Target.Handle))
                        {
                            // A reused handle or process identity is an error, never a replacement to follow.
                            VerifyTarget(req.Target);
                            Dictionary<string, object> row = WindowInfo(req.Target.Handle);
                            window = row;
                            present = true; visible = (bool)row["visible"] && !(bool)row["minimized"]; enabled = (bool)row["enabled"];
                            if (elementSearch)
                            {
                                ElementSearch elements = SearchElements();
                                if (elements.Rows.Count > 1) throw Fail("ambiguous_element", "Multiple UIA elements match the wait selector; narrow it.");
                                complete = !elements.Incomplete && !elements.Truncated;
                                if (!complete) last = "accessibility_incomplete (visited " + elements.Visited.ToString(Invariant) + ", truncated=" + elements.Truncated.ToString() + ")";
                                present = elements.Rows.Count == 1;
                                if (present)
                                {
                                    Dictionary<string, object> found = elements.Rows[0]; element = found;
                                    visible = visible && !(bool)found["offscreen"]; enabled = enabled && (bool)found["enabled"];
                                }
                            }
                            VerifyTarget(req.Target);
                        }
                        else if (state != "closed") throw Fail("stale_target", "The bound window disappeared while waiting; the wait will not follow a replacement.");
                    }
                }
                catch (Exception e)
                {
                    KilixDesktopException targetError = e as KilixDesktopException;
                    bool observationFailed = ProviderFailure(e) || (targetError != null &&
                        (targetError.Code == "stale_target" || targetError.Code == "native_error" || targetError.Code == "access_denied"));
                    if (state == "closed" && req.Target != null && observationFailed && !Native.IsWindow(req.Target.Handle))
                    {
                        // The exact target may close during enumeration/verification, too.
                        // An existing reused HWND still fails the identity checks above.
                        complete = true; present = false; window = null; element = null;
                    }
                    else
                    {
                        if (!ProviderFailure(e)) throw;
                        complete = false; last = "provider_unavailable (" + e.GetType().Name + ")";
                    }
                }
                if (complete)
                {
                    last = !present ? "absent" : !visible ? "not_visible" : !enabled ? "disabled" : "enabled";
                    bool matched = state == "closed" ? !present : state == "present" ? present : state == "visible" ? present && visible : present && visible && enabled;
                    if (matched)
                    {
                        Available();
                        return Map("state", state, "matched", true, "polls", polls, "elapsed_ms", clock.ElapsedMilliseconds - start,
                            "target", req.Target == null ? null : req.Target.Json(), "window", window, "element", element);
                    }
                }
                long remaining = end - clock.ElapsedMilliseconds;
                if (remaining <= 0) break;
                Thread.Sleep((int)Math.Min(interval, remaining));
            } while (true);
            throw Fail("wait_timeout", "Condition '" + state + "' was not confirmed after " + (clock.ElapsedMilliseconds - start).ToString(Invariant) +
                " ms (" + polls.ToString(Invariant) + " observations); last=" + last + ". No mutation was attempted.");
        }

        private object WindowAction()
        {
            Target target = req.Target; IntPtr hwnd = target.Handle;
            Available(); VerifyTarget(target);
            string op = req.Operation;
            if (op == "close")
            {
                MutationStarted = true;
                if (!Native.PostMessage(hwnd, 0x0010, UIntPtr.Zero, IntPtr.Zero)) NativeFailure("WM_CLOSE");
                return Map("target", target.Json(), "requested", true, "completion", "wm_close_posted");
            }
            if (op == "focus")
            {
                if (Native.IsIconic(hwnd))
                {
                    MutationStarted = true;
                    if (!Native.ShowWindowAsync(hwnd, 9)) NativeFailure("Restore");
                    WaitWindow(delegate { return !Native.IsIconic(hwnd); }, "restore");
                }
                Available(); VerifyTarget(target);
                // AttachThreadInput fails when the calling MTA has no message queue yet.
                Native.MSG message;
                Native.PeekMessage(out message, IntPtr.Zero, 0, 0, 0);
                uint current = Native.GetCurrentThreadId(), foregroundPid;
                uint foreground = Native.GetWindowThreadProcessId(Native.GetForegroundWindow(), out foregroundPid);
                bool attached = foreground != 0 && foreground != current && Native.AttachThreadInput(current, foreground, true);
                uint targetPid;
                uint targetThread = Native.GetWindowThreadProcessId(hwnd, out targetPid);
                bool targetAttached = targetThread != current && targetThread != foreground && Native.AttachThreadInput(current, targetThread, true);
                try
                {
                    Available(); VerifyTarget(target);
                    MutationStarted = true;
                    Native.BringWindowToTop(hwnd);
                    Native.SetForegroundWindow(hwnd);
                    Native.SetActiveWindow(hwnd);
                }
                finally
                {
                    if (targetAttached) Native.AttachThreadInput(current, targetThread, false);
                    if (attached) Native.AttachThreadInput(current, foreground, false);
                }
                WaitWindow(delegate { return Native.GetForegroundWindow() == hwnd; }, "focus");
                return Map("target", target.Json(), "foreground", true);
            }
            if (op == "move")
            {
                if (Native.IsIconic(hwnd) || Native.IsZoomed(hwnd)) throw Fail("invalid_window_state", "Restore the window before moving it.");
                int x = Convert.ToInt32(req.Params["x"], Invariant), y = Convert.ToInt32(req.Params["y"], Invariant);
                int width = Convert.ToInt32(req.Params["width"], Invariant), height = Convert.ToInt32(req.Params["height"], Invariant);
                // Async positioning avoids hanging on an unresponsive application's message loop.
                MutationStarted = true;
                if (!Native.SetWindowPos(hwnd, IntPtr.Zero, x, y, width, height, 0x4000 | 0x0010 | 0x0004)) NativeFailure("SetWindowPos");
                WaitWindow(delegate {
                    Native.RECT rect = WindowRect(hwnd);
                    // DPI-unaware applications round through logical pixels.
                    // At 150%, a requested 700px width can become 701px. Return
                    // the observed rectangle while tolerating that rounding.
                    return Math.Abs((long)rect.Left - x) <= 1 && Math.Abs((long)rect.Top - y) <= 1 &&
                        Math.Abs((long)rect.Right - rect.Left - width) <= 1 && Math.Abs((long)rect.Bottom - rect.Top - height) <= 1;
                }, "move (the application may constrain its size)");
            }
            else
            {
                int command = op == "minimize" ? 6 : op == "maximize" ? 3 : 9;
                MutationStarted = true;
                if (!Native.ShowWindowAsync(hwnd, command)) NativeFailure("ShowWindowAsync");
                WaitWindow(delegate { return op == "minimize" ? Native.IsIconic(hwnd) : op == "maximize" ? Native.IsZoomed(hwnd) : !Native.IsIconic(hwnd) && !Native.IsZoomed(hwnd); }, op);
            }
            return Map("target", target.Json(), "rect", RectJson(WindowRect(hwnd)), "minimized", Native.IsIconic(hwnd), "maximized", Native.IsZoomed(hwnd));
        }

        private void WaitWindow(Func<bool> condition, string operation)
        {
            long end = clock.ElapsedMilliseconds + Math.Min(2000, Math.Max(0, Remaining - 100));
            do
            {
                Available(); VerifyTarget(req.Target);
                if (condition()) return;
                Thread.Sleep(25);
            } while (clock.ElapsedMilliseconds < end);
            throw Fail("not_completed", "Windows did not confirm " + operation + "; the request may still be pending.");
        }

        private object Launch()
        {
            IList argv = (IList)req.Params["argv"];
            StringBuilder arguments = new StringBuilder();
            for (int i = 1; i < argv.Count; i++) { if (i > 1) arguments.Append(' '); arguments.Append(QuoteArgument((string)argv[i])); }
            ProcessStartInfo info = new ProcessStartInfo((string)argv[0], arguments.ToString());
            info.UseShellExecute = false;
            if (req.Params.ContainsKey("cwd")) info.WorkingDirectory = (string)req.Params["cwd"];
            Available();
            MutationStarted = true;
            using (Process process = Process.Start(info))
            {
                if (process == null) throw Fail("launch_failed", "Windows did not return a process handle.");
                uint pid = (uint)process.Id; ulong started = ProcessStarted(process.Handle);
                Target found = null; bool exited = false;
                long end = clock.ElapsedMilliseconds + Math.Min(5000, Math.Max(0, Remaining - 250));
                do
                {
                    if (Cancelled) break;
                    exited = process.HasExited;
                    if (exited) break;
                    EnumerateWindows(delegate(IntPtr hwnd) {
                        uint actual; Native.GetWindowThreadProcessId(hwnd, out actual);
                        if (actual == pid && Native.IsWindowVisible(hwnd))
                        {
                            Target candidate = new Target { Hwnd = unchecked((ulong)hwnd.ToInt64()), Pid = pid, Started = started };
                            try { VerifyTarget(candidate); found = candidate; return false; }
                            catch (KilixDesktopException) { }
                        }
                        return true;
                    });
                    if (found != null || clock.ElapsedMilliseconds >= end) break;
                    Thread.Sleep(50);
                } while (clock.ElapsedMilliseconds < end);
                return Map("pid", pid, "started", started.ToString(Invariant), "target", found == null ? null : found.Json(),
                    "window_found", found != null, "exited", exited, "completion", "process_started");
            }
        }

        private void GuardInput()
        {
            Available(); VerifyTarget(req.Target);
            if (!Native.IsWindowVisible(req.Target.Handle) || Native.IsIconic(req.Target.Handle)) throw Fail("invalid_window_state", "Input requires a visible, restored target.");
            if (Native.GetForegroundWindow() != req.Target.Handle) throw Fail("foreground_mismatch", "The target is not the foreground window. Focus it explicitly before input.");
        }

        private object Input()
        {
            GuardInput();
            // Never release or borrow a modifier/button currently held by the user.
            foreach (int key in new int[] { 0x10, 0x11, 0x12, 0x5B, 0x5C, 1, 2, 4 })
                if ((Native.GetAsyncKeyState(key) & 0x8000) != 0) throw Fail("input_busy", "A modifier key or mouse button is already held down.");
            List<Native.INPUT> events = new List<Native.INPUT>();
            Native.POINT? mousePoint = null;
            Native.RECT mouseRect = new Native.RECT();
            string op = req.Operation;
            if (op == "type")
            {
                foreach (char c in (string)req.Params["text"])
                {
                    events.Add(Keyboard(0, c, 0x0004)); events.Add(Keyboard(0, c, 0x0004 | 0x0002));
                }
            }
            else if (op == "key")
            {
                IList keys = (IList)req.Params["keys"];
                foreach (object value in keys)
                {
                    ushort key = KeyCode((string)value);
                    if ((Native.GetAsyncKeyState(key) & 0x8000) != 0) throw Fail("input_busy", "A requested key is already held down.");
                    events.Add(Keyboard(key, 0, ExtendedKey(key) ? 1u : 0u));
                }
                for (int i = keys.Count - 1; i >= 0; i--)
                {
                    ushort key = KeyCode((string)keys[i]); events.Add(Keyboard(key, 0, 2u | (ExtendedKey(key) ? 1u : 0u)));
                }
            }
            else
            {
                Native.RECT rect = WindowRect(req.Target.Handle);
                int x = Number(req.Params, "x", (rect.Right - rect.Left) / 2, 0, Int32.MaxValue);
                int y = Number(req.Params, "y", (rect.Bottom - rect.Top) / 2, 0, Int32.MaxValue);
                if (x >= (long)rect.Right - rect.Left || y >= (long)rect.Bottom - rect.Top)
                    throw Fail("invalid_coordinates", "Mouse coordinates are outside the target window.");
                Native.POINT point = new Native.POINT { X = checked(rect.Left + x), Y = checked(rect.Top + y) };
                mousePoint = point; mouseRect = rect;
                Native.RECT desktop = DesktopRect();
                if (point.X < desktop.Left || point.Y < desktop.Top || point.X >= desktop.Right || point.Y >= desktop.Bottom)
                    throw Fail("invalid_coordinates", "Mouse coordinates are outside the visible desktop.");
                IntPtr hit = Native.WindowFromPoint(point);
                if (hit == IntPtr.Zero || Native.GetAncestor(hit, 2) != req.Target.Handle)
                    throw Fail("window_occluded", "The window at the requested point does not belong to the target.");
                Native.INPUT move = Mouse(0x8000 | 0x4000 | 0x0001, 0);
                // Use the center of the physical pixel in the 65536-unit virtual-desktop space.
                move.Data.Mouse.dx = (int)(((long)(point.X - desktop.Left) * 65536 + 32768) / (desktop.Right - desktop.Left));
                move.Data.Mouse.dy = (int)(((long)(point.Y - desktop.Top) * 65536 + 32768) / (desktop.Bottom - desktop.Top));
                events.Add(move);
                if (op == "scroll") events.Add(Mouse(0x0800, unchecked((uint)Convert.ToInt32(req.Params["delta"], Invariant))));
                else
                {
                    string button = req.Params.ContainsKey("button") ? (string)req.Params["button"] : "left";
                    uint down = button == "left" ? 0x0002u : button == "right" ? 0x0008u : 0x0020u;
                    for (int i = 0; i < Number(req.Params, "count", 1, 1, 2); i++)
                    { events.Add(Mouse(down, 0)); events.Add(Mouse(down * 2, 0)); }
                }
            }
            GuardInput();
            if (mousePoint.HasValue)
            {
                Native.POINT point = mousePoint.Value;
                Native.RECT current = WindowRect(req.Target.Handle), clip;
                if (current.Left != mouseRect.Left || current.Top != mouseRect.Top || current.Right != mouseRect.Right || current.Bottom != mouseRect.Bottom)
                    throw Fail("window_moved", "The target moved while preparing input; no input was submitted.");
                if (!Native.GetClipCursor(out clip)) NativeFailure("GetClipCursor");
                if (point.X < clip.Left || point.X >= clip.Right || point.Y < clip.Top || point.Y >= clip.Bottom)
                    throw Fail("cursor_clipped", "The requested point is outside the current cursor confinement rectangle.");
                if (Native.GetAncestor(Native.WindowFromPoint(point), 2) != req.Target.Handle)
                    throw Fail("window_occluded", "The requested point no longer belongs to the target window.");
            }
            if (events.Count == 0) return Map("target", req.Target.Json(), "submitted", true, "events", 0);
            Native.INPUT[] input = events.ToArray();
            MutationStarted = true;
            uint sent = Native.SendInput((uint)input.Length, input, Marshal.SizeOf(typeof(Native.INPUT)));
            if (sent != input.Length)
            {
                int error = Marshal.GetLastWin32Error();
                bool released = ReleasePartial(input, sent);
                throw Fail(sent == 0 ? "input_blocked" : "partial_input", "SendInput inserted " + sent.ToString(Invariant) + " of " + input.Length.ToString(Invariant) +
                    " events (Windows error " + error.ToString(Invariant) + "). " + (released ? "Inserted key/button downs were released." : "Some inserted key/button downs may remain pressed.") + " Do not automatically retry.");
            }
            return Map("target", req.Target.Json(), "submitted", true, "events", sent, "completion", "input_submitted");
        }

        private Native.RECT CaptureBounds(Target target)
        {
            Native.RECT bounds = DesktopRect();
            if (target != null)
            {
                VerifyTarget(target);
                if (!Native.IsWindowVisible(target.Handle) || Native.IsIconic(target.Handle)) throw Fail("invalid_window_state", "A minimized or hidden window cannot be captured from the screen.");
                Native.RECT window = WindowRect(target.Handle);
                bounds.Left = Math.Max(bounds.Left, window.Left); bounds.Top = Math.Max(bounds.Top, window.Top);
                bounds.Right = Math.Min(bounds.Right, window.Right); bounds.Bottom = Math.Min(bounds.Bottom, window.Bottom);
            }
            long width = (long)bounds.Right - bounds.Left, height = (long)bounds.Bottom - bounds.Top;
            if (width <= 0 || height <= 0) throw Fail("invalid_window_state", "The target has no visible screen bounds.");
            if (width > 16384 || height > 16384 || width * height > 67108864) throw Fail("capture_limit", "Capture exceeds 16384 pixels per dimension or 64 megapixels.");
            return bounds;
        }

        private void CapturePng(Target target, Native.RECT bounds, Stream stream)
        {
            Available(); if (target != null) VerifyTarget(target);
            using (Bitmap bitmap = new Bitmap(bounds.Right - bounds.Left, bounds.Bottom - bounds.Top, PixelFormat.Format32bppRgb))
            {
                CaptureScreen(bitmap, bounds);
                Available(); if (target != null) VerifyTarget(target);
                bitmap.Save(stream, ImageFormat.Png);
            }
            Available();
        }

        private object Capture(Target target)
        {
            Native.RECT bounds = CaptureBounds(target);
            using (BoundedPngStream stream = new BoundedPngStream())
            {
                try { CapturePng(target, bounds, stream); }
                catch (Exception)
                {
                    // GDI+ can wrap a Stream.Write exception as ExternalException.
                    if (stream.Exceeded) throw Fail("capture_limit", "Encoded PNG exceeds 32 MiB.");
                    throw;
                }
                if (stream.Exceeded) throw Fail("capture_limit", "Encoded PNG exceeds 32 MiB.");
                byte[] png = stream.ToArray();
                string hash;
                using (SHA256 sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(png)).Replace("-", "").ToLowerInvariant();
                Check();
                return Map("png_base64", Convert.ToBase64String(png), "byte_length", png.Length, "sha256", hash,
                    "bounds", RectJson(bounds), "capture", "screen", "occlusion_possible", true);
            }
        }

        private object Screenshot(Target target)
        {
            string path = Path.GetFullPath((string)req.Params["path"]);
            bool overwrite = req.Params.ContainsKey("overwrite") && (bool)req.Params["overwrite"];
            if (!Directory.Exists(Path.GetDirectoryName(path))) throw Fail("invalid_path", "Screenshot parent directory does not exist.");
            if (!overwrite && File.Exists(path)) throw Fail("file_exists", "Screenshot already exists; choose another path or request overwrite.");
            Native.RECT bounds = CaptureBounds(target);
            string temporary = Path.Combine(Path.GetDirectoryName(path), ".kilix-capture-" + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                using (FileStream stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) CapturePng(target, bounds, stream);
                Available();
                if (overwrite && File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path); // Atomic and fails if another writer won the destination.
            }
            catch (IOException)
            {
                if (!overwrite && File.Exists(path)) throw Fail("file_exists", "Screenshot already exists; it was not overwritten.");
                throw;
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
            return Map("path", path, "bounds", RectJson(bounds), "capture", "screen", "occlusion_possible", true);
        }

        private sealed class Node
        {
            public AutomationElement Element; public int Depth; public string Parent;
            public Node(AutomationElement element, int depth, string parent) { Element = element; Depth = depth; Parent = parent; }
        }

        private object Inspect()
        {
            List<object> nodes = new List<object>(); bool incomplete = false, truncated = false;
            int maxDepth = Number(req.Params, "depth", 3, 0, 8), limit = Number(req.Params, "limit", 200, 1, 500);
            AutomationElement root;
            try { root = AutomationElement.FromHandle(req.Target.Handle); }
            catch (Exception e) { if (!ProviderFailure(e)) throw; root = null; incomplete = true; }
            if (root != null)
            {
                Stack<Node> stack = new Stack<Node>(); stack.Push(new Node(root, 0, null));
                HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
                int visited = 0;
                while (stack.Count > 0 && nodes.Count < limit && visited < 2000)
                {
                    Available(); VerifyTarget(req.Target); visited++;
                    Node item = stack.Pop(); string id = null;
                    try
                    {
                        id = RuntimeId(item.Element);
                        if (id != null && !seen.Add(id)) { incomplete = true; continue; }
                        if (id == null) incomplete = true;
                        else nodes.Add(NodeJson(item.Element, item.Depth, item.Parent, id));
                    }
                    catch (Exception e) { if (!ProviderFailure(e)) throw; incomplete = true; }
                    if (item.Depth >= maxDepth) { truncated = true; continue; }
                    try
                    {
                        // Raw view includes the nodes a CEF provider actually exposes. No inferred controls.
                        List<AutomationElement> children = new List<AutomationElement>();
                        AutomationElement child = TreeWalker.RawViewWalker.GetFirstChild(item.Element);
                        while (child != null && children.Count < limit && visited + children.Count < 2000)
                        {
                            Check(); children.Add(child); child = TreeWalker.RawViewWalker.GetNextSibling(child);
                        }
                        if (child != null) truncated = true;
                        for (int i = children.Count - 1; i >= 0; i--) stack.Push(new Node(children[i], item.Depth + 1, id));
                    }
                    catch (Exception e) { if (!ProviderFailure(e)) throw; incomplete = true; }
                }
                if (stack.Count > 0) truncated = true;
            }
            Available(); VerifyTarget(req.Target);
            return Map("target", req.Target.Json(), "nodes", nodes.ToArray(), "truncated", truncated, "incomplete", incomplete,
                "accessibility", nodes.Count > 1 ? "available" : "limited",
                "note", "Only provider-exposed UIA nodes are returned; some applications expose little or no accessibility tree.");
        }

        private sealed class ElementSearch
        {
            public readonly List<Dictionary<string, object>> Rows = new List<Dictionary<string, object>>();
            public AutomationElement Element;
            public int Visited;
            public bool Incomplete, Truncated;
        }

        private bool MatchesElement(AutomationElement.AutomationElementInformation info, string id)
        {
            if (req.Params.ContainsKey("element")) return id == (string)req.Params["element"];
            Dictionary<string, object> selector = ObjectValue(req.Params["selector"], "selector");
            return MatchesElementFields(selector, info.IsPassword ? null : info.Name, info.AutomationId,
                info.ControlType.ProgrammaticName.Replace("ControlType.", ""));
        }

        private ElementSearch SearchElements()
        {
            ElementSearch result = new ElementSearch();
            int maxDepth = Number(req.Params, "depth", 32, 0, 64);
            int limit = Number(req.Params, "limit", req.Operation == "invoke" || req.Operation == "set-value" ? 20000 : 2000, 1, 20000);
            AutomationElement root;
            try { root = AutomationElement.FromHandle(req.Target.Handle); }
            catch (Exception e) { if (!ProviderFailure(e)) throw; result.Incomplete = true; return result; }
            if (root == null) { result.Incomplete = true; return result; }
            Stack<Node> stack = new Stack<Node>(); stack.Push(new Node(root, 0, null));
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            while (stack.Count > 0 && result.Visited < limit)
            {
                Available(); VerifyTarget(req.Target);
                Node item = stack.Pop(); result.Visited++;
                try
                {
                    string id = RuntimeId(item.Element);
                    if (id == null || !seen.Add(id)) { result.Incomplete = true; continue; }
                    AutomationElement.AutomationElementInformation info = item.Element.Current;
                    if (MatchesElement(info, id))
                    {
                        // Read no ValuePattern/TextPattern contents during selection or waits.
                        bool password = info.IsPassword || item.Element.Current.IsPassword;
                        result.Rows.Add(Map("id", id, "name", password ? "" : info.Name, "automation_id", info.AutomationId,
                            "control_type", info.ControlType.ProgrammaticName.Replace("ControlType.", ""), "depth", item.Depth,
                            "enabled", info.IsEnabled, "offscreen", info.IsOffscreen, "password", password));
                        result.Element = item.Element;
                    }
                    // Check for children at the boundary: a depth limit is not proof of absence/uniqueness.
                    AutomationElement child = TreeWalker.RawViewWalker.GetFirstChild(item.Element);
                    if (item.Depth >= maxDepth) { if (child != null) result.Truncated = true; continue; }
                    while (child != null && stack.Count + result.Visited < limit)
                    { Check(); stack.Push(new Node(child, item.Depth + 1, null)); child = TreeWalker.RawViewWalker.GetNextSibling(child); }
                    if (child != null) result.Truncated = true;
                }
                catch (Exception e) { if (!ProviderFailure(e)) throw; result.Incomplete = true; }
            }
            if (stack.Count > 0) result.Truncated = true;
            return result;
        }

        private void GuardElement(AutomationElement found, string id)
        {
            Available(); VerifyTarget(req.Target);
            if (!Native.IsWindowVisible(req.Target.Handle) || Native.IsIconic(req.Target.Handle)) throw Fail("invalid_window_state", "UIA actions require a visible, restored window.");
            if (!Native.IsWindowEnabled(req.Target.Handle)) throw Fail("element_disabled", "The target window is disabled.");
            if (RuntimeId(found) != id) throw Fail("stale_element", "The UIA element identity changed.");
            EnsureElementInWindow(found);
            AutomationElement.AutomationElementInformation current = found.Current;
            if (!MatchesElement(current, id)) throw Fail("stale_element", "The UIA element no longer matches the selector.");
            if (!current.IsEnabled) throw Fail("element_disabled", "The UIA element is disabled.");
            if (current.IsOffscreen) throw Fail("element_offscreen", "The UIA element is offscreen; make it visible explicitly before acting.");
            if (current.IsPassword) throw Fail("password_element", "Password elements are not available to UIA invoke or set-value.");
            Available(); VerifyTarget(req.Target);
        }

        private object ElementAction()
        {
            ElementSearch search = SearchElements();
            if (search.Rows.Count > 1) throw Fail("ambiguous_element", "Multiple UIA elements match; narrow the selector. No action was attempted.");
            if (search.Incomplete || search.Truncated) throw Fail("accessibility_incomplete", "The UIA search hit a limit or provider failure; uniqueness/absence is unknown. No action was attempted.");
            if (search.Rows.Count == 0) throw Fail(req.Params.ContainsKey("element") ? "stale_element" : "element_not_found", "No UIA element matches in this window; find/inspect again.");
            AutomationElement found = search.Element; string requested = (string)search.Rows[0]["id"];
            GuardElement(found, requested);
            object pattern;
            if (req.Operation == "invoke")
            {
                if (!found.TryGetCurrentPattern(InvokePattern.Pattern, out pattern)) throw Fail("unsupported_pattern", "The element does not support InvokePattern.");
                GuardElement(found, requested);
                MutationStarted = true; ((InvokePattern)pattern).Invoke();
            }
            else
            {
                if (!found.TryGetCurrentPattern(ValuePattern.Pattern, out pattern)) throw Fail("unsupported_pattern", "The element does not support ValuePattern.");
                ValuePattern value = (ValuePattern)pattern;
                if (value.Current.IsReadOnly) throw Fail("read_only_element", "The element value is read-only.");
                // Never read ValuePattern.Value, TextPattern text, or password contents.
                GuardElement(found, requested);
                MutationStarted = true;
                value.SetValue((string)req.Params["text"]);
            }
            return Map("target", req.Target.Json(), "element", requested, "completion", "provider_returned");
        }

        private void EnsureElementInWindow(AutomationElement element)
        {
            AutomationElement currentRoot = AutomationElement.FromHandle(req.Target.Handle);
            for (int depth = 0; element != null && depth <= 64; depth++)
            {
                Check();
                if (Automation.Compare(element, currentRoot)) return;
                element = TreeWalker.RawViewWalker.GetParent(element);
            }
            throw Fail("stale_element", "The UIA element no longer belongs to the target window.");
        }
    }

    private sealed class MutationLock : IDisposable
    {
        private Mutex mutex;
        public MutationLock()
        {
            string sid;
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent()) sid = identity.User.Value;
            mutex = new Mutex(false, @"Local\Kilix.Desktop.v1." + sid);
            try
            {
                // Do not queue input behind another caller's focus/window changes.
                if (!mutex.WaitOne(0)) throw Fail("desktop_busy", "Another desktop mutation is in progress for this user and session.");
            }
            catch (AbandonedMutexException)
            {
                mutex.ReleaseMutex(); mutex.Dispose(); mutex = null;
                throw Fail("previous_operation_incomplete", "A previous desktop worker ended during an operation; inspect current state before another mutation.");
            }
            catch { mutex.Dispose(); mutex = null; throw; }
        }
        public void Dispose() { if (mutex != null) { mutex.ReleaseMutex(); mutex.Dispose(); mutex = null; } }
    }

    private sealed class BoundedPngStream : MemoryStream
    {
        public bool Exceeded { get; private set; }
        private void Bound(long length)
        {
            if (length > MaxCaptureBytes)
            { Exceeded = true; throw Fail("capture_limit", "Encoded PNG exceeds 32 MiB."); }
        }
        public override void Write(byte[] buffer, int offset, int count)
        { Bound(Position + (long)count); base.Write(buffer, offset, count); }
        public override void WriteByte(byte value)
        { Bound(Position + 1); base.WriteByte(value); }
        public override void SetLength(long value)
        { Bound(value); base.SetLength(value); }
    }

    private static void CaptureScreen(Bitmap bitmap, Native.RECT bounds)
    {
        // Graphics.CopyFromScreen rejects combined raster flags on .NET Framework.
        // BitBlt supports SRCCOPY | CAPTUREBLT, including layered windows.
        IntPtr screen = Native.GetDC(IntPtr.Zero);
        if (screen == IntPtr.Zero) NativeFailure("GetDC");
        try
        {
            using (Graphics graphics = Graphics.FromImage(bitmap))
            {
                IntPtr destination = graphics.GetHdc();
                try
                {
                    if (!Native.BitBlt(destination, 0, 0, bitmap.Width, bitmap.Height, screen, bounds.Left, bounds.Top, 0x40CC0020)) NativeFailure("BitBlt");
                }
                finally { graphics.ReleaseHdc(destination); }
            }
        }
        finally { Native.ReleaseDC(IntPtr.Zero, screen); }
    }

    private static bool ProviderFailure(Exception e)
    { return e is ElementNotAvailableException || e is InvalidOperationException || e is COMException || e is UnauthorizedAccessException; }

    private static bool MatchesApp(string app, string processPath)
    {
        string expected = app.Replace('/', '\\'), actual = processPath.Replace('/', '\\');
        if (expected.IndexOf('\\') < 0) actual = actual.Substring(actual.LastIndexOf('\\') + 1);
        return String.Equals(expected, actual, StringComparison.OrdinalIgnoreCase);
    }

    private static bool MatchesElementFields(Dictionary<string, object> selector, string name, string automationId, string controlType)
    {
        return (!selector.ContainsKey("name") || String.Equals((string)selector["name"], name, StringComparison.Ordinal)) &&
            (!selector.ContainsKey("automation_id") || String.Equals((string)selector["automation_id"], automationId, StringComparison.Ordinal)) &&
            (!selector.ContainsKey("control_type") || String.Equals((string)selector["control_type"], controlType, StringComparison.Ordinal));
    }

    private static object NodeJson(AutomationElement element, int depth, string parent, string id)
    {
        AutomationElement.AutomationElementInformation info = element.Current;
        bool password = info.IsPassword;
        string name = password ? "" : info.Name;
        string value = null; bool valueTruncated = false, valueUnavailable = false;
        if (!password)
        {
            try
            {
                object pattern;
                if (element.TryGetCurrentPattern(ValuePattern.Pattern, out pattern))
                {
                    bool redacted;
                    value = ReadInspectableValue(delegate { return element.Current.IsPassword; },
                        delegate { return ((ValuePattern)pattern).Current.Value; }, out redacted, out valueTruncated);
                    password = redacted;
                }
                // Also redact a name if a non-ValuePattern element changed to a password field.
                password = password || element.Current.IsPassword;
            }
            catch (Exception e)
            {
                if (!ProviderFailure(e)) throw;
                value = null; valueUnavailable = true;
                // Failure to verify password state must not disclose a stale name/value.
                password = true;
            }
        }
        if (password) { name = ""; value = null; valueTruncated = false; }
        List<string> patterns = new List<string>();
        foreach (AutomationPattern pattern in element.GetSupportedPatterns())
        {
            string patternName = pattern.ProgrammaticName;
            if (password && (pattern == ValuePattern.Pattern || pattern == TextPattern.Pattern)) continue;
            if (patternName.EndsWith("Identifiers.Pattern", StringComparison.Ordinal)) patternName = patternName.Substring(0, patternName.Length - "Identifiers.Pattern".Length);
            patterns.Add(patternName);
        }
        System.Windows.Rect rect = info.BoundingRectangle;
        object rectangle = rect.IsEmpty || Double.IsInfinity(rect.X) || Double.IsInfinity(rect.Y) ? null :
            (object)Map("x", rect.X, "y", rect.Y, "width", rect.Width, "height", rect.Height);
        return Map("id", id, "parent", parent, "depth", depth, "name", name,
            "automation_id", info.AutomationId, "control_type", info.ControlType.ProgrammaticName.Replace("ControlType.", ""),
            "rect", rectangle, "enabled", info.IsEnabled, "offscreen", info.IsOffscreen,
            "patterns", patterns.ToArray(), "password", password, "redacted", password,
            "value", value, "value_truncated", valueTruncated, "value_unavailable", valueUnavailable);
    }

    private static string ReadInspectableValue(Func<bool> isPassword, Func<string> readValue, out bool redacted, out bool truncated)
    {
        redacted = isPassword(); truncated = false;
        if (redacted) return null; // A known password provider's value getter is never called.
        string value = readValue();
        redacted = isPassword();
        if (redacted || value == null) return null;
        if (value.Length <= 4096) return value;
        truncated = true;
        int length = Char.IsHighSurrogate(value[4095]) ? 4095 : 4096;
        return value.Substring(0, length);
    }

    private static int[] ParseRuntimeId(string id)
    {
        string[] parts = id.Split('.');
        if (parts.Length == 0 || parts.Length > 128) throw Fail("invalid_request", "Invalid UIA runtime ID.");
        int[] numbers = new int[parts.Length];
        for (int i = 0; i < parts.Length; i++)
            if (!Int32.TryParse(parts[i], NumberStyles.AllowLeadingSign, Invariant, out numbers[i]) || numbers[i].ToString(Invariant) != parts[i])
                throw Fail("invalid_request", "Invalid UIA runtime ID.");
        return numbers;
    }

    private static string RuntimeId(AutomationElement element)
    {
        int[] id = element.GetRuntimeId();
        if (id == null || id.Length == 0) return null;
        StringBuilder text = new StringBuilder();
        foreach (int part in id) { if (text.Length > 0) text.Append('.'); text.Append(part.ToString(Invariant)); }
        return text.ToString();
    }

    private static ushort KeyCode(string key)
    {
        key = key.ToUpperInvariant();
        if (key.Length == 1 && ((key[0] >= 'A' && key[0] <= 'Z') || (key[0] >= '0' && key[0] <= '9'))) return key[0];
        int function;
        if (key.Length >= 2 && key[0] == 'F' && Int32.TryParse(key.Substring(1), out function) && function >= 1 && function <= 12 && key == "F" + function.ToString(Invariant)) return (ushort)(0x70 + function - 1);
        switch (key)
        {
            case "CTRL": return 0x11; case "SHIFT": return 0x10; case "ALT": return 0x12; case "WIN": return 0x5B;
            case "ENTER": return 0x0D; case "TAB": return 0x09; case "ESC": return 0x1B; case "SPACE": return 0x20;
            case "BACKSPACE": return 0x08; case "DELETE": return 0x2E; case "HOME": return 0x24; case "END": return 0x23;
            case "PAGEUP": return 0x21; case "PAGEDOWN": return 0x22;
            case "LEFT": return 0x25; case "UP": return 0x26; case "RIGHT": return 0x27; case "DOWN": return 0x28;
            default: throw Fail("invalid_request", "Unsupported key name.");
        }
    }

    private static bool ExtendedKey(ushort key) { return (key >= 0x21 && key <= 0x28) || key == 0x2E || key == 0x5B; }
    private static Native.INPUT Keyboard(ushort key, char scan, uint flags) { return Keyboard(key, (ushort)scan, flags); }
    private static Native.INPUT Keyboard(ushort key, ushort scan, uint flags)
    {
        Native.INPUT input = new Native.INPUT(); input.Type = 1;
        input.Data.Keyboard.wVk = key; input.Data.Keyboard.wScan = scan; input.Data.Keyboard.dwFlags = flags;
        return input;
    }
    private static Native.INPUT Mouse(uint flags, uint data)
    { Native.INPUT input = new Native.INPUT(); input.Data.Mouse.dwFlags = flags; input.Data.Mouse.mouseData = data; return input; }

    // On a partial SendInput insertion, only release downs inserted by this call.
    // The original action is never retried and user-held keys were rejected beforehand.
    private static bool ReleasePartial(Native.INPUT[] inputs, uint sent)
    {
        List<Native.INPUT> held = new List<Native.INPUT>();
        for (int i = 0; i < Math.Min(sent, (uint)inputs.Length); i++)
        {
            Native.INPUT item = inputs[i];
            if (item.Type == 1)
            {
                if ((item.Data.Keyboard.dwFlags & 2) == 0) held.Add(item);
                else for (int k = held.Count - 1; k >= 0; k--)
                    if (held[k].Type == 1 && held[k].Data.Keyboard.wVk == item.Data.Keyboard.wVk && held[k].Data.Keyboard.wScan == item.Data.Keyboard.wScan) { held.RemoveAt(k); break; }
            }
            else if ((item.Data.Mouse.dwFlags & (2 | 8 | 32)) != 0) held.Add(item);
            else if ((item.Data.Mouse.dwFlags & (4 | 16 | 64)) != 0)
                for (int k = held.Count - 1; k >= 0; k--)
                    if (held[k].Type == 0 && held[k].Data.Mouse.dwFlags * 2 == item.Data.Mouse.dwFlags) { held.RemoveAt(k); break; }
        }
        List<Native.INPUT> releases = new List<Native.INPUT>();
        for (int i = held.Count - 1; i >= 0; i--)
        {
            Native.INPUT item = held[i];
            if (item.Type == 1) item.Data.Keyboard.dwFlags |= 2;
            else item.Data.Mouse.dwFlags *= 2;
            releases.Add(item);
        }
        return releases.Count == 0 || Native.SendInput((uint)releases.Count, releases.ToArray(), Marshal.SizeOf(typeof(Native.INPUT))) == releases.Count;
    }

    private static object RectJson(Native.RECT rect)
    { return Map("x", rect.Left, "y", rect.Top, "width", (long)rect.Right - rect.Left, "height", (long)rect.Bottom - rect.Top); }
    private static Native.RECT WindowRect(IntPtr hwnd)
    { Native.RECT rect; if (!Native.GetWindowRect(hwnd, out rect)) NativeFailure("GetWindowRect"); return rect; }
    private static Native.RECT DesktopRect()
    {
        int x = Native.GetSystemMetrics(76), y = Native.GetSystemMetrics(77);
        return new Native.RECT { Left = x, Top = y, Right = checked(x + Native.GetSystemMetrics(78)), Bottom = checked(y + Native.GetSystemMetrics(79)) };
    }
    private static void NativeFailure(string operation)
    { throw Fail("native_error", operation + " failed (Windows error " + Marshal.GetLastWin32Error().ToString(Invariant) + ")."); }

    private static void EnumerateWindows(Func<IntPtr, bool> action)
    {
        Exception error = null; bool stopped = false;
        Native.EnumWindowsProc callback = delegate(IntPtr hwnd, IntPtr unused) {
            try { bool keep = action(hwnd); if (!keep) stopped = true; return keep; }
            catch (Exception e) { error = e; stopped = true; return false; }
        };
        bool success = Native.EnumWindows(callback, IntPtr.Zero);
        GC.KeepAlive(callback);
        if (error != null) throw error;
        if (!success && !stopped) NativeFailure("EnumWindows");
    }
    private static string WindowTitle(IntPtr hwnd)
    {
        int length = Math.Min(32767, Math.Max(0, Native.GetWindowTextLength(hwnd)));
        StringBuilder title = new StringBuilder(length + 1); Native.GetWindowText(hwnd, title, title.Capacity); return title.ToString();
    }
    private static Dictionary<string, object> WindowInfo(IntPtr hwnd)
    {
        uint pid; Native.GetWindowThreadProcessId(hwnd, out pid);
        IntPtr process = Native.OpenProcess(0x1000, false, pid);
        if (process == IntPtr.Zero) throw Fail("access_denied", "Cannot obtain process identity.");
        try
        {
            Target target = new Target { Hwnd = unchecked((ulong)hwnd.ToInt64()), Pid = pid, Started = ProcessStarted(process) };
            VerifyTarget(target);
            StringBuilder cls = new StringBuilder(256); Native.GetClassName(hwnd, cls, cls.Capacity);
            StringBuilder path = new StringBuilder(32768); int size = path.Capacity;
            string executable = Native.QueryFullProcessImageName(process, 0, path, ref size) ? path.ToString() : null;
            Dictionary<string, object> row = Map("title", WindowTitle(hwnd), "class", cls.ToString(), "rect", RectJson(WindowRect(hwnd)),
                "minimized", Native.IsIconic(hwnd), "visible", Native.IsWindowVisible(hwnd), "enabled", Native.IsWindowEnabled(hwnd),
                "target", target.Json(), "process_path", executable);
            VerifyTarget(target);
            return row;
        }
        finally { Native.CloseHandle(process); }
    }

    private static ulong ProcessStarted(IntPtr process)
    {
        Native.FILETIME start, exit, kernel, user;
        if (!Native.GetProcessTimes(process, out start, out exit, out kernel, out user)) NativeFailure("GetProcessTimes");
        return ((ulong)start.High << 32) | start.Low;
    }
    private static void VerifyTarget(Target target)
    {
        uint pid, session;
        if (!Native.IsWindow(target.Handle) || Native.GetAncestor(target.Handle, 2) != target.Handle ||
            Native.GetWindowThreadProcessId(target.Handle, out pid) == 0 || pid != target.Pid)
            throw Fail("stale_target", "The HWND no longer identifies this top-level window and process.");
        if (!Native.ProcessIdToSessionId(pid, out session) || session != (uint)CurrentSession())
            throw Fail("session_mismatch", "The target is not in this worker's interactive session.");
        IntPtr process = Native.OpenProcess(0x1000, false, pid);
        if (process == IntPtr.Zero) throw Fail("access_denied", "Cannot verify the target process identity.");
        try
        {
            if (ProcessStarted(process) != target.Started) throw Fail("stale_target", "The process creation time no longer matches this target.");
            uint again; Native.GetWindowThreadProcessId(target.Handle, out again);
            if (again != pid) throw Fail("stale_target", "The window identity changed during verification.");
        }
        finally { Native.CloseHandle(process); }
    }
    private sealed class ProcessLease : IDisposable
    {
        private IntPtr handle;
        public ProcessLease(Target target)
        {
            VerifyTarget(target);
            handle = Native.OpenProcess(0x1000, false, target.Pid);
            if (handle == IntPtr.Zero) throw Fail("access_denied", "Cannot retain the target process identity.");
            try { if (ProcessStarted(handle) != target.Started) throw Fail("stale_target", "The process identity changed."); }
            catch { Dispose(); throw; }
        }
        public void EnsureWritable()
        {
            if (Integrity(handle) > Integrity(Native.GetCurrentProcess()))
                throw Fail("elevated_target", "The target has a higher integrity level; elevated UI is unavailable to this worker.");
        }
        public void Dispose() { if (handle != IntPtr.Zero) { Native.CloseHandle(handle); handle = IntPtr.Zero; } }
    }
    private static int Integrity(IntPtr process)
    {
        IntPtr token;
        if (!Native.OpenProcessToken(process, 0x0008, out token)) throw Fail("access_denied", "Cannot verify process integrity.");
        try
        {
            int size; Native.GetTokenInformation(token, 25, IntPtr.Zero, 0, out size);
            if (size <= 0 || size > 65536) throw Fail("access_denied", "Cannot query process integrity.");
            IntPtr data = Marshal.AllocHGlobal(size);
            try
            {
                if (!Native.GetTokenInformation(token, 25, data, size, out size)) NativeFailure("GetTokenInformation");
                IntPtr sid = Marshal.ReadIntPtr(data); IntPtr count = Native.GetSidSubAuthorityCount(sid);
                if (count == IntPtr.Zero) throw Fail("access_denied", "Invalid integrity SID.");
                byte n = Marshal.ReadByte(count);
                if (n == 0) throw Fail("access_denied", "Invalid integrity SID.");
                return Marshal.ReadInt32(Native.GetSidSubAuthority(sid, (uint)n - 1));
            }
            finally { Marshal.FreeHGlobal(data); }
        }
        finally { Native.CloseHandle(token); }
    }

    private static int CurrentSession() { using (Process process = Process.GetCurrentProcess()) return process.SessionId; }
    private sealed class DesktopState
    {
        public bool Interactive; public bool Active; public bool Available; public string Reason;
    }
    private static string ObjectName(IntPtr handle)
    {
        if (handle == IntPtr.Zero) return null;
        StringBuilder name = new StringBuilder(512); int needed;
        return Native.GetUserObjectInformation(handle, 2, name, name.Capacity * 2, out needed) ? name.ToString() : null;
    }
    private static DesktopState GetDesktopState()
    {
        DesktopState state = new DesktopState(); int session = CurrentSession();
        state.Interactive = session != 0 && Environment.UserInteractive && String.Equals(ObjectName(Native.GetProcessWindowStation()), "WinSta0", StringComparison.OrdinalIgnoreCase);
        IntPtr info; int bytes;
        if (Native.WTSQuerySessionInformation(IntPtr.Zero, session, 8, out info, out bytes))
        {
            try { state.Active = bytes >= 4 && Marshal.ReadInt32(info) == 0; }
            finally { Native.WTSFreeMemory(info); }
        }
        if (!state.Interactive || !state.Active) { state.Reason = "No active interactive user session is available."; return state; }
        IntPtr desktop = Native.OpenInputDesktop(0, false, 0x0001);
        if (desktop == IntPtr.Zero) { state.Reason = "The input desktop is locked, secure, or inaccessible."; return state; }
        try
        {
            string input = ObjectName(desktop), current = ObjectName(Native.GetThreadDesktop(Native.GetCurrentThreadId()));
            state.Available = String.Equals(input, "Default", StringComparison.OrdinalIgnoreCase) && String.Equals(input, current, StringComparison.OrdinalIgnoreCase);
            state.Reason = state.Available ? null : "The input desktop is locked, secure, or different from the worker desktop.";
        }
        finally { Native.CloseDesktop(desktop); }
        return state;
    }
    private static void EnsureDesktop()
    { DesktopState state = GetDesktopState(); if (!state.Available) throw Fail("desktop_unavailable", state.Reason); }
    private static object Status()
    {
        DesktopState state = GetDesktopState(); string user;
        using (WindowsIdentity identity = WindowsIdentity.GetCurrent()) user = identity.Name;
        return Map("session", CurrentSession(), "user", user, "interactive", state.Interactive, "session_active", state.Active,
            "available", state.Available, "locked", state.Interactive && state.Active ? (object)!state.Available : null,
            "locked_or_secure", state.Interactive && state.Active && !state.Available, "reason", state.Reason,
            "capabilities", Operations, "limitations", new string[] {
                "No login, unlock, RDP, or secure-desktop management.", "Elevated UI above this worker's integrity level is unavailable.",
                "Locked and UAC secure desktops may both appear unavailable.", "UIA depends on application accessibility providers; passwords are not read.",
                "Screenshots capture visible screen pixels and may include overlapping windows.", "Input submission does not prove application completion."
            });
    }

    private sealed class DpiScope : IDisposable
    {
        private IntPtr previous;
        public DpiScope()
        {
            try
            {
                previous = Native.SetThreadDpiAwarenessContext(new IntPtr(-4));
                if (previous == IntPtr.Zero) previous = Native.SetThreadDpiAwarenessContext(new IntPtr(-3));
                if (previous != IntPtr.Zero) return;
            }
            catch (EntryPointNotFoundException) { }
            int result = Native.SetProcessDpiAwareness(2), awareness;
            if (result < 0 && (Native.GetProcessDpiAwareness(IntPtr.Zero, out awareness) < 0 || awareness != 2))
                throw Fail("dpi_unavailable", "Per-monitor DPI awareness could not be enabled.");
        }
        public void Dispose() { if (previous != IntPtr.Zero) Native.SetThreadDpiAwarenessContext(previous); }
    }

    private static class Native
    {
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
        [StructLayout(LayoutKind.Sequential)] public struct FILETIME { public uint Low, High; }
        [StructLayout(LayoutKind.Sequential)] public struct MSG { public IntPtr hwnd; public uint message; public UIntPtr wParam; public IntPtr lParam; public uint time; public POINT point; public uint lPrivate; }
        [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public UIntPtr dwExtraInfo; }
        [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public UIntPtr dwExtraInfo; }
        [StructLayout(LayoutKind.Sequential)] public struct HARDWAREINPUT { public uint uMsg; public ushort wParamL, wParamH; }
        [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION
        {
            [FieldOffset(0)] public MOUSEINPUT Mouse;
            [FieldOffset(0)] public KEYBDINPUT Keyboard;
            [FieldOffset(0)] public HARDWAREINPUT Hardware;
        }
        // Sequential alignment puts the union at offset 8 on x64: sizeof(INPUT) == 40.
        [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint Type; public INPUTUNION Data; }
        [return: MarshalAs(UnmanagedType.Bool)] public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
        [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool IsWindow(IntPtr hwnd);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool IsWindowEnabled(IntPtr hwnd);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool IsZoomed(IntPtr hwnd);
        [DllImport("user32.dll", SetLastError = true)] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern int GetWindowTextLength(IntPtr hwnd);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern int GetClassName(IntPtr hwnd, StringBuilder text, int count);
        [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
        [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool BringWindowToTop(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr hwnd);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool SetForegroundWindow(IntPtr hwnd);
        [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool AttachThreadInput(uint from, uint to, [MarshalAs(UnmanagedType.Bool)] bool attach);
        [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool ShowWindowAsync(IntPtr hwnd, int command);
        [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int width, int height, uint flags);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool PostMessage(IntPtr hwnd, uint message, UIntPtr wparam, IntPtr lparam);
        [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT point);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
        [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
        [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetClipCursor(out RECT rect);
        [DllImport("user32.dll", SetLastError = true)] public static extern uint SendInput(uint count, [In] INPUT[] inputs, int size);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool PeekMessage(out MSG message, IntPtr hwnd, uint min, uint max, uint remove);
        [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr GetDC(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
        [DllImport("gdi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool BitBlt(IntPtr destination, int x, int y, int width, int height, IntPtr source, int sourceX, int sourceY, uint operation);
        [DllImport("kernel32.dll", SetLastError = true)] public static extern IntPtr OpenProcess(uint access, [MarshalAs(UnmanagedType.Bool)] bool inherit, uint pid);
        [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetProcessTimes(IntPtr process, out FILETIME start, out FILETIME exit, out FILETIME kernel, out FILETIME user);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder path, ref int size);
        [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool ProcessIdToSessionId(uint pid, out uint session);
        [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
        [DllImport("kernel32.dll")] public static extern IntPtr GetCurrentProcess();
        [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
        [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetTokenInformation(IntPtr token, int infoClass, IntPtr info, int length, out int needed);
        [DllImport("advapi32.dll")] public static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
        [DllImport("advapi32.dll")] public static extern IntPtr GetSidSubAuthority(IntPtr sid, uint index);
        [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr OpenInputDesktop(uint flags, [MarshalAs(UnmanagedType.Bool)] bool inherit, uint access);
        [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool CloseDesktop(IntPtr desktop);
        [DllImport("user32.dll")] public static extern IntPtr GetThreadDesktop(uint thread);
        [DllImport("user32.dll")] public static extern IntPtr GetProcessWindowStation();
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder info, int length, out int needed);
        [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool WTSQuerySessionInformation(IntPtr server, int session, int infoClass, out IntPtr buffer, out int bytes);
        [DllImport("wtsapi32.dll")] public static extern void WTSFreeMemory(IntPtr memory);
        [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
        [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int awareness);
        [DllImport("shcore.dll")] public static extern int GetProcessDpiAwareness(IntPtr process, out int awareness);
    }
}
