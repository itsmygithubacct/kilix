// SPDX-License-Identifier: GPL-3.0-only
// Native, relocatable Kilix launcher. The renderer owns the child sessions.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static wchar_t command[32768];
static size_t used;

static void fail(const wchar_t *message) {
#ifdef KILIX_CONSOLE
    fwprintf(stderr, L"Kilix: %ls\n", message);
#else
    MessageBoxW(NULL, message, L"Kilix 0.2.2", MB_OK | MB_ICONERROR);
#endif
    ExitProcess(2);
}

static void put(wchar_t c) {
    if (used + 1 >= sizeof(command) / sizeof(*command)) fail(L"Command line is too long.");
    command[used++] = c;
    command[used] = 0;
}

static void argument(const wchar_t *value) {
    if (used) put(L' ');
    put(L'"');
    for (;;) {
        size_t slashes = 0;
        while (*value == L'\\') { slashes++; value++; }
        size_t count = (*value == L'"' || !*value) ? slashes * 2 : slashes;
        while (count--) put(L'\\');
        if (!*value) break;
        if (*value == L'"') put(L'\\');
        put(*value++);
    }
    put(L'"');
}

static BOOL is_command(const wchar_t *verb) {
    static const wchar_t *verbs[] = {
        L"help", L"--help", L"-h", L"@", L"ls", L"pane", L"tab", L"focus", L"watch",
        L"new-pane", L"split", L"new-tab", L"new-page", L"launch", L"--launch",
        L"fullscreen", L"settings", L"--settings", L"status", L"transcript", L"transcripts",
        L"session-log", L"session-logs", L"apps", L"app", L"games", L"setup", L"update",
        L"session", L"sessions"
    };
    for (size_t i = 0; i < sizeof(verbs) / sizeof(*verbs); i++)
        if (!wcscmp(verb, verbs[i])) return TRUE;
    return FALSE;
}

static int run_command(const wchar_t *root, int argc, wchar_t **argv) {
    wchar_t path[32768], script[32768];
    if (swprintf(path, 32768, L"%ls\\kitty\\launcher\\kitty-console.exe", root) < 0 ||
        swprintf(script, 32768, L"%ls\\native-commands.py", root) < 0)
        fail(L"Installation path is too long.");
    argument(path);
    argument(L"+runpy");
    // The path and every user argument remain separate Windows argv items.
    // No user text is inserted into Python or shell source.
    argument(L"import runpy,sys; p=sys.argv.pop(1); sys.argv[0]=p; runpy.run_path(p,run_name='__main__')");
    argument(script);
    for (int i = 1; i < argc; i++) argument(argv[i]);
    STARTUPINFOW startup = { .cb = sizeof(startup) };
    PROCESS_INFORMATION process;
    if (!CreateProcessW(path, command, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &process))
        fail(L"Cannot start the bundled native command runtime.");
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD result = 1;
    GetExitCodeProcess(process.hProcess, &result);
    CloseHandle(process.hProcess);
    return (int)result;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR tail, int show) {
    (void)instance; (void)previous; (void)tail; (void)show;
    wchar_t root[32768], path[32768], user_config[32768];
    DWORD length = GetModuleFileNameW(NULL, root, 32768);
    if (!length || length >= 32768) fail(L"Cannot locate the Kilix installation.");
    wchar_t *end = wcsrchr(root, L'\\');
    if (!end) fail(L"Cannot locate the Kilix installation.");
    *end = 0; // Kilix.exe lives at the installation root.
    int argc;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) fail(L"Cannot read the command line.");
    // Portable/managed generations remain immutable while commands execute.
    if (!SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", L"1"))
        fail(L"Cannot configure the command runtime.");
    const wchar_t *verb = argc > 1 ? argv[1] : L"";
    if (is_command(verb)) {
#ifndef KILIX_CONSOLE
        // CLI verbs invoked through the graphical launcher need somewhere to
        // display their result. Console launchers retain redirected I/O.
        if (!AttachConsole(ATTACH_PARENT_PROCESS)) AllocConsole();
#endif
        int result = run_command(root, argc, argv);
        LocalFree(argv);
        return result;
    }
    if (!wcscmp(verb, L"--version") || !wcscmp(verb, L"version")) {
        const wchar_t *message = L"Kilix 0.2.2 Windows development build (Kitty 0.48.2)";
#ifdef KILIX_CONSOLE
        wprintf(L"%ls\n", message);
#else
        MessageBoxW(NULL, message, L"Kilix 0.2.2", MB_OK | MB_ICONINFORMATION);
#endif
        LocalFree(argv);
        return 0;
    }
    BOOL engine = !wcscmp(verb, L"engine");
    BOOL wsl = !wcscmp(verb, L"--wsl") || !wcscmp(verb, L"wsl");
    BOOL cmd = !wcscmp(verb, L"--cmd") || !wcscmp(verb, L"cmd");
    BOOL powershell = !wcscmp(verb, L"powershell");
    if (argc > 1 && !engine && !wsl && !cmd && !powershell &&
        wcscmp(verb, L"-o") && wcscmp(verb, L"--override") &&
        wcscmp(verb, L"--config") && wcscmp(verb, L"--title") && wcscmp(verb, L"--directory")) {
        wchar_t error[1024];
        swprintf(error, 1024, L"Unsupported Windows command: %.200ls. Run kilix help for supported commands, or kilix engine --help for engine options.", verb);
        fail(error);
    }
    if (swprintf(path, 32768, L"%ls\\kitty\\launcher\\kitty.exe", root) < 0) fail(L"Installation path is too long.");
#ifdef KILIX_CONSOLE
    if (engine && swprintf(path, 32768, L"%ls\\kitty\\launcher\\kitty-console.exe", root) < 0) fail(L"Installation path is too long.");
#endif
    argument(path);
    argument(L"--config");
    wchar_t config[32768];
    if (swprintf(config, 32768, L"%ls\\config\\kilix.conf", root) < 0) fail(L"Installation path is too long.");
    argument(config);
    length = GetEnvironmentVariableW(L"LOCALAPPDATA", user_config, 32768);
    if (!length || length > 32700) fail(L"LOCALAPPDATA is unavailable.");
    wcscat(user_config, L"\\Kilix");
    if (!CreateDirectoryW(user_config, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) fail(L"Cannot create the Kilix settings directory.");
    SetEnvironmentVariableW(L"KITTY_CONFIG_DIRECTORY", user_config);
    wcscat(user_config, L"\\kitty.conf");
    if (GetFileAttributesW(user_config) != INVALID_FILE_ATTRIBUTES) {
        argument(L"--config"); argument(user_config);
    }
    argument(L"--title"); argument(L"Kilix");
    int first = engine || powershell ? 2 : 1;
    if (wsl) {
        wchar_t shell[32768] = L"shell=wsl.exe";
        first = 2;
        if (argc > 2 && argv[2][0] != L'-') {
            // Kitty parses this option with shell quoting; WSL distribution
            // names cannot contain quotes or control characters.
            for (const wchar_t *p = argv[2]; *p; p++)
                if (*p < L' ' || *p == L'\'' || *p == L'"' || *p == L'\\') fail(L"Invalid WSL distribution name.");
            if (swprintf(shell, 32768, L"shell=wsl.exe --distribution '%ls'", argv[2]) < 0) fail(L"Distribution name is too long.");
            first = 3;
        }
        argument(L"-o"); argument(shell);
    } else if (cmd) {
        argument(L"-o"); argument(L"shell=cmd.exe"); first = 2;
    } else if (powershell) {
        argument(L"-o"); argument(L"shell=powershell.exe -NoLogo");
    }
    for (int i = first; i < argc; i++) argument(argv[i]);
    LocalFree(argv);
    // Make the native command available inside this application's sessions.
    // Do not modify the user's or machine's persistent PATH.
    DWORD path_size = GetEnvironmentVariableW(L"PATH", NULL, 0);
    size_t env_size = 2 * wcslen(root) + 12 + path_size + 1;
    wchar_t *session_path = calloc(env_size, sizeof(wchar_t));
    if (!session_path) fail(L"Cannot allocate the session PATH.");
    wcscpy(session_path, root);
    wcscat(session_path, L";");
    wcscat(session_path, root);
    wcscat(session_path, L"\\wsl-bin;");
    if (path_size) GetEnvironmentVariableW(L"PATH", session_path + wcslen(session_path), path_size);
    if (!SetEnvironmentVariableW(L"PATH", session_path)) fail(L"Cannot set the session PATH.");
    free(session_path);
    STARTUPINFOW startup = { .cb = sizeof(startup) };
    PROCESS_INFORMATION process;
    if (!CreateProcessW(path, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &process)) {
        wchar_t error[256];
        swprintf(error, 256, L"Cannot start the Kilix engine (Windows error %lu).", GetLastError());
        fail(error);
    }
    CloseHandle(process.hThread);
    DWORD result = 0;
#ifdef KILIX_CONSOLE
    if (engine) {
        WaitForSingleObject(process.hProcess, INFINITE);
        if (!GetExitCodeProcess(process.hProcess, &result)) result = 1;
    }
#endif
    CloseHandle(process.hProcess);
    return (int)result;
}

#ifdef KILIX_CONSOLE
int wmain(void) {
    return wWinMain(GetModuleHandleW(NULL), NULL, NULL, SW_SHOWNORMAL);
}
#endif
