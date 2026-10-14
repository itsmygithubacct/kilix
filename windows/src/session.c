/* SPDX-License-Identifier: GPL-3.0-only */
#include "session.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

static SRWLOCK spawn_lock = SRWLOCK_INIT;
static INIT_ONCE api_once = INIT_ONCE_STATIC_INIT;
static HMODULE conpty_module;
static HRESULT (WINAPI *create_console)(COORD, HANDLE, HANDLE, DWORD, HPCON *);
static HRESULT (WINAPI *resize_console)(HPCON, COORD);
static void (WINAPI *close_console)(HPCON);

static BOOL CALLBACK load_api(PINIT_ONCE once, void *parameter, void **context) {
    (void)once; (void)parameter; (void)context;
    wchar_t path[32768];
    DWORD length = GetModuleFileNameW(NULL, path, 32768);
    if (!length || length >= 32750) return FALSE;
    wchar_t *end = wcsrchr(path, L'\\');
    if (!end) { SetLastError(ERROR_BAD_PATHNAME); return FALSE; }
    wcscpy(end + 1, L"conpty.dll");
    /* Load only the bundled, pinned Microsoft runtime next to the executable. */
    conpty_module = LoadLibraryExW(path, NULL,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!conpty_module) return FALSE;
    create_console = (void *)GetProcAddress(conpty_module, "ConptyCreatePseudoConsole");
    resize_console = (void *)GetProcAddress(conpty_module, "ConptyResizePseudoConsole");
    close_console = (void *)GetProcAddress(conpty_module, "ConptyClosePseudoConsole");
    if (!create_console || !resize_console || !close_console) {
        FreeLibrary(conpty_module); conpty_module = NULL;
        SetLastError(ERROR_PROC_NOT_FOUND);
        return FALSE;
    }
    return TRUE; /* Module is shared for the lifetime of this process. */
}

/* Windows 10 can attach redirected parent standard handles to a ConPTY child
 * even with bInheritHandles=FALSE. Clear the process standard-handle slots for
 * this synchronous spawn, then restore them. All transport spawns serialize
 * here; callers must coordinate unrelated spawning around this process-global
 * operation too. The output worker only uses its explicit pipe handle. */
static BOOL spawn_attached(const wchar_t *application, wchar_t *command,
                          STARTUPINFOEXW *startup, PROCESS_INFORMATION *child) {
    const DWORD ids[] = {STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE};
    HANDLE saved[3];
    BOOL ok = TRUE;
    DWORD error = ERROR_SUCCESS;
    AcquireSRWLockExclusive(&spawn_lock);
    for (int i = 0; i < 3; ++i) saved[i] = GetStdHandle(ids[i]);
    for (int i = 0; i < 3; ++i) {
        if (!SetStdHandle(ids[i], NULL)) { error = GetLastError(); ok = FALSE; break; }
    }
    if (ok) {
        ok = CreateProcessW(application, command, NULL, NULL, FALSE,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED,
            NULL, NULL, &startup->StartupInfo, child);
        if (!ok) error = GetLastError();
    }
    for (int i = 0; i < 3; ++i) SetStdHandle(ids[i], saved[i]);
    ReleaseSRWLockExclusive(&spawn_lock);
    SetLastError(error);
    return ok;
}

static void close_handle(HANDLE *h) {
    if (*h && *h != INVALID_HANDLE_VALUE) CloseHandle(*h);
    *h = NULL;
}

/* Windows CRT quoting. Leave simple arguments bare, since cmd.exe and older
 * wsl.exe parse option tokens themselves rather than using the CRT parser. */
static wchar_t *command_line(int argc, const wchar_t *const *argv) {
    size_t capacity = 1;
    for (int i = 0; i < argc; ++i) {
        size_t n = wcslen(argv[i]);
        if (n > 16000 || capacity + n * 2 + 3 > 32767) return NULL;
        capacity += n * 2 + 3;
    }
    wchar_t *result = calloc(capacity, sizeof(wchar_t)), *out = result;
    if (!result) return NULL;
    for (int i = 0; i < argc; ++i) {
        if (i) *out++ = L' ';
        if (*argv[i] && !wcspbrk(argv[i], L" \t\r\n\v\"")) {
            size_t count = wcslen(argv[i]);
            memcpy(out, argv[i], count * sizeof(wchar_t));
            out += count;
            continue;
        }
        *out++ = L'"';
        const wchar_t *p = argv[i];
        for (;;) {
            size_t slashes = 0;
            while (*p == L'\\') { ++slashes; ++p; }
            size_t copies = (*p == L'"' || !*p) ? slashes * 2 : slashes;
            while (copies--) *out++ = L'\\';
            if (!*p) break;
            if (*p == L'"') *out++ = L'\\';
            *out++ = *p++;
        }
        *out++ = L'"';
    }
    *out = 0;
    return result;
}

static DWORD WINAPI output_thread(void *context) {
    kw_session *s = context;
    char buffer[8192];
    DWORD count;
    for (;;) {
        if (!ReadFile(s->output, buffer, sizeof(buffer), &count, NULL)) {
            DWORD error = GetLastError();
            if (error != ERROR_BROKEN_PIPE) s->read_error = error;
            break;
        }
        if (!count) break;
        if (s->on_output) s->on_output(buffer, count, s->context);
    }
    return 0;
}

DWORD kw_close(kw_session *s) {
    DWORD error = ERROR_SUCCESS;
    close_handle(&s->input);
    /* Closing the private job kills only processes assigned by this object.
     * The reader must stay alive through pseudoconsole teardown. */
    close_handle(&s->job);
    if (s->console) { close_console(s->console); s->console = NULL; }
    if (s->process && WaitForSingleObject(s->process, 5000) != WAIT_OBJECT_0)
        error = ERROR_TIMEOUT;
    if (s->reader) {
        if (WaitForSingleObject(s->reader, 5000) != WAIT_OBJECT_0) {
            CancelSynchronousIo(s->reader);
            /* No callback or context can outlive the owning session. */
            WaitForSingleObject(s->reader, INFINITE);
            error = ERROR_TIMEOUT;
        }
        if (!error) error = s->read_error;
    }
    close_handle(&s->reader);
    close_handle(&s->output);
    close_handle(&s->process);
    return error;
}

DWORD kw_start(kw_session *s, int argc, const wchar_t *const *argv,
               SHORT columns, SHORT rows, kw_output output, void *context) {
    memset(s, 0, sizeof(*s));
    if (argc < 1 || !argv || !argv[0] || columns < 1 || rows < 1)
        return ERROR_INVALID_PARAMETER;
    if (!InitOnceExecuteOnce(&api_once, load_api, NULL, NULL)) return GetLastError();
    s->on_output = output;
    s->context = context;
    HANDLE console_input = NULL, console_output = NULL;
    STARTUPINFOEXW startup = {0};
    PROCESS_INFORMATION child = {0};
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {0};
    SIZE_T attribute_size = 0;
    DWORD error = ERROR_SUCCESS;
    BOOL attributes_initialized = FALSE;
    wchar_t *command = command_line(argc, argv);
    if (!command) return ERROR_BAD_LENGTH;
    if (!CreatePipe(&console_input, &s->input, NULL, 0) ||
        !CreatePipe(&s->output, &console_output, NULL, 0)) {
        error = GetLastError(); goto done;
    }
    HRESULT hr = create_console((COORD){columns, rows}, console_input,
                                     console_output, 0, &s->console);
    if (FAILED(hr)) { error = (DWORD)hr; goto done; }
    close_handle(&console_input);
    close_handle(&console_output);
    s->reader = CreateThread(NULL, 0, output_thread, s, 0, NULL);
    if (!s->reader) { error = GetLastError(); goto done; }
    s->job = CreateJobObjectW(NULL, NULL);
    if (!s->job) { error = GetLastError(); goto done; }
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(s->job, JobObjectExtendedLimitInformation,
                                 &limits, sizeof(limits))) {
        error = GetLastError(); goto done;
    }
    InitializeProcThreadAttributeList(NULL, 1, 0, &attribute_size);
    startup.lpAttributeList = malloc(attribute_size);
    if (!startup.lpAttributeList) { error = ERROR_NOT_ENOUGH_MEMORY; goto done; }
    if (!InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0,
                                          &attribute_size)) {
        error = GetLastError(); goto done;
    }
    attributes_initialized = TRUE;
    if (!UpdateProcThreadAttribute(startup.lpAttributeList, 0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, s->console,
            sizeof(s->console), NULL, NULL)) {
        error = GetLastError(); goto done;
    }
    startup.StartupInfo.cb = sizeof(startup);
    if (!spawn_attached(argv[0], command, &startup, &child)) {
        error = GetLastError(); goto done;
    }
    s->process = child.hProcess;
    if (!AssignProcessToJobObject(s->job, child.hProcess)) {
        error = GetLastError();
        TerminateProcess(child.hProcess, 1);
        goto done;
    }
    if (ResumeThread(child.hThread) == (DWORD)-1) error = GetLastError();
done:
    close_handle(&child.hThread);
    if (attributes_initialized) DeleteProcThreadAttributeList(startup.lpAttributeList);
    free(startup.lpAttributeList);
    free(command);
    close_handle(&console_input);
    close_handle(&console_output);
    if (error) kw_close(s);
    return error;
}

DWORD kw_write(kw_session *s, const char *bytes, DWORD size) {
    if (!s->input) return ERROR_INVALID_HANDLE;
    while (size) {
        DWORD written;
        if (!WriteFile(s->input, bytes, size, &written, NULL)) return GetLastError();
        if (!written) return ERROR_WRITE_FAULT;
        bytes += written; size -= written;
    }
    return ERROR_SUCCESS;
}

HRESULT kw_resize(kw_session *s, SHORT columns, SHORT rows) {
    if (!s->console || columns < 1 || rows < 1) return E_INVALIDARG;
    return resize_console(s->console, (COORD){columns, rows});
}

DWORD kw_wait(kw_session *s, DWORD milliseconds, DWORD *exit_code) {
    if (!s->process) return ERROR_INVALID_HANDLE;
    DWORD status = WaitForSingleObject(s->process, milliseconds);
    if (status == WAIT_TIMEOUT) return ERROR_TIMEOUT;
    if (status != WAIT_OBJECT_0) return GetLastError();
    return GetExitCodeProcess(s->process, exit_code) ? ERROR_SUCCESS : GetLastError();
}
