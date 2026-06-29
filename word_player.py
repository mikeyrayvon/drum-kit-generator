#!/usr/bin/env python3
"""
Word sequencer: synthesizes each word of a text as a WAV (via macOS say),
then plays them one-by-one on MIDI hits from a Roland TD-11.
Any pad advances to the next word; the previous word is cut off immediately.

Usage:
    word_player.py --generate --text "the quick brown fox"
    word_player.py                       # load existing current_word_kit/
    word_player.py --generate --text-file lyrics.txt --voice Zoe
"""

import os
import re
import sys
import time
import wave
import tempfile
import subprocess
import threading
from pathlib import Path
import rtmidi
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 44100
CURRENT_WORD_KIT_DIR = Path(__file__).parent / "current_word_kit"


# ── WAV I/O ───────────────────────────────────────────────────────────────────

def _read_wav(path):
    with wave.open(str(path), "r") as f:
        raw = f.readframes(f.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0


# ── TTS generation ────────────────────────────────────────────────────────────

def _run_subprocess(cmd):
    """Run a command, terminating it cleanly if Ctrl-C is pressed."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        raise
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def generate_word_kit(text, voice=None, kit_dir=CURRENT_WORD_KIT_DIR):
    words = text.split()
    if not words:
        raise ValueError("Text contains no words.")

    kit_dir.mkdir(exist_ok=True)
    for f in kit_dir.glob("*.wav"):
        f.unlink()

    voice_str = voice if voice else "system default"
    print(f"Generating {len(words)} word(s) — voice: {voice_str}")
    print(f"Output: {kit_dir}/")

    for i, word in enumerate(words):
        safe = re.sub(r"[^\w]", "_", word)
        out_path = kit_dir / f"{i:03d}_{safe}.wav"

        # say writes AIFF by default; afconvert converts to WAV
        fd, tmp_path = tempfile.mkstemp(suffix=".aiff")
        try:
            os.close(fd)
            say_cmd = ["say", "-o", tmp_path, word]
            if voice:
                say_cmd += ["-v", voice]
            _run_subprocess(say_cmd)
            _run_subprocess(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", tmp_path, str(out_path)])
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        print(f"  {i:03d}  {word}")

    (kit_dir / "text.txt").write_text(text)
    print()


def load_word_kit(kit_dir=CURRENT_WORD_KIT_DIR):
    wavs = sorted(kit_dir.glob("*.wav"))
    if not wavs:
        raise RuntimeError(
            f"No WAV files found in {kit_dir}/. Run with --generate first."
        )
    words = []
    for path in wavs:
        parts = path.stem.split("_", 1)
        label = parts[1] if len(parts) == 2 else path.stem
        label = label.replace("_", " ").strip()
        words.append((label, _read_wav(path)))
    print(f"Loaded {len(words)} word(s) from {kit_dir}/")
    if (kit_dir / "text.txt").exists():
        print(f'  "{(kit_dir / "text.txt").read_text().strip()}"')
    print()
    return words


# ── Real-time mixer (mirrored from drum_generator.py) ─────────────────────────

class Mixer:
    def __init__(self, sr=SAMPLE_RATE, blocksize=512, record_path=None, device=None):
        self._lock = threading.Lock()
        self._playing = {}
        self._record_path = record_path
        self._recorded = [] if record_path else None
        dev_info = sd.query_devices(device if device is not None else sd.default.device[1], "output")
        n_ch = min(int(dev_info["max_output_channels"]), 2)
        self._stream = sd.OutputStream(
            samplerate=sr,
            channels=n_ch,
            dtype="float32",
            blocksize=blocksize,
            callback=self._callback,
            device=device,
        )
        self._sr = sr
        self._stream.start()

    def _callback(self, outdata, frames, _time, _status):
        with self._lock:
            buf = np.zeros(frames, dtype=np.float32)
            done = []
            for ch_id, state in self._playing.items():
                audio, pos = state
                end = min(pos + frames, len(audio))
                chunk = audio[pos:end]
                buf[:len(chunk)] += chunk
                if end >= len(audio):
                    done.append(ch_id)
                else:
                    state[1] = end
            for ch_id in done:
                del self._playing[ch_id]
        out = np.clip(buf, -1.0, 1.0)
        outdata[:] = out[:, np.newaxis]  # broadcast mono mix to all channels
        if self._recorded is not None:
            self._recorded.append(out.copy())

    def play(self, ch_id, audio, volume=1.0):
        with self._lock:
            self._playing[ch_id] = [audio * volume, 0]

    def close(self):
        self._stream.stop()
        self._stream.close()
        if self._record_path and self._recorded:
            data = (np.concatenate(self._recorded) * 32767).astype(np.int16)
            with wave.open(self._record_path, "w") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(self._sr)
                f.writeframes(data.tobytes())
            print(f"Saved recording to {self._record_path}")


# ── Word sequencer ────────────────────────────────────────────────────────────

class WordPlayer:
    def __init__(self, words, log_notes=False, record_path=None, device=None, loop=True):
        self._words = words  # list of (label_str, audio_f32)
        self._idx = 0
        self._loop = loop
        self._log_notes = log_notes
        self.mixer = Mixer(record_path=record_path, device=device)

        self.midi_in = rtmidi.MidiIn()
        n_ports = self.midi_in.get_port_count()
        if n_ports == 0:
            raise RuntimeError("No MIDI ports found. Connect the TD-11 and try again.")

        port = None
        for i in range(n_ports):
            if "TD-11" in self.midi_in.get_port_name(i):
                port = i
                break

        if port is None:
            print("TD-11 not found. Available ports:")
            for i in range(n_ports):
                print(f"  {i}: {self.midi_in.get_port_name(i)}")
            port = int(input("Select port number: "))

        self.midi_in.open_port(port)
        self.midi_in.set_callback(self._on_midi)
        print(f"Connected: {self.midi_in.get_port_name(port)}")

    def _on_midi(self, message, _):
        data = message[0]
        status = data[0] & 0xF0

        if self._log_notes and status == 0x90 and data[2] > 0:
            print(f"  note={data[1]}  vel={data[2]}")

        if status != 0x90 or data[2] == 0:
            return

        velocity = data[2]
        label, audio = self._words[self._idx]
        print(f"[{self._idx + 1}/{len(self._words)}] {label}")
        # ch_id=0 always: overwrites the previous word instantly
        self.mixer.play(0, audio, volume=velocity / 127.0)

        at_end = self._idx == len(self._words) - 1
        if not at_end:
            self._idx += 1
        elif self._loop:
            self._idx = 0

    def run(self):
        loop_str = "loops" if self._loop else "stops at last word"
        print(f"Ready — {len(self._words)} word(s), {loop_str}. Ctrl-C to quit.\n")
        try:
            while True:
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nExiting.")
        finally:
            self.midi_in.close_port()
            self.mixer.close()


# ── Entry point ───────────────────────────────────────────────────────────────

HELP = """
Usage: word_player.py [options]

Generate word kit:
  --generate              Synthesize word WAVs from text, overwrite current_word_kit/
  --text "some text"      Inline text to synthesize (required with --generate)
  --text-file <path>      Path to a text file (alternative to --text)
  --voice <name>          macOS TTS voice (e.g. Samantha, Zoe). Default: system voice.

Playback:
  --no-loop               Stop at last word instead of wrapping to the first
  --record [<file>]       Record session to WAV. Auto-increments: session_1.wav…
  --device <id|name>      Force a specific audio output device
  --list-devices          Print available audio output devices and exit

Debug:
  --log-notes             Print every incoming MIDI note number and velocity
  --help / -h             Show this message
"""

if __name__ == "__main__":
    args = sys.argv[1:]

    def flag(name):
        return name in args

    def flag_val(name, default=None):
        if name in args:
            idx = args.index(name)
            if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
                return args[idx + 1]
            return default
        return None

    if flag("--help") or flag("-h"):
        print(HELP)
        sys.exit(0)

    if flag("--list-devices"):
        print("Output devices:")
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_output_channels"] > 0:
                marker = " *" if i == sd.default.device[1] else ""
                print(f"  {i:2d}  {dev['name']}{marker}")
        sys.exit(0)

    log_notes = flag("--log-notes")
    loop = not flag("--no-loop")

    record_path = flag_val("--record")
    if flag("--record") and record_path is None:
        sessions_dir = Path(__file__).parent / "sessions"
        sessions_dir.mkdir(exist_ok=True)
        n = 1
        while (sessions_dir / f"session_{n}.wav").exists():
            n += 1
        record_path = str(sessions_dir / f"session_{n}.wav")
    if record_path:
        print(f"Recording to {record_path}")

    if flag("--generate"):
        text = flag_val("--text")
        text_file = flag_val("--text-file")
        if text_file:
            text = Path(text_file).read_text()
        if not text:
            print("Error: --generate requires --text or --text-file.", file=sys.stderr)
            sys.exit(1)
        voice = flag_val("--voice")
        try:
            generate_word_kit(text.strip(), voice=voice)
        except KeyboardInterrupt:
            print("\nGeneration interrupted.")
            sys.exit(1)

    words = load_word_kit()

    device = flag_val("--device")
    if device is not None and device.isdigit():
        device = int(device)

    WordPlayer(words, log_notes=log_notes, record_path=record_path, device=device, loop=loop).run()
