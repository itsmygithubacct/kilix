// Processes with deliberately stalled pipes for the SSH wrapper regression tests.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;

class Fixture {
    static int Main(string[] args) {
        string exe = Process.GetCurrentProcess().MainModule.FileName;
        string root = Path.GetDirectoryName(exe);
        if (args.Length > 0 && args[0] == "cleanup") {
            foreach (string file in Directory.GetFiles(root, "*.pid")) {
                try {
                    using (Process child = Process.GetProcessById(Int32.Parse(File.ReadAllText(file)))) {
                        if (String.Equals(child.MainModule.FileName, exe, StringComparison.OrdinalIgnoreCase)) {
                            child.Kill();
                            if (!child.WaitForExit(5000)) throw new Exception("Fixture cleanup timed out");
                        }
                    }
                } catch (ArgumentException) { } catch (InvalidOperationException) { }
            }
            return 0;
        }
        if (args.Length > 0 && args[0] == "hold") {
            Thread.Sleep(60000);
            return 0;
        }
        File.WriteAllText(Path.Combine(root, Process.GetCurrentProcess().Id + ".pid"),
                          Process.GetCurrentProcess().Id.ToString());
        string mode = File.ReadAllText(Path.Combine(root, "mode"));
        if (mode == "stdin") {
            Thread.Sleep(60000); // Never consume the request; a pipe-sized write must stall.
            return 0;
        }
        Console.In.ReadToEnd(); // The wrapper must send EOF after its complete JSON line.
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.WriteLine("{\"schema\":\"kilix.desktop/v1\",\"operation\":\"status\",\"ok\":true,\"result\":{\"text\":\"caf\u00e9 \u5b57\"}}");
        Console.Error.WriteLine("fixture diagnostic");
        if (mode == "output") {
            ProcessStartInfo info = new ProcessStartInfo(exe, "hold");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            using (Process child = Process.Start(info)) {
                File.WriteAllText(Path.Combine(root, child.Id + ".pid"), child.Id.ToString());
            }
            // Exit while the descendant still owns our redirected stdout/stderr.
        }
        return 0;
    }
}
