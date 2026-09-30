# Inspecting available agent actions

`kilix action capabilities` prints one JSON object with schema
`kilix.capabilities/v1`. It works before terminal, storage, model, or application
setup. Python callers can use `agent_capabilities.discover()` from the selected
Kilix configuration directory.

The query reports the wrapper tree answering the request as
`kilix.invoked_source`, and the resolved `kilix` command on `PATH` as
`kilix.path_selected`. They can be different installations. Each record gives
its exact root, observed `VERSION` and Git revision when resolvable, and the
commands whose wrapper dispatch and implementation files are both present.
These are filesystem observations; they do not establish a live terminal,
valid remote-control authentication, provider readiness, or installed models.

`action_backend` reports the static action schema and operation metadata from
that record's `config/agent_actions.py`. A backend file can be present even when
the wrapper has no `action` command. Use the command list together with the
schema. Discovery does not import the backend or execute its code. An explicit
absolute `KILIX_ACTION_MODULE_ROOT` is reported separately.

Needle discovery resolves `kilix-needle` on `PATH`, follows its launcher symlink,
and reports its root and Git revision. Missing version metadata remains unknown.
Its `KILIX_NEEDLE_KILIX` selection is resolved independently; that selector can
name a command or a source directory. The presence of Needle source files does
not establish a loaded model or an available inference service.

Plebian-OS records the coordinated installation in
`/var/lib/plebian-os/versions.env`. The query exposes only named OS/Kilix version,
root, and revision fields from that provenance record. Recorded pins are
separate from observed revisions. `revision_matches_expected` compares the
observed commit with an exact `KILIX_REF` from the process environment, or with
the provenance pin when no environment pin is supplied. A mismatch is reported
without changing the installation. Branch/tag pins remain unresolved.

The query inspects fixed files and selected command paths without recursive
search, CLI probes, setup, installation, or model execution. File reads require
regular files and are capped at 256 KiB (16 KiB for OS provenance). Git is the
only subprocess; each revision query has a one-second deadline and a 2 KiB
output limit. The JSON response is capped at 32 KiB, with bounded strings and
metadata depth. Excessive operation metadata is omitted with
`status: "response_limit"` and `truncated: true`. Missing, malformed, and
unresolvable evidence remains explicit rather than becoming an availability
claim. Credential files, session transcripts, and user model state are never
read.
