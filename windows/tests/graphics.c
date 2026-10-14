/* SPDX-License-Identifier: GPL-3.0-only */
/* Run in the logged-in desktop session to test the real display driver. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include <stdio.h>
#include <stdlib.h>

typedef HGLRC (WINAPI *create_context_fn)(HDC, HGLRC, const int *);

int wmain(int argc, wchar_t **argv) {
    if (argc != 2) {
        fputs("Usage: kilix-wgl-probe.exe RESULT_FILE\n", stderr);
        return 2;
    }
    FILE *report = _wfopen(argv[1], L"wb");
    if (!report) return 2;
    HWND window = NULL;
    HDC dc = NULL;
    HGLRC legacy = NULL, modern = NULL;
    int result = 1;
    const wchar_t *name = L"KilixWGLProbe";
    WNDCLASSW klass = {0};
    klass.style = CS_OWNDC;
    klass.lpfnWndProc = DefWindowProcW;
    klass.hInstance = GetModuleHandleW(NULL);
    klass.lpszClassName = name;
    DWORD session = 0;
    ProcessIdToSessionId(GetCurrentProcessId(), &session);
    fprintf(report, "windows_session=%lu\n", session);
    if (!RegisterClassW(&klass)) goto done;
    window = CreateWindowExW(0, name, L"Kilix graphics capability check",
        WS_OVERLAPPEDWINDOW, 0, 0, 128, 128, NULL, NULL, klass.hInstance, NULL);
    if (!window) goto done;
    dc = GetDC(window);
    if (!dc) goto done;
    PIXELFORMATDESCRIPTOR format = {0};
    format.nSize = sizeof(format);
    format.nVersion = 1;
    format.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    format.iPixelType = PFD_TYPE_RGBA;
    format.cColorBits = 32;
    format.cAlphaBits = 8;
    int selected = ChoosePixelFormat(dc, &format);
    if (!selected || !SetPixelFormat(dc, selected, &format)) goto done;
    legacy = wglCreateContext(dc);
    if (!legacy || !wglMakeCurrent(dc, legacy)) goto done;
    fprintf(report, "legacy_version=%s\n", glGetString(GL_VERSION));
    create_context_fn create = (create_context_fn)wglGetProcAddress("wglCreateContextAttribsARB");
    if (!create || (INT_PTR)create == 1 || (INT_PTR)create == 2 ||
        (INT_PTR)create == 3 || (INT_PTR)create == -1) {
        fputs("reason=WGL_ARB_create_context unavailable\n", report);
        goto done;
    }
    /* WGL_CONTEXT_MAJOR_VERSION_ARB, MINOR, PROFILE_MASK, CORE_PROFILE_BIT. */
    const int attributes[] = {0x2091, 3, 0x2092, 3, 0x9126, 1, 0};
    modern = create(dc, NULL, attributes);
    if (!modern || !wglMakeCurrent(dc, modern)) {
        fputs("reason=OpenGL 3.3 core context creation failed\n", report);
        goto done;
    }
    fprintf(report, "vendor=%s\nrenderer=%s\nversion=%s\n",
        glGetString(GL_VENDOR), glGetString(GL_RENDERER), glGetString(GL_VERSION));
    glViewport(0, 0, 32, 32);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    unsigned char pixel[4] = {0};
    glReadPixels(1, 1, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
    GLenum error = glGetError();
    fprintf(report, "pixel=%u,%u,%u,%u\ngl_error=%u\n",
        pixel[0], pixel[1], pixel[2], pixel[3], error);
    if (error == GL_NO_ERROR && abs((int)pixel[0] - 64) <= 1 &&
        abs((int)pixel[1] - 128) <= 1 && abs((int)pixel[2] - 191) <= 1)
        result = 0;
done:
    if (result) fprintf(report, "last_win32_error=%lu\n", GetLastError());
    wglMakeCurrent(NULL, NULL);
    if (modern) wglDeleteContext(modern);
    if (legacy) wglDeleteContext(legacy);
    if (dc) ReleaseDC(window, dc);
    if (window) DestroyWindow(window);
    UnregisterClassW(name, klass.hInstance);
    fprintf(report, "RESULT=%s\n", result ? "FAIL" : "PASS");
    fclose(report);
    return result;
}
