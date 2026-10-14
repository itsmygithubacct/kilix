/* SPDX-License-Identifier: GPL-3.0-only */
#include "session.h"
#include <stdio.h>
#include <string.h>
#include <wchar.h>

typedef struct { char bytes[1024 * 1024]; size_t size; BOOL overflow; } capture;
static capture received;
static void collect(const char *data, DWORD size, void *context) {
    capture *c = context;
    if (size >= sizeof(c->bytes) - c->size) { c->overflow = TRUE; return; }
    memcpy(c->bytes + c->size, data, size);
    c->size += size;
    c->bytes[c->size] = 0;
}
static int run_case(const char *name, int argc, const wchar_t *const *argv,
                    const char *input, const char *expected, DWORD expected_exit,
                    BOOL resize) {
    kw_session session;
    memset(&received, 0, sizeof(received));
    DWORD error = kw_start(&session, argc, argv, 80, 25, collect, &received);
    DWORD code = 0, close_error = 0;
    if (!error && resize && FAILED(kw_resize(&session, 101, 37))) error = ERROR_INVALID_DATA;
    if (!error && input) error = kw_write(&session, input, (DWORD)strlen(input));
    if (!error) error = kw_wait(&session, 30000, &code);
    close_error = kw_close(&session);
    int ok = !error && !close_error && code == expected_exit && !received.overflow &&
             strstr(received.bytes, expected) != NULL;
    printf("%s %s (error=%lu close=%lu exit=%lu bytes=%zu)\n",
            ok ? "PASS" : "FAIL", name, error, close_error, code, received.size);
    /* Raw child output can contain terminal control sequences; don't print it. */
    return ok ? 0 : 1;
}

int wmain(int argc, wchar_t **argv) {
    if (argc > 1 && wcscmp(argv[1], L"--argv-child") == 0) {
        const wchar_t *expected[] = {L"", L"two words", L"quote\"here", L"trailing\\",
                                     L"slashes\\\\\"quote", L"\x03bb\x4e2d"};
        if (argc != 8) return 10;
        for (int i = 0; i < 6; ++i)
            if (wcscmp(argv[i + 2], expected[i])) return 11 + i;
        puts("KILIX_ARGV_OK");
        return 0;
    }
    wchar_t system[MAX_PATH], powershell[MAX_PATH], cmd[MAX_PATH], wsl[MAX_PATH];
    UINT length = GetSystemDirectoryW(system, MAX_PATH);
    if (!length || length >= MAX_PATH - 60) return 2;
    swprintf(powershell, MAX_PATH, L"%ls\\WindowsPowerShell\\v1.0\\powershell.exe", system);
    swprintf(cmd, MAX_PATH, L"%ls\\cmd.exe", system);
    swprintf(wsl, MAX_PATH, L"%ls\\wsl.exe", system);
    int failures = 0;
    DWORD before = 0, after = 0;
    const wchar_t *cmd_args[] = {cmd, L"/d", L"/c", L"echo KILIX_CMD_OK & exit /b 7"};
    /* Load the shared runtime and its one-time Windows state before measuring
     * per-session ownership. The warmup itself must pass. */
    failures += run_case("runtime-initialization", 4, cmd_args, NULL, "KILIX_CMD_OK", 7, FALSE);
    GetProcessHandleCount(GetCurrentProcess(), &before);
    failures += run_case("cmd-exit-code", 4, cmd_args, NULL, "KILIX_CMD_OK", 7, FALSE);
    const wchar_t *ps_args[] = {powershell, L"-NoLogo", L"-NoProfile", L"-Command",
        L"[Console]::WriteLine('KILIX_PS_OK'); exit 0"};
    failures += run_case("powershell", 5, ps_args, NULL, "KILIX_PS_OK", 0, FALSE);
    const wchar_t *input_args[] = {powershell, L"-NoLogo", L"-NoProfile", L"-Command",
        L"$v=[Console]::ReadLine(); [Console]::WriteLine('REPLY:'+ $v)"};
    failures += run_case("input-roundtrip", 5, input_args, "hello from kilix\r",
                         "REPLY:hello from kilix", 0, FALSE);
    const wchar_t *size_args[] = {powershell, L"-NoLogo", L"-NoProfile", L"-Command",
        L"Start-Sleep -Milliseconds 300; [Console]::WriteLine(('SIZE:{0}x{1}' -f [Console]::WindowWidth,[Console]::WindowHeight))"};
    failures += run_case("resize", 5, size_args, NULL, "SIZE:101x37", 0, TRUE);
    const wchar_t *unicode_args[] = {powershell, L"-NoLogo", L"-NoProfile", L"-Command",
        L"[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Console]::WriteLine('UTF8:'+[char]0x03bb+[char]0x4e2d)"};
    failures += run_case("utf8-output", 5, unicode_args, NULL, "UTF8:\xce\xbb\xe4\xb8\xad", 0, FALSE);
    const wchar_t *graphics_args[] = {powershell, L"-NoLogo", L"-NoProfile", L"-Command",
        L"[Console]::Write(([char]27)+'_Ga=q,i=42,s=1,v=1,f=24;AAAA'+([char]27)+'\\')"};
    failures += run_case("kitty-graphics-sequence", 5, graphics_args, NULL,
                         "\x1b_Ga=q,i=42,s=1,v=1,f=24;AAAA\x1b\\", 0, FALSE);
    wchar_t self[32768];
    DWORD self_length = GetModuleFileNameW(NULL, self, 32768);
    if (!self_length || self_length >= 32768) return 2;
    const wchar_t *quote_args[] = {self, L"--argv-child", L"", L"two words",
        L"quote\"here", L"trailing\\", L"slashes\\\\\"quote", L"\x03bb\x4e2d"};
    failures += run_case("argument-boundaries", 8, quote_args, NULL, "KILIX_ARGV_OK", 0, FALSE);
    kw_session session;
    const wchar_t *bad[] = {L"C:\\kilix-no-such-program-8274.exe"};
    DWORD error = kw_start(&session, 1, bad, 80, 25, NULL, NULL);
    BOOL ok = error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND;
    kw_close(&session);
    printf("%s missing-executable (error=%lu)\n", ok ? "PASS" : "FAIL", error);
    failures += !ok;
    const wchar_t *sleep_args[] = {powershell, L"-NoLogo", L"-NoProfile", L"-Command", L"Start-Sleep 60"};
    error = kw_start(&session, 5, sleep_args, 80, 25, NULL, NULL);
    DWORD code = 0;
    ok = !error && kw_wait(&session, 100, &code) == ERROR_TIMEOUT;
    ULONGLONG started = GetTickCount64();
    DWORD closed = kw_close(&session);
    ok = ok && !closed && GetTickCount64() - started < 5000;
    printf("%s timeout-and-owned-cleanup (error=%lu close=%lu)\n", ok ? "PASS" : "FAIL", error, closed);
    failures += !ok;
    GetProcessHandleCount(GetCurrentProcess(), &after);
    ok = after == before;
    printf("%s handle-balance (before=%lu after=%lu)\n", ok ? "PASS" : "FAIL", before, after);
    failures += !ok;
    if (argc == 3 && wcscmp(argv[1], L"--wsl") == 0) {
        const wchar_t *wsl_args[] = {wsl, L"--distribution", argv[2], L"--exec",
                                   L"/bin/sh", L"-c", L"printf KILIX_WSL_OK"};
        failures += run_case("wsl-distribution", 7, wsl_args, NULL, "KILIX_WSL_OK", 0, FALSE);
    } else if (argc != 1) {
        fputs("Usage: kilix-conpty-probe.exe [--wsl DISTRIBUTION]\n", stderr);
        return 2;
    }
    printf("RESULT: %s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
