import numpy as np
from typing import Generator, Optional
from pathlib import Path

import mlx.core as mx
import soundfile as sf
from mlx_audio.tts.utils import load_model as load_tts

class ChatterboxTTS:
    """Chatterbox Turbo TTS backend with voice cloning support."""

    # Chunk size in samples (120ms at 24kHz = 2880 samples)
    # This MUST match the Opus frame size (OPUS_FRAME_SAMPLES in utils.py)
    CHUNK_SAMPLES = 2880

    def __init__(
        self,
        model_id: str = "mlx-community/chatterbox-turbo-fp16",
        ref_audio_path: Optional[str] = None,
        output_sample_rate: int = 24_000,
        temperature: float = 0.8,
        top_k: int = 1000,
        top_p: float = 0.95,
        repetition_penalty: float = 1.2,
        stream: bool = False,
        streaming_interval: float = 1.5,
    ):
        self.model_id = model_id
        self.ref_audio_path = ref_audio_path
        self.output_sample_rate = output_sample_rate
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.repetition_penalty = repetition_penalty
        self.stream = stream
        self.streaming_interval = streaming_interval
        self.model = None

    def load(self) -> None:
        """Load the Chatterbox model and prepare conditionals if ref audio provided."""
        self.model = load_tts(self.model_id)

        if self.ref_audio_path:
            self.model.prepare_conditionals(self.ref_audio_path)

    def prepare_ref_audio(self, ref_audio_path: Optional[str]) -> None:
        if not self.model:
            raise RuntimeError("TTS model not loaded")
        if ref_audio_path:
            self.model.prepare_conditionals(ref_audio_path)
            self.ref_audio_path = ref_audio_path
        else:
            self.ref_audio_path = None

    def generate(self, text: str, ref_audio_path: Optional[str] = None) -> Generator[bytes, None, None]:
        """Generate audio chunks for the given text."""
        if ref_audio_path is not None and ref_audio_path != self.ref_audio_path:
            self.prepare_ref_audio(ref_audio_path)
        for chunk in self.model.generate(
            text,
            ref_audio=None,  # Already prepared via prepare_conditionals
            temperature=self.temperature,
            top_k=self.top_k,
            top_p=self.top_p,
            repetition_penalty=self.repetition_penalty,
            stream=self.stream,
            streaming_interval=self.streaming_interval,
        ):
            audio_np = np.asarray(chunk.audio, dtype=np.float32)
            audio_np = np.clip(audio_np, -1.0, 1.0)
            audio_int16 = (audio_np * 32767.0).astype(np.int16)
            
            # Chunk the audio to avoid WebSocket message size limits
            for i in range(0, len(audio_int16), self.CHUNK_SAMPLES):
                audio_chunk = audio_int16[i:i + self.CHUNK_SAMPLES]
                yield audio_chunk.tobytes()

    def warmup(self) -> None:
        """Warm up the TTS model."""
        for _ in self.generate("Hello."):
            pass

    @property
    def sample_rate(self) -> int:
        return self.output_sample_rate


class Qwen3TTS:
    CHUNK_SAMPLES = 2880

    def __init__(
        self,
        model_id: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
        ref_audio_path: Optional[str] = None,
        output_sample_rate: int = 24_000,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        repetition_penalty: float = 1.05,
        stream: bool = False,
        streaming_interval: float = 2.0,
    ):
        self.model_id = model_id
        self.ref_audio_path = ref_audio_path
        self.output_sample_rate = output_sample_rate
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.repetition_penalty = repetition_penalty
        self.stream = stream
        self.streaming_interval = streaming_interval

        self.model = None
        self._cached_ref_path: Optional[str] = None
        self._cached_ref_audio = None
        self.ref_text_map = {
            "santa.wav": (
                "Ho ho ho! Your toy is awake, the AI elves are working locally, and Santa's "
                "workshop is officially running on localhost."
            ),
            "narrator1.wav": (
                "When the day feels heavy, remember this: You are not behind. Life unfolds in "
                "its own time, like seasons changing. Be gentle with yourself today."
            ),
            "aussie.wav": (
                "Crikey! Would you look at this beauty right here? Absolutely magnificent. "
                "Now I'm gonna get nice and close, but very gentle, very respectful. She's not "
                "aggressive, just misunderstood."
            ),
        }

    def load(self) -> None:
        last_err = None
        for repo in (self.model_id, "Qwen/Qwen3-TTS-12Hz-0.6B-Base"):
            try:
                self.model = load_tts(repo)
                self.model_id = repo
                break
            except Exception as e:
                last_err = e
        if not self.model:
            raise RuntimeError(f"Failed to load Qwen3-TTS model: {last_err}")

        self.output_sample_rate = int(
            getattr(self.model, "sample_rate", self.output_sample_rate)
        )

    def prepare_ref_audio(self, ref_audio_path: Optional[str]) -> None:
        self.ref_audio_path = ref_audio_path or None
        if self.ref_audio_path != self._cached_ref_path:
            self._cached_ref_path = None
            self._cached_ref_audio = None

    def _load_ref_audio(self, ref_audio_path: str):
        if self._cached_ref_path == ref_audio_path and self._cached_ref_audio is not None:
            return self._cached_ref_audio

        audio, sample_rate = sf.read(ref_audio_path, always_2d=False, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        target_sr = int(getattr(self.model, "sample_rate", self.output_sample_rate))
        if sample_rate != target_sr:
            try:
                import librosa

                audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=target_sr)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to resample ref audio from {sample_rate}Hz to {target_sr}Hz: {e}"
                ) from e

        ref_audio = mx.array(np.asarray(audio, dtype=np.float32))
        self._cached_ref_path = ref_audio_path
        self._cached_ref_audio = ref_audio
        return ref_audio

    def _resolve_ref_text(self, ref_audio_path: Optional[str]) -> Optional[str]:
        if not ref_audio_path:
            return None
        p = Path(ref_audio_path)
        mapped = self.ref_text_map.get(p.name.lower())
        if mapped:
            return mapped

        # Optional sidecar transcript: same filename with .txt extension
        sidecar = p.with_suffix(".txt")
        try:
            if sidecar.exists() and sidecar.is_file():
                text = sidecar.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except Exception:
            pass
        return None

    def generate(
        self,
        text: str,
        ref_audio_path: Optional[str] = None,
        ref_text: Optional[str] = None,
    ) -> Generator[bytes, None, None]:
        if not self.model:
            raise RuntimeError("TTS model not loaded")

        if ref_audio_path is not None:
            self.prepare_ref_audio(ref_audio_path)

        use_ref_audio = self.ref_audio_path
        use_ref_text = ref_text or self._resolve_ref_text(use_ref_audio)
        gen_kwargs = dict(
            temperature=self.temperature,
            top_k=self.top_k,
            top_p=self.top_p,
            repetition_penalty=self.repetition_penalty,
            stream=self.stream,
            streaming_interval=self.streaming_interval,
        )
        if use_ref_audio and use_ref_text:
            gen_kwargs["ref_audio"] = self._load_ref_audio(use_ref_audio)
            gen_kwargs["ref_text"] = use_ref_text

        for result in self.model.generate(text, **gen_kwargs):
            audio = getattr(result, "audio", result)
            audio_np = np.asarray(audio, dtype=np.float32)

            audio_np = np.clip(audio_np, -1.0, 1.0)
            audio_int16 = (audio_np * 32767.0).astype(np.int16)

            for i in range(0, len(audio_int16), self.CHUNK_SAMPLES):
                audio_chunk = audio_int16[i : i + self.CHUNK_SAMPLES]
                yield audio_chunk.tobytes()

    def warmup(self) -> None:
        for _ in self.generate("Hello."):
            pass

    @property
    def sample_rate(self) -> int:
        return self.output_sample_rate
