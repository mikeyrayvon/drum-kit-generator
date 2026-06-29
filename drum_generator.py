#!/usr/bin/env python3
"""
Procedural drum kit generator.
Synthesizes a randomized drum kit on each run and plays it back
via MIDI triggers from a Roland TD-11.

Usage:
    python drum_generator.py
    python drum_generator.py --log-notes   # print every incoming MIDI note (for debugging)
"""

import sys
import time
import wave
import shutil
import struct
import threading
import random
from pathlib import Path
import rtmidi
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 44100


# ── Helpers ──────────────────────────────────────────────────────────────────

def normalize(x, peak=0.88):
    m = np.max(np.abs(x))
    return (x / m * peak if m > 0 else x).astype(np.float32)


def colored_noise(n, low_hz, high_hz):
    """Band-limited noise via FFT — fast and purely vectorized."""
    raw = np.random.randn(n)
    spec = np.fft.rfft(raw)
    freqs = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
    spec *= (freqs >= low_hz) & (freqs <= high_hz)
    return np.fft.irfft(spec, n)


def env(n, decay_s, attack_s=0.001):
    """Simple attack + exponential decay envelope."""
    t = np.arange(n) / SAMPLE_RATE
    atk = np.minimum(t / attack_s, 1.0)
    dec = np.exp(-t / decay_s)
    return atk * dec


# ── Synthesis functions ───────────────────────────────────────────────────────

def saturate(x, amount):
    """Soft-clip waveshaper. amount=0 is clean, 1.0 is heavily saturated."""
    if amount < 0.01:
        return x
    drive = 1 + amount * 12
    return np.tanh(x * drive) / np.tanh(np.array(drive))


def synth_kick(f_start, f_end, decay, punch, distort):
    duration = decay * 4
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    freq = f_end + (f_start - f_end) * np.exp(-t / (decay * 0.25))
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    body = np.sin(phase) * env(n, decay)
    click = colored_noise(n, 60, 8000) * np.exp(-t / 0.004) * punch
    return saturate(body + click, distort)


def synth_snare(noise_decay, tone_freq, tone_ratio, distort):
    duration = noise_decay * 5
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    noise_sig = colored_noise(n, 200, 10000) * env(n, noise_decay)
    tone = np.sin(2 * np.pi * tone_freq * t) * env(n, noise_decay * 1.8) * tone_ratio
    return saturate(noise_sig + tone, distort)


def synth_rim(ring_freq, decay):
    duration = max(decay * 6, 0.15)
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    click = colored_noise(n, 1000, 12000) * np.exp(-t * 120)
    ring = np.sin(2 * np.pi * ring_freq * t) * env(n, decay)
    return click * 0.4 + ring * 0.8


def synth_hihat(low_hz, high_hz, decay):
    duration = max(decay * 6, 0.06)
    n = int(SAMPLE_RATE * duration)
    return colored_noise(n, low_hz, high_hz) * env(n, decay, attack_s=0.0005)


def synth_hihat_foot(freq, decay):
    duration = decay * 6
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    body = np.sin(2 * np.pi * freq * t) * env(n, decay)
    click = colored_noise(n, 500, 8000) * np.exp(-t * 100) * 0.3
    return body + click


def synth_tom(f_start, f_end, decay):
    duration = decay * 4
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    freq = f_end + (f_start - f_end) * np.exp(-t / (decay * 0.2))
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    body = np.sin(phase) * env(n, decay)
    attack = colored_noise(n, 500, 8000) * np.exp(-t * 80) * 0.25
    return body + attack


def synth_crash(low_hz, high_hz, decay):
    duration = decay * 3
    n = int(SAMPLE_RATE * duration)
    return colored_noise(n, low_hz, high_hz) * env(n, decay, attack_s=0.002)


def synth_ride_bell(f, decay):
    duration = decay * 3
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    # Inharmonic partials give a metallic bell character
    partials = [(1.000, 1.00), (2.756, 0.55), (5.404, 0.28), (8.933, 0.14)]
    sig = np.zeros(n)
    for ratio, amp in partials:
        sig += amp * np.sin(2 * np.pi * f * ratio * t) * env(n, decay / ratio)
    return sig


def synth_ride_bow(low_hz, high_hz, decay):
    duration = decay * 3
    n = int(SAMPLE_RATE * duration)
    # Blend metallic shimmer noise with a subtle bell partial
    t = np.arange(n) / SAMPLE_RATE
    noise_sig = colored_noise(n, low_hz, high_hz) * env(n, decay * 0.6)
    shimmer = np.sin(2 * np.pi * random.uniform(low_hz, low_hz * 2) * t) * env(n, decay) * 0.15
    return noise_sig + shimmer


# ── Voice table ───────────────────────────────────────────────────────────────

def build_voices():
    r = random.uniform
    voices = {
        "kick":          (synth_kick,      dict(f_start=r(40,220),    f_end=r(18,80),     decay=r(0.08,2.5),  punch=r(0,1.2),    distort=r(0,0.8))),
        "snare_head":    (synth_snare,     dict(noise_decay=r(0.04,0.18), tone_freq=r(150,400),  tone_ratio=r(0,0.4),  distort=r(0,0.7))),
        "snare_rimshot": (synth_rim,       dict(ring_freq=r(200,5000), decay=r(0.02,0.35))),
        "snare_xstick":  (synth_rim,       dict(ring_freq=r(400,6000), decay=r(0.02,0.20))),
        "hihat_closed":  (synth_hihat,     dict(low_hz=r(2000,12000), high_hz=r(8000,20000),  decay=r(0.01,0.15))),
        "hihat_open":    (synth_hihat,     dict(low_hz=r(800,8000),   high_hz=r(5000,20000),  decay=r(0.1,2.5))),
        "tom1_head":     (synth_tom,       dict(f_start=r(80,400),    f_end=r(40,200),     decay=r(0.1,1.5))),
        "tom1_rim":      (synth_rim,       dict(ring_freq=r(200,3000), decay=r(0.04,0.35))),
        "tom2_head":     (synth_tom,       dict(f_start=r(60,300),    f_end=r(30,150),     decay=r(0.15,1.8))),
        "tom2_rim":      (synth_rim,       dict(ring_freq=r(150,2500), decay=r(0.05,0.40))),
        "floortom_head": (synth_tom,       dict(f_start=r(40,200),    f_end=r(20,100),     decay=r(0.2,2.5))),
        "floortom_rim":  (synth_rim,       dict(ring_freq=r(100,1500), decay=r(0.06,0.45))),
        "crash_bow":     (synth_crash,     dict(low_hz=r(300,5000),   high_hz=r(4000,20000),  decay=r(0.3,5.0))),
        "crash_edge":    (synth_crash,     dict(low_hz=r(800,8000),   high_hz=r(6000,20000),  decay=r(0.2,3.0))),
        "ride_bow":      (synth_ride_bow,  dict(low_hz=r(500,5000),   high_hz=r(4000,14000),  decay=r(0.2,2.5))),
        "ride_bell":     (synth_ride_bell, dict(f=r(150,1800),         decay=r(0.2,4.0))),
        "ride_edge":     (synth_crash,     dict(low_hz=r(1000,7000),  high_hz=r(6000,20000),  decay=r(0.2,2.5))),
        "hihat_edge":    (synth_crash,     dict(low_hz=r(2000,10000), high_hz=r(8000,20000),  decay=r(0.05,0.6))),
    }
    return voices


# Standard Roland TD-11 MIDI note → voice label
NOTE_MAP = {
    36: "kick",
    38: "snare_head",
    40: "snare_rimshot",
    37: "snare_xstick",
    48: "tom1_head",
    50: "tom1_rim",
    45: "tom2_head",
    47: "tom2_rim",
    43: "floortom_head",
    58: "floortom_rim",
    22: "hihat_closed",
    26: "hihat_edge",
    42: "hihat_closed",
    44: "hihat_closed",
    46: "hihat_open",
    49: "crash_bow",
    55: "crash_edge",
    51: "ride_bow",
    53: "ride_bell",
    59: "ride_edge",
}


# ── Kit generation ────────────────────────────────────────────────────────────

def _write_wav(path, x):
    data = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(data.tobytes())


def _read_wav(path):
    with wave.open(str(path), "r") as f:
        raw = f.readframes(f.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0


CURRENT_KIT_DIR = Path(__file__).parent / "current_kit"


def _populate_current_kit(src=None, generate_all=False):
    """Fill current_kit/ and return sounds dict.

    generate_all: ignore existing files, synthesize everything fresh.
    src: optional directory to copy from before filling gaps.
    """
    needed = set(NOTE_MAP.values())
    voices = build_voices()
    sounds = {}

    CURRENT_KIT_DIR.mkdir(exist_ok=True)

    if src:
        print(f"Loading kit from {src}/ → {CURRENT_KIT_DIR}/")
    elif generate_all:
        print("Generating kit...")
    else:
        print(f"Using {CURRENT_KIT_DIR}/")

    for label in needed:
        dst = CURRENT_KIT_DIR / f"{label}.wav"

        if generate_all:
            fn, params = voices[label]
            audio = normalize(fn(**params))
            _write_wav(dst, audio)
            sounds[label] = audio
            param_str = "  ".join(f"{k}={v:.3f}" for k, v in params.items())
            print(f"  {label:<20s} generated  {param_str}")
        elif src and (src / f"{label}.wav").exists():
            shutil.copy2(src / f"{label}.wav", dst)
            sounds[label] = _read_wav(dst)
            print(f"  {label:<20s} copied")
        elif dst.exists():
            sounds[label] = _read_wav(dst)
            print(f"  {label:<20s} loaded")
        else:
            fn, params = voices[label]
            audio = normalize(fn(**params))
            _write_wav(dst, audio)
            sounds[label] = audio
            param_str = "  ".join(f"{k}={v:.3f}" for k, v in params.items())
            print(f"  {label:<20s} generated  {param_str}")

    print()
    return sounds


# ── Simple real-time mixer ────────────────────────────────────────────────────

class Mixer:
    """Mixes active voices in a sounddevice output callback."""

    def __init__(self, sr=SAMPLE_RATE, blocksize=512, record_path=None, device=None):
        self._lock = threading.Lock()
        self._playing = {}  # channel_id -> [audio_f32, position]
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

    def stop(self, ch_id):
        with self._lock:
            self._playing.pop(ch_id, None)

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


# ── DMX ───────────────────────────────────────────────────────────────────────

def _dmx_packet(channels: bytearray) -> bytes:
    data = bytes([0x00]) + bytes(channels)
    return b'\x7E\x06' + struct.pack('<H', len(data)) + data + b'\xE7'


def find_enttec():
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return None
    for p in comports():
        if p.vid == 0x0403:
            return p.device
        if 'EN' in (p.serial_number or ''):
            return p.device
    return None


class DMXFlasher:
    def __init__(self, port, dmx_channel=1, fade_s=0.5):
        import serial
        self._ser = serial.Serial(port, 57600, timeout=1)
        self._ch_idx = dmx_channel - 1
        self._channels = bytearray(512)
        self._brightness = 0.0
        self._fade_step = 255.0 / (fade_s * 60)
        self._lock = threading.Lock()
        threading.Thread(target=self._fade_loop, daemon=True).start()

    def flash(self, velocity):
        with self._lock:
            self._brightness = velocity / 127.0 * 255.0

    def _fade_loop(self):
        while True:
            with self._lock:
                if self._brightness > 0:
                    self._brightness = max(0.0, self._brightness - self._fade_step)
                    self._channels[self._ch_idx] = int(self._brightness)
                    self._ser.write(_dmx_packet(self._channels))
            time.sleep(1 / 60)

    def close(self):
        with self._lock:
            self._channels[self._ch_idx] = 0
        self._ser.write(_dmx_packet(self._channels))
        self._ser.close()


# ── MIDI player ───────────────────────────────────────────────────────────────

class DrumPlayer:
    def __init__(self, sounds, log_notes=False, record_path=None, device=None, dmx_flasher=None):
        self.log_notes = log_notes
        self.dmx_flasher = dmx_flasher
        self.sounds = {note: sounds[label] for note, label in NOTE_MAP.items()}
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

        if self.log_notes and status == 0x90 and data[2] > 0:
            print(f"  note={data[1]}  vel={data[2]}")

        if status != 0x90 or data[2] == 0:
            return

        note, velocity = data[1], data[2]

        # Closed hi-hat bow and foot pedal both choke the open hi-hat
        if note in (22, 42, 44):
            self.mixer.stop(46)

        if note not in self.sounds:
            if self.log_notes:
                print(f"  (unmapped note {note})")
            return

        self.mixer.play(note, self.sounds[note], volume=velocity / 127.0)

        if self.dmx_flasher:
            self.dmx_flasher.flash(velocity)

    def run(self):
        print(f"Ready — {len(NOTE_MAP)} voices. Ctrl-C to quit.\n")
        try:
            while True:
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nExiting.")
        finally:
            self.midi_in.close_port()
            self.mixer.close()
            if self.dmx_flasher:
                self.dmx_flasher.close()


# ── Entry point ───────────────────────────────────────────────────────────────

HELP = """
Usage: drum_generator.py [options]

Sound source (pick one, default is current_kit/):
  --generate              Synthesize a new random kit, overwrite current_kit/
  --sounds-dir <path>     Copy sounds from <path> into current_kit/, generate
                          any voices missing from that directory

Playback:
  --record [<file>]       Record the session to a WAV file. If no filename is
                          given, auto-increments: session_1.wav, session_2.wav…
  --device <id|name>      Force a specific audio output device (use
                          --list-devices to see options)
  --list-devices          Print available audio output devices and exit

DMX lighting (Enttec USB Pro Mk2):
  --dmx                   Enable DMX; auto-detects Enttec device
  --dmx-port <path>       Serial port for the Enttec (implies --dmx)
  --dmx-channel <n>       DMX channel to control, 1-indexed (default 1)
  --fade <seconds>        Fade-to-black duration in seconds (default 0.5)
  --list-ports            Print available serial ports and exit

Workflow:
  current_kit/            Always the active kit. Copy it elsewhere to save it.
                          e.g.  cp -r current_kit/ kits/my_favourite

Debug:
  --log-notes             Print every incoming MIDI note number and velocity
  --help                  Show this message
"""

if __name__ == "__main__":
    args = sys.argv[1:]

    def flag(name):
        return name in args

    def flag_val(name, default=None):
        if name in args:
            idx = args.index(name)
            return args[idx + 1] if idx + 1 < len(args) else default
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

    if flag("--list-ports"):
        from serial.tools.list_ports import comports
        print("Serial ports:")
        for p in comports():
            print(f"  {p.device}  {p.description}  vid={p.vid:#06x}")
        sys.exit(0)

    log_notes = flag("--log-notes")

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

    sounds_dir = flag_val("--sounds-dir")
    if flag("--generate"):
        sounds = _populate_current_kit(generate_all=True)
    elif sounds_dir:
        sounds = _populate_current_kit(src=Path(sounds_dir))
    else:
        sounds = _populate_current_kit()

    device = flag_val("--device")
    if device is not None and device.isdigit():
        device = int(device)

    dmx_flasher = None
    if flag("--dmx") or flag_val("--dmx-port"):
        dmx_port = flag_val("--dmx-port") or find_enttec()
        if not dmx_port:
            print("Error: Enttec USB Pro not found. Use --dmx-port <path>.", file=sys.stderr)
            sys.exit(1)
        dmx_ch = int(flag_val("--dmx-channel") or 1)
        fade_s = float(flag_val("--fade") or 0.5)
        dmx_flasher = DMXFlasher(dmx_port, dmx_channel=dmx_ch, fade_s=fade_s)
        print(f"DMX: {dmx_port}  channel {dmx_ch}  fade {fade_s}s")

    DrumPlayer(sounds, log_notes=log_notes, record_path=record_path,
               device=device, dmx_flasher=dmx_flasher).run()
