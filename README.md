# drum_generator

Procedural drum kit synthesizer for Roland TD-11. Generates a random kit from scratch on demand and plays it back in real time via MIDI.

Each voice is synthesized from noise, sine sweeps, and inharmonic partials — no samples. Every run produces a different kit.

## Setup

```bash
python3 -m venv .
./bin/pip install numpy sounddevice python-rtmidi
```

Install Roland's USB MIDI driver from roland.com so the TD-11 appears as a MIDI device.

## Usage

```bash
# Play whatever is in current_kit/ (generate missing voices if any)
./bin/python3.14 drum_generator.py

# Generate a new random kit and play it
./bin/python3.14 drum_generator.py --generate

# Load a saved kit (copy into current_kit/, generate anything missing)
./bin/python3.14 drum_generator.py --sounds-dir kits/my_kit

# Record a session (auto-increments: sessions/session_1.wav, session_2.wav, …)
./bin/python3.14 drum_generator.py --record

# Record to a specific file
./bin/python3.14 drum_generator.py --record sessions/take3.wav
```

Flags can be combined: `--generate --record`, `--sounds-dir kits/x --record`, etc.

## Saving a kit

`current_kit/` is always overwritten on `--generate`. Copy it before regenerating to keep a kit:

```bash
cp -r current_kit/ kits/dark_industrial
```

Reload it later with `--sounds-dir kits/dark_industrial`.

You can also mix kits by deleting individual files from a saved directory before loading — missing voices will be generated fresh.

## Voices

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

## Debug

```bash
# Print every incoming MIDI note number and velocity
./bin/python3.14 drum_generator.py --log-notes
```

Use this if a pad produces no sound — the note number it sends may differ from the defaults above and need to be added to `NOTE_MAP` in the script.
