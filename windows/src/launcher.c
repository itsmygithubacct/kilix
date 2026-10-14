// SPDX-License-Identifier: GPL-3.0-only
// Native, relocatable Kilix launcher. The renderer owns the child sessions.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <wchar.h>

static wchar_t command[32768];
static size_t used;

static void fail(const wchar_t *message) {
    MessageBoxW(NULL, message, L"Kilix 0.2.2", MB_OK | MB_ICONERROR);
    ExitProcess(1);
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
    if (argc > 1 && (!wcscmp(argv[1], L"--version") || !wcscmp(argv[1], L"--help"))) {
        MessageBoxW(NULL,
            !wcscmp(argv[1], L"--version") ? L"Kilix 0.2.2 Windows development build\nBased on RC6-era main; Kitty engine 0.48.2."
            : L"Kilix.exe                     PowerShell\nKilix.exe --wsl                Default WSL distribution\nKilix.exe --wsl Ubuntu         Named WSL distribution\nKilix.exe --cmd                Command Prompt\nKilix.exe [kitty options]      Advanced engine options\n\nCtrl+Alt+R/D split; Ctrl+Shift+T new tab.\nCtrl+Shift+U opens a WSL tab.\nUser settings: %LOCALAPPDATA%\\Kilix\\kitty.conf",
            L"Kilix 0.2.2", MB_OK | MB_ICONINFORMATION);
        LocalFree(argv);
        return 0;
    }
    if (swprintf(path, 32768, L"%ls\\kitty\\launcher\\kitty.exe", root) < 0) fail(L"Installation path is too long.");
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
    int first = 1;
    if (argc > 1 && !wcscmp(argv[1], L"--wsl")) {
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
    } else if (argc > 1 && !wcscmp(argv[1], L"--cmd")) {
        argument(L"-o"); argument(L"shell=cmd.exe"); first = 2;
    }
    for (int i = first; i < argc; i++) argument(argv[i]);
    LocalFree(argv);
    STARTUPINFOW startup = { .cb = sizeof(startup) };
    PROCESS_INFORMATION process;
    if (!CreateProcessW(path, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &process)) {
        wchar_t error[256];
        swprintf(error, 256, L"Cannot start the Kilix engine (Windows error %lu).", GetLastError());
        fail(error);
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
