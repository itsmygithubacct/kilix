# Kilix 0.2.2 read aloud and dictation

Run `kilix voice doctor` to inspect dependencies and the selected output and
input audio devices. A null output sink is inaudible; a monitor source is not
an ordinary microphone. Output and input are diagnosed independently, so a
real microphone can remain usable when output is null.

Run `kilix speak "hello"` to read explicit text aloud. Run `kilix dictate`
to recognize one utterance and insert text without pressing Enter.
The speaking-head and microphone controls in the page strip perform these
actions; desktop-menu entries open settings and diagnostics instead.

Speech actions target terminal text. Pixel applications such as Kilix 95
have no readable terminal cells or visible terminal input target, so switch
to a terminal pane first. Dictation checks the original target again before
inserting; if it became a pixel application or a hidden password prompt,
the transcript is discarded.

Run `kilix tts` for read-aloud settings and `kilix stt` for dictation settings.
Run `kilix stt --models` for the speech catalog and install/runtime state;
`kilix stt --models --json` emits the versioned machine-readable catalog.
Use the reported runtime support rather than assuming downloaded weights run.
Opening STT settings does not fetch a recognizer library or acoustic model.
Use the explicit install choices in the speech settings or
`kilix stt --install MODEL --default MODEL` with a listed supported model ID.
