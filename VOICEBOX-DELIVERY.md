# Offline Voicebox delivery

Build the interpreted web package from clean committed sources and its frozen
dependencies using Voicebox's tools/build_web_delivery.py. Select an explicit
Linux x86_64 package file and SHA256; installation never downloads software,
models or license receipts:

```
kilix voicebox prepare --package /absolute/voicebox.tar.gz --sha256 FULL_SHA256
kilix voicebox status
kilix voicebox run
kilix voicebox update --package /absolute/new-voicebox.tar.gz --sha256 FULL_SHA256
kilix voicebox rollback
kilix voicebox uninstall
```

Code generations are verified before atomic selection and launch. An active
window prevents lifecycle mutations. Uninstall clears the selection and retains
generations and XDG product data for recovery. Rollback changes code selection;
it does not rewind profiles, samples, history, exports or shared provider state.
Interrupted staging remains unselected. Missing packages/browser/private-display
prerequisites refuse with retained private launch diagnostics.

`run` uses existing Kilix private XAppSession delivery. It requires Chromium with
its sandbox enabled, Xvfb, xauth, dbus-run-session and host X11/terminal support.
The backend listens on an inherited ephemeral loopback socket. A new private
browser profile receives a fresh per-launch fragment credential; every API/media
resource remains authenticated. Closing the window reaps its exact owned backend
and descendants while shared speech providers continue separately. Explicit
browser downloads go to XDG_DATA_HOME/kilix-voicebox/exports.

Providers must be prepared and supervised separately with genuine Content asset
agreement/population admission. Optional explicit `--voice-source /absolute`
`--voice-ref FULL_COMMIT --lease-source /absolute --lease-ref FULL_COMMIT` on
Qwen/Transcribe preparation stages the selected managed Voice and canonical
lease closure. Supply all four fields together. The generated launcher binds
and rechecks every library/shared byte and selects the existing lease policy.
New preparation includes the pinned canonical lease API in the bound provider
generation. The four explicit arguments additionally select managed recording
and the shared execution policy. Rebuild older generations with `prepare` to
include the new provider and library pins. No fixture
or unreviewed source becomes an integrated pin automatically.

The interpreted package has its own mandatory verifier. It does not issue or
bypass the separately gated Tauri sidecar authority. Synthetic product tests
prove interface/data/lifetime behavior; model quality, resource fit, physical
microphones and actual guest/pane acceptance require their own evidence.
