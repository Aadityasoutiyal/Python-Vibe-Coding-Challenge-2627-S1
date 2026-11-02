"""
JARVIS voice.
 - WakeListener: the microphone is always open, speech is recognised on this computer
   (Vosk), and JARVIS reacts only when it hears "Jarvis". Everything else is discarded.
 - Speaker: natural neural voices (Microsoft, through edge-tts) with the Windows voice
   as an offline fallback.
"""
import array
import asyncio
import json
import math
import os
import queue
import tempfile
import time

from PySide6.QtCore import QThread, Signal, QObject, QUrl

from Jarvis.core import find_wake

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    from vosk import Model, KaldiRecognizer, SetLogLevel
except Exception:
    Model = None

try:
    import edge_tts
except Exception:
    edge_tts = None

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
except Exception:
    QMediaPlayer = None

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except Exception:
    QTextToSpeech = None


# ------------------------------------------------------------ listening ----
class WakeListener(QThread):
    status = Signal(str)
    level = Signal(float)
    partial = Signal(str)     # live text, only while JARVIS has been addressed
    wake = Signal()           # "Jarvis" heard
    heard = Signal(str)       # the request
    dismissed = Signal()      # false alarm or nobody spoke
    failed = Signal(str)
    ready = Signal()

    _model = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._paused = False
        self._muted = False
        self._awaiting_until = 0.0

    def stop(self):
        self._stop = True

    def set_paused(self, v):
        self._paused = bool(v)

    def set_muted(self, v):
        self._muted = bool(v)

    def arm(self, seconds=8.0):
        """Treat the next thing said as a request, no wake word needed."""
        self._awaiting_until = time.time() + seconds

    def run(self):
        if sd is None or Model is None:
            self.failed.emit("Voice input needs two packages. Run: py -m pip install vosk sounddevice")
            return
        try:
            if WakeListener._model is None:
                self.status.emit("Loading the speech model. The first time this downloads about 40 MB.")
                SetLogLevel(-1)
                WakeListener._model = Model(lang="en-us")
        except Exception as e:
            self.failed.emit("I couldn't load the speech model. Connect to the internet once so it can "
                             f"download. Details: {e}")
            return
        try:
            rec = KaldiRecognizer(WakeListener._model, 16000)
            q = queue.Queue()

            def callback(indata, frames, time_info, status):
                q.put(bytes(indata))

            woke = False
            was_off = False
            with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16",
                                   channels=1, callback=callback):
                self.ready.emit()
                while not self._stop:
                    now = time.time()
                    if self._awaiting_until and now > self._awaiting_until:
                        self._awaiting_until = 0.0
                        woke = False
                        self.dismissed.emit()
                    try:
                        data = q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if self._paused or self._muted:
                        if not was_off:
                            rec.Reset()
                            woke = False
                            was_off = True
                        continue
                    was_off = False
                    samples = array.array("h", data)
                    rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples)))
                    self.level.emit(min(1.0, rms / 5000.0))
                    armed = self._awaiting_until > now
                    if rec.AcceptWaveform(data):
                        text = json.loads(rec.Result()).get("text", "").strip()
                        if text:
                            self._final(text, woke)
                        elif woke and not armed:
                            self.dismissed.emit()
                        woke = False
                    else:
                        part = json.loads(rec.PartialResult()).get("partial", "")
                        if part:
                            if not woke and find_wake(part):
                                woke = True
                                self.wake.emit()
                            if woke or armed:
                                self.partial.emit(part)
        except Exception as e:
            self.failed.emit(f"Microphone problem: {e}")

    def _final(self, text, woke):
        m = find_wake(text)
        if m:
            command = text[m[1]:].strip(" ,.!?")
            if not woke:
                self.wake.emit()
            if command:
                self._awaiting_until = 0.0
                self.heard.emit(command)
            else:
                self._awaiting_until = time.time() + 8.0
                self.status.emit("I'm listening.")
        elif self._awaiting_until and time.time() < self._awaiting_until:
            self._awaiting_until = 0.0
            self.heard.emit(text)
        elif woke:
            self.dismissed.emit()


# -------------------------------------------------------------- speaking ----
class _SynthWorker(QThread):
    ready = Signal(int, str)
    failed = Signal(int, str)

    def __init__(self, speaker):
        super().__init__()
        self.speaker = speaker
        self.q = queue.Queue()

    def submit(self, gen, text, voice, rate):
        self.q.put((gen, text, voice, rate))

    def close(self):
        self.q.put(None)

    def run(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            gen, text, voice, rate = item
            if gen != self.speaker._gen:
                self.ready.emit(gen, "")
                continue
            try:
                fd, path = tempfile.mkstemp(suffix=".mp3", prefix="jarvis_")
                os.close(fd)
                asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(path))
                self.ready.emit(gen, path)
            except Exception:
                self.failed.emit(gen, text)


class Speaker(QObject):
    speaking_changed = Signal(bool)

    VOICES = [
        ("British male (Ryan)", "en-GB-RyanNeural"),
        ("American male (Andrew)", "en-US-AndrewNeural"),
        ("American male (Guy)", "en-US-GuyNeural"),
        ("British female (Sonia)", "en-GB-SoniaNeural"),
        ("American female (Aria)", "en-US-AriaNeural"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.speaking = False
        self.natural = True
        self.voice = "en-GB-RyanNeural"
        self.rate = "+4%"
        self._gen = 0
        self._pending = {}
        self._play = []
        self._playing = False
        self._cur = None
        self._qt = None
        self._qt_speaking = False
        self._buffer = []
        self.player = None
        self.worker = None

        if QMediaPlayer is not None:
            try:
                self.player = QMediaPlayer(self)
                self.audio = QAudioOutput(self)
                self.audio.setVolume(1.0)
                self.player.setAudioOutput(self.audio)
                self.player.mediaStatusChanged.connect(self._media_status)
                self.player.errorOccurred.connect(self._media_error)
            except Exception:
                self.player = None
        if edge_tts is not None and self.player is not None:
            self.worker = _SynthWorker(self)
            self.worker.ready.connect(self._on_ready)
            self.worker.failed.connect(self._on_failed)
            self.worker.start()
        if QTextToSpeech is not None:
            try:
                self._qt = QTextToSpeech()
                self._qt.stateChanged.connect(self._qt_state)
            except Exception:
                self._qt = None

    @property
    def available(self):
        return self.worker is not None or self._qt is not None

    @property
    def natural_available(self):
        return self.worker is not None

    # ---- state ----
    def _update(self):
        on = (self._pending.get(self._gen, 0) > 0 or self._playing or bool(self._play)
              or self._qt_speaking)
        if on != self.speaking:
            self.speaking = on
            self.speaking_changed.emit(on)

    def _qt_state(self, state):
        self._qt_speaking = state == QTextToSpeech.State.Speaking
        self._update()

    # ---- natural voice pipeline ----
    def _on_ready(self, gen, path):
        n = self._pending.get(gen, 0) - 1
        if n <= 0:
            self._pending.pop(gen, None)
        else:
            self._pending[gen] = n
        if path:
            if gen == self._gen:
                self._play.append(path)
                self._next()
            else:
                self._remove(path)
        self._update()

    def _on_failed(self, gen, text):
        n = self._pending.get(gen, 0) - 1
        if n <= 0:
            self._pending.pop(gen, None)
        else:
            self._pending[gen] = n
        if gen == self._gen and self._qt is not None:
            self._qt.say(text)
        self._update()

    def _next(self):
        if self._playing or not self._play:
            return
        path = self._play.pop(0)
        self._cur = path
        self._playing = True
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def _media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            self._finish_current()

    def _media_error(self, *_):
        if self._playing:
            self._finish_current()

    def _finish_current(self):
        if not self._playing:
            return
        self._playing = False
        self.player.setSource(QUrl())
        if self._cur:
            self._remove(self._cur)
            self._cur = None
        self._next()
        self._update()

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    # ---- public API ----
    @staticmethod
    def _clean(text):
        return text.replace("*", "").replace("#", "").replace("`", "").strip()

    def queue(self, text):
        text = self._clean(text)
        if not text:
            return
        if self.natural and self.worker is not None:
            self._pending[self._gen] = self._pending.get(self._gen, 0) + 1
            self.worker.submit(self._gen, text, self.voice, self.rate)
            self._update()
        elif self._qt is not None:
            self._buffer.append(text)

    def flush(self):
        if self._buffer and self._qt is not None:
            self._qt.say(" ".join(self._buffer))
        self._buffer = []

    def say(self, text):
        self.stop()
        self.queue(text)
        self.flush()

    def stop(self):
        self._gen += 1
        self._buffer = []
        for p in self._play:
            self._remove(p)
        self._play = []
        if self.player is not None:
            self.player.stop()
        if self._playing:
            self._playing = False
            self.player.setSource(QUrl())
            if self._cur:
                self._remove(self._cur)
                self._cur = None
        if self._qt is not None:
            self._qt.stop()
        self._update()

    def shutdown(self):
        self.stop()
        if self.worker is not None:
            self.worker.close()
            self.worker.wait(1500)
