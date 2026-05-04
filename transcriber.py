"""
Speech-to-text transcription using faster-whisper (local, no API calls).

Model sizes vs accuracy/speed tradeoff:
  tiny   (~75MB)  — fastest, decent for clear speech
  base   (~145MB) — good balance
  small  (~465MB) — noticeably better accuracy
  medium (~1.5GB) — high accuracy
  large  (~3GB)   — best accuracy, slower

Models download from HuggingFace on first use and are cached in ~/.cache/huggingface/.
"""

import numpy as np
from faster_whisper import WhisperModel

DEFAULT_MODEL = "base"


class Transcriber:
    def __init__(self, model_size: str = DEFAULT_MODEL, language: str | None = None):
        self.language = language
        self.model_size = model_size
        print(f"[TalkTalk] loading Whisper model: {model_size!r}")
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        print(f"[TalkTalk] Whisper model ready: {model_size!r}")

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16_000,
        initial_prompt: str | None = None,
        task: str = "transcribe",
    ) -> tuple[str, str]:
        """
        Transcribe a float32 numpy audio array.
        Returns (transcript, detected_language_code) e.g. ("hello", "en").

        task: "transcribe" — output in the detected/source language
              "translate"  — translate output to English (Whisper built-in)
        """
        if audio is None or len(audio) == 0:
            return "", "en"

        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            task=task,
            beam_size=1,
            initial_prompt=initial_prompt,
            vad_filter=True,          # skip silent regions — measurable speedup
            condition_on_previous_text=False,
        )

        transcript = " ".join(segment.text.strip() for segment in segments)
        return transcript, info.language
