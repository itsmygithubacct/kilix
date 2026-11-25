# Conversational Avatar

Run `kilix avatar` to install the pinned native Avatar and open its conversation
pane. Build dependencies are a C compiler, make, pkg-config, FreeType, fontconfig
and zlib development headers. `kilix avatar --force-install` refreshes an existing
installation to this release's pin. The source repository requires repository
access when building from source.

Select **Codex**, then **Codex setup**, to reuse Codex CLI sign-in, sign in with
ChatGPT, or paste an API key into hidden terminal input. Avatar stores that key
outside Git and the shared execution workspace, in a private directory and an
owner-only file. The configuration check makes no paid generation request;
service-side key validity is checked by the first conversation request. Codex
CLI must be installed separately. The model and effort selection persists, and
an explicitly saved default takes precedence on startup; the initial fallback is
Luna / low. Local Qwen chat, Kristin speech and dictation use their existing
explicit setup flows.
