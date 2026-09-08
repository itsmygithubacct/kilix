# Codex

Use `/model` in the existing interactive session. The picker can include a
second reasoning-effort screen: selecting a model row alone may not complete
the operation. Inspect both screens and the final confirmation. Preserve the
previous effort unless the user specified another or delegated its selection.

Use `/status` and the model footer for current configuration. When already
available, a new turn's runtime metadata is stronger evidence than a shared
config file. Do not inspect unrelated rollouts to discover model state.

`/fast` changes service tier, not the model. It may toggle rather than set a
value; never send it twice as a retry without reading its state. A request for
Fast off must end with the default service tier confirmed. No particular model
implies a universal Fast-mode preference.

Inspect the installed picker's persistence controls. If it saves future-session
defaults without offering a session-only choice, explain that limitation before
using it for a session-only request. Do not restart with `codex --model` or
edit `config.toml` as an unannounced substitute for an in-place switch.

Official references, checked 2026-09-08:

- [Models](https://learn.chatgpt.com/docs/models)
- [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
