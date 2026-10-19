import io
import os
import wave
from functools import lru_cache
from pathlib import Path


DEFAULT_MODEL_PATH = Path.home() / ".local/share/blinkassist/voices/en_US-lessac-medium.onnx"


@lru_cache(maxsize=1)
def _load_voice(model_path):
    from piper import PiperVoice

    return PiperVoice.load(model_path)


def synthesize_speech(text):
    model_path = Path(os.environ.get("PIPER_MODEL_PATH", DEFAULT_MODEL_PATH)).expanduser()
    if not model_path.is_file():
        raise FileNotFoundError(model_path)

    audio_buffer = io.BytesIO()
    voice = _load_voice(str(model_path.resolve()))
    with wave.open(audio_buffer, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    return audio_buffer.getvalue()