# Source provenance

This is a focused CLI/library derivative of the MIT-licensed tmux-cli core
vendored by tmux-browse, not the web dashboard or its agent extensions.

- Upstream: https://github.com/itsmygithubacct/tmux-cli
- Source revision: `c1c9a3ff1617a5e517bd60d32222d545b58f5466`
- Parent tmux-browse revision: `80e2d8eb90aa2f6c1a4abc32b827693da650efb1`
- Adapted source: `lib/sessions.py` enumeration, lifecycle, bounded capture,
  literal send, private paste buffer and separate Enter helpers;
  `lib/targeting.py` exact targeting.
- Licence: MIT; original copyright retained in `LICENSE`.

Changes: mandatory explicit socket, stable-ID resolution, refusal of ambiguous
pane targets, strict request schema, dry runs, key allowlist, no session logging
or web service, and one shared versioned response for CLI/Kilix/Needle consumers.
Literal payloads travel through a unique named tmux buffer loaded from stdin,
so tmux's command parser cannot remove a trailing semicolon.
