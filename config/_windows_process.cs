// SPDX-License-Identifier: GPL-3.0-only
// One child process with an explicit three-handle stdio inheritance boundary.
using System;
using System.ComponentModel;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public sealed class KilixSshProcess : IDisposable {
    AnonymousPipeServerStream input, output, error;
    SafeWaitHandle process;
    bool disposed;
    public StreamWriter StandardInput { get; private set; }
    public StreamReader StandardOutput { get; private set; }
    public StreamReader StandardError { get; private set; }

    public static KilixSshProcess Start(string file, string arguments) {
        if (String.IsNullOrEmpty(file) || file.IndexOf('"') >= 0 || file.IndexOf('\0') >= 0)
            throw new ArgumentException("Invalid executable path", "file");
        var child = new KilixSshProcess();
        var pi = new ProcessInfo();
        IntPtr attributes = IntPtr.Zero, handles = IntPtr.Zero, size = IntPtr.Zero;
        bool initialized = false, spawned = false, complete = false;
        try {
            child.input = new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.Inheritable);
            child.output = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
            child.error = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
            var utf8 = new UTF8Encoding(false);
            child.StandardInput = new StreamWriter(child.input, utf8, 4096, false);
            child.StandardInput.AutoFlush = true;
            child.StandardOutput = new StreamReader(child.output, utf8, true, 4096, true);
            child.StandardError = new StreamReader(child.error, utf8, true, 4096, true);

            // The sizing call fails with ERROR_INSUFFICIENT_BUFFER and returns the required size.
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
            if (size == IntPtr.Zero) throw new Win32Exception();
            attributes = Marshal.AllocHGlobal(size);
            if (!InitializeProcThreadAttributeList(attributes, 1, 0, ref size)) throw new Win32Exception();
            initialized = true;
            var startup = new StartupInfoEx();
            startup.info.cb = Marshal.SizeOf(typeof(StartupInfoEx));
            startup.info.flags = 0x100; // STARTF_USESTDHANDLES
            startup.info.stdin = child.input.ClientSafePipeHandle.DangerousGetHandle();
            startup.info.stdout = child.output.ClientSafePipeHandle.DangerousGetHandle();
            startup.info.stderr = child.error.ClientSafePipeHandle.DangerousGetHandle();
            startup.attributes = attributes;
            handles = Marshal.AllocHGlobal(3 * IntPtr.Size);
            Marshal.WriteIntPtr(handles, 0, startup.info.stdin);
            Marshal.WriteIntPtr(handles, IntPtr.Size, startup.info.stdout);
            Marshal.WriteIntPtr(handles, 2 * IntPtr.Size, startup.info.stderr);
            if (!UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), handles,
                                           new IntPtr(3 * IntPtr.Size), IntPtr.Zero, IntPtr.Zero))
                throw new Win32Exception();
            var command = new StringBuilder("\"" + file + "\" " + (arguments ?? ""));
            // EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW; only the allowlisted handles pass.
            if (!CreateProcess(file, command, IntPtr.Zero, IntPtr.Zero, true, 0x08080000,
                               IntPtr.Zero, null, ref startup, out pi)) throw new Win32Exception();
            spawned = true;
            child.process = new SafeWaitHandle(pi.process, true);
            pi.process = IntPtr.Zero;
            child.input.DisposeLocalCopyOfClientHandle();
            child.output.DisposeLocalCopyOfClientHandle();
            child.error.DisposeLocalCopyOfClientHandle();
            complete = true;
            return child;
        } finally {
            if (pi.thread != IntPtr.Zero) CloseHandle(pi.thread);
            if (initialized) DeleteProcThreadAttributeList(attributes);
            if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
            if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);
            if (!complete) {
                if (spawned) {
                    if (child.process != null) TerminateProcess(child.process, UInt32.MaxValue);
                    else if (pi.process != IntPtr.Zero) TerminateRawProcess(pi.process, UInt32.MaxValue);
                }
                child.Dispose();
            }
            if (pi.process != IntPtr.Zero) CloseHandle(pi.process);
        }
    }

    SafeWaitHandle Handle {
        get {
            if (disposed || process == null) throw new ObjectDisposedException("KilixSshProcess");
            return process;
        }
    }
    public bool WaitForExit(int milliseconds) {
        if (milliseconds < -1) throw new ArgumentOutOfRangeException("milliseconds");
        uint result = WaitForSingleObject(Handle, unchecked((uint)milliseconds));
        if (result == 0) return true;
        if (result == 258) return false;
        throw new Win32Exception();
    }
    public bool HasExited { get { return WaitForExit(0); } }
    public int ExitCode {
        get {
            if (!HasExited) throw new InvalidOperationException("Process has not exited");
            uint code;
            if (!GetExitCodeProcess(Handle, out code)) throw new Win32Exception();
            return unchecked((int)code);
        }
    }
    public void Kill() {
        if (!TerminateProcess(Handle, UInt32.MaxValue)) {
            int code = Marshal.GetLastWin32Error();
            if (!HasExited) throw new Win32Exception(code);
        }
    }
    public void Dispose() {
        if (disposed) return;
        disposed = true;
        // Close pipes first: disposing a writer must never flush into a stalled reader.
        Close(input); Close(output); Close(error);
        Close(StandardInput); Close(StandardOutput); Close(StandardError); Close(process);
    }
    static void Close(IDisposable value) {
        if (value == null) return;
        try { value.Dispose(); } catch (IOException) { } catch (InvalidOperationException) { }
    }

    [StructLayout(LayoutKind.Sequential)] struct StartupInfo {
        public int cb;
        public IntPtr reserved, desktop, title;
        public uint x, y, width, height, xChars, yChars, fill, flags;
        public ushort show, reservedBytes;
        public IntPtr reservedData, stdin, stdout, stderr;
    }
    [StructLayout(LayoutKind.Sequential)] struct StartupInfoEx {
        public StartupInfo info;
        public IntPtr attributes;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo {
        public IntPtr process, thread;
        public uint processId, threadId;
    }
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", EntryPoint="CreateProcessW", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string file, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string directory, ref StartupInfoEx startup, out ProcessInfo process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(SafeWaitHandle handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(SafeWaitHandle handle, out uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(SafeWaitHandle handle, uint code);
    [DllImport("kernel32.dll", EntryPoint="TerminateProcess", SetLastError=true)] static extern bool TerminateRawProcess(IntPtr handle, uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
}
