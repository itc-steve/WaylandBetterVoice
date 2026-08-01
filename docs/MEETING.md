# Meetings

`wbv meeting toggle` (`Super+Alt+Space`) records two 16 kHz mono streams at once:

- **Microphone** — the default PipeWire source. Always labeled `Me`.
- **System audio** — the sink that is actually playing (see below). This is the one
  stream that carries every remote participant, so it is the only stream that gets
  diarized.

Both streams are transcribed in `meeting_chunk_seconds` windows by the whisper model
already resident in VRAM. Output lands in
`~/.local/share/waylandbettervoice/meetings/<timestamp>/`:

| File | Contents |
|---|---|
| `transcript.md` | Readable, `## Speaker` headings, `[mm:ss]` stamps |
| `transcript.json` | `[{start, end, speaker, source, text}]` |
| `mix.wav` | Mic + system audio summed, 16 kHz mono |

The transcript is rewritten after every chunk, so a crash mid-meeting still leaves a
usable file.

## Which sink gets recorded

Not the default sink. With EasyEffects (or any filter chain) applications play into the
effects sink and only its output reaches hardware, so recording the *default* sink
captures silence. `audio.resolve_default_sink()` picks the sink in `RUNNING` state,
preferring a physical `alsa_*` device, and only falls back to `pactl get-default-sink`
when nothing is playing.

`pw-record` also cannot record a `.monitor` target directly — it returns silence. The
working form is the sink node plus `-P '{ stream.capture.sink=true }'`, which is what
`audio.Capture(monitor=True)` does.

## Speaker labels (optional)

Mic audio is always `Me`. System audio is embedded with `sherpa_onnx` and assigned to the
nearest online cosine cluster: `Speaker 1`, `Speaker 2`, … `meeting_speakers: 0` clusters
automatically; a positive value caps the number of clusters.

Without the module or a model, recording and transcription continue normally and every
system segment is labeled `Speaker ?` (one warning in the log).

Install on Arch:

```sh
yay -S python-sherpa-onnx        # pulls onnxruntime; CPU build is enough
mkdir -p ~/.local/share/waylandbettervoice/models/speaker
cd ~/.local/share/waylandbettervoice/models/speaker
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx
```

Any 3D-Speaker, NeMo, or WeSpeaker speaker-verification `.onnx` in that directory is
picked up (first match, sorted). English options from the same release:
`nemo_en_titanet_small.onnx` (fast), `nemo_en_titanet_large.onnx` (more accurate),
`wespeaker_en_voxceleb_CAM++.onnx`. Full list:
<https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models>

Restart the daemon after installing: `systemctl --user restart waylandbettervoice`.

## Limits

- Labels are per chunk. Two people talking over each other, or a speaker change inside
  one chunk, gets a single label.
- Cluster numbers are local to one meeting and are not identities. `Speaker 2` in two
  different meetings is not the same person.
- Chunk boundaries can cut a word in half; the next chunk usually recovers it.
- `mix.wav` aligns the two streams by capture order, not sample-accurate PipeWire
  timestamps. Good enough to review, not for forensic alignment.
- The RMS gate drops near-silent chunks, which also drops very quiet speech.
