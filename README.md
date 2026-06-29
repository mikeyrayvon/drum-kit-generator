# drum-kit-generator

Procedural drum kit synthesizer for Roland TD-11. Generates a random kit from scratch on demand and plays it back in real time via MIDI. Optionally sequences spoken words on each hit, and flashes DMX lights.

Each voice is synthesized from noise, sine sweeps, and inharmonic partials — no samples. Every run produces a different kit.

## Setup

```bash
python3 -m venv .
./bin/pip install numpy sounddevice python-rtmidi pyserial
```

- Install Roland's USB MIDI driver from roland.com so the TD-11 appears as a MIDI device.
- `pyserial` is only required for DMX (Enttec USB Pro Mk2). The drum and word scripts work without it.

## drum_generator.py

Plays the current kit and responds to TD-11 MIDI input.

```bash
# Play whatever is in current_kit/ (generate missing voices if any)
./bin/python3.14 drum_generator.py

# Generate a new random kit and play it
./bin/python3.14 drum_generator.py --generate

# Load a saved kit (copies into current_kit/, generates anything missing)
./bin/python3.14 drum_generator.py --sounds-dir kits/my_kit

# Record a session (auto-increments: sessions/session_1.wav, session_2.wav, …)
./bin/python3.14 drum_generator.py --record

# Route audio to a specific output device
./bin/python3.14 drum_generator.py --list-devices
./bin/python3.14 drum_generator.py --device 2

# Enable DMX flash on every hit (auto-detects Enttec USB Pro Mk2)
./bin/python3.14 drum_generator.py --dmx

# Print every incoming MIDI note (for debugging unmapped pads)
./bin/python3.14 drum_generator.py --log-notes
```

Flags can be combined freely: `--generate --record`, `--sounds-dir kits/x --dmx --device 2`, etc.

### All flags

| Flag | Description |
|------|-------------|
| `--generate` | Synthesize a new random kit, overwrite `current_kit/` |
| `--sounds-dir <path>` | Load kit from directory, generate missing voices |
| `--record [file]` | Record session to WAV; auto-increments to `sessions/session_N.wav` |
| `--device <id\|name>` | Force a specific audio output device |
| `--list-devices` | List available audio output devices and exit |
| `--dmx` | Enable DMX flash via Enttec USB Pro Mk2 (auto-detect) |
| `--dmx-port <path>` | Serial port for Enttec device (implies `--dmx`) |
| `--dmx-channel <n>` | DMX channel to control, 1-indexed (default 1) |
| `--fade <seconds>` | DMX fade-to-black duration (default 0.5) |
| `--list-ports` | List available serial ports and exit |
| `--log-notes` | Print every incoming MIDI note number and velocity |
| `--help` | Show usage |

### Saving a kit

`current_kit/` is always overwritten on `--generate`. Copy it before regenerating:

```bash
cp -r current_kit/ kits/dark_industrial
```

Reload later with `--sounds-dir kits/dark_industrial`. You can delete individual files from a saved kit before loading — missing voices will be generated fresh.

### Voices / MIDI notes

| Pad zone | MIDI note |
|---|---|
| Kick | 36 |
| Snare head | 38 |
| Snare rimshot | 40 |
| Snare cross-stick | 37 |
| Hi-hat closed / foot | 42, 44, 22 |
| Hi-hat open | 46 |
| Hi-hat edge | 26 |
| Tom 1 head / rim | 48, 50 |
| Tom 2 head / rim | 45, 47 |
| Floor tom head / rim | 43, 58 |
| Crash bow / edge | 49, 55 |
| Ride bow / bell / edge | 51, 53, 59 |

---

## word_player.py

Synthesizes each word of a text as a WAV (via macOS `say`) and plays them back one-by-one on any pad hit. The previous word is cut off immediately on each hit.

```bash
# Generate word WAVs and play
./bin/python3.14 word_player.py --generate --text "the quick brown fox"

# Use a text file
./bin/python3.14 word_player.py --generate --text-file lyrics.txt

# Use a specific macOS TTS voice
./bin/python3.14 word_player.py --generate --text "hello world" --voice Samantha

# Play existing current_word_kit/ without regenerating
./bin/python3.14 word_player.py

# Stop at last word instead of looping
./bin/python3.14 word_player.py --no-loop
```

Word WAVs are stored in `current_word_kit/` (overwritten on `--generate`). The terminal prints `[1/4] the`, `[2/4] quick`, etc. as each word plays.

### All flags

| Flag | Description |
|------|-------------|
| `--generate` | Synthesize word WAVs from text, overwrite `current_word_kit/` |
| `--text "…"` | Inline text (required with `--generate`) |
| `--text-file <path>` | Text file (alternative to `--text`) |
| `--voice <name>` | macOS TTS voice (e.g. `Samantha`, `Zoe`). Default: system voice |
| `--no-loop` | Stop at last word instead of wrapping to the first |
| `--record [file]` | Record session to WAV |
| `--device <id\|name>` | Audio output device |
| `--list-devices` | List audio output devices and exit |
| `--log-notes` | Print every incoming MIDI note |
| `--help` | Show usage |

### List available voices

```bash
say -v "?"
```

---

## DMX (Enttec USB Pro Mk2 + Eurolite EDX-1 Mk2)

Each pad hit flashes the connected light at velocity-proportional brightness, then fades to black. Runs inside `drum_generator.py` — no separate process needed.

Set the DMX start address on your dimmer pack to match `--dmx-channel` (default 1).

```bash
# Auto-detect Enttec and flash on channel 1, 0.5s fade
./bin/python3.14 drum_generator.py --dmx

# Custom channel and fade time
./bin/python3.14 drum_generator.py --dmx --dmx-channel 3 --fade 1.0

# Specify port manually if auto-detect fails
./bin/python3.14 drum_generator.py --dmx-port /dev/cu.usbserial-EN260001
```
