/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef KILIX_WIN_SESSION_H
#define KILIX_WIN_SESSION_H
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#include <windows.h>
#include <stddef.h>

/* Byte streams are UTF-8 with terminal escape sequences, not rendered text.
 * The callback runs on the output thread and must not call session_close.
 * All other calls belong to one owner thread. write is synchronous: a future
 * GUI must place writes on a separate queue, never on the window thread. */
typedef void (*kw_output)(const char *bytes, DWORD count, void *context);
typedef struct kw_session {
    HPCON console;
    HANDLE input, output, process, reader, job;
    kw_output on_output;
    void *context;
    DWORD read_error;
} kw_session;

/* argv[0] is a resolved executable path. No shell or string interpolation. */
DWORD kw_start(kw_session *s, int argc, const wchar_t *const *argv,
               SHORT columns, SHORT rows, kw_output output, void *context);
DWORD kw_write(kw_session *s, const char *bytes, DWORD size);
HRESULT kw_resize(kw_session *s, SHORT columns, SHORT rows);
DWORD kw_wait(kw_session *s, DWORD milliseconds, DWORD *exit_code);
/* Explicit close terminates this session's Windows job, not other sessions.
 * Keep output draining while ClosePseudoConsole completes on Windows 10. */
DWORD kw_close(kw_session *s);
#endif
