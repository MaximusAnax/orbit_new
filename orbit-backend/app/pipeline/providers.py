from abc import ABC, abstractmethod

from openai import AsyncOpenAI

from app.core.config import settings


class LLMProvider(ABC):
    @abstractmethod
    async def structured_completion(
        self, system: str, user: str, response_model: type
    ) -> tuple[object, dict, dict]:
        """Returns (parsed_model, raw_input, raw_output)."""


class OpenAILLMProvider(LLMProvider):
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def structured_completion(
        self, system: str, user: str, response_model: type
    ) -> tuple[object, dict, dict]:
        raw_input = {"system": system, "user": user, "model": settings.llm_model}
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        completion = await self.client.beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=response_model,
        )
        parsed = completion.choices[0].message.parsed
        raw_output = completion.model_dump()
        return parsed, raw_input, raw_output


class TranscriptionProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.m4a") -> str:
        pass


class OpenAITranscriptionProvider(TranscriptionProvider):
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.m4a") -> str:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        import io

        buffer = io.BytesIO(audio_bytes)
        buffer.name = filename
        result = await self.client.audio.transcriptions.create(
            model="whisper-1",
            file=buffer,
        )
        return result.text


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        pass


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def embed(self, text: str) -> list[float]:
        if not settings.openai_api_key:
            return [0.0] * settings.embedding_dimensions
        result = await self.client.embeddings.create(
            model=settings.embedding_model,
            input=text,
            dimensions=settings.embedding_dimensions,
        )
        return result.data[0].embedding


_llm: LLMProvider | None = None
_transcription: TranscriptionProvider | None = None
_embedding: EmbeddingProvider | None = None


def get_llm() -> LLMProvider:
    global _llm
    if _llm is None:
        _llm = OpenAILLMProvider()
    return _llm


def get_transcription() -> TranscriptionProvider:
    global _transcription
    if _transcription is None:
        _transcription = OpenAITranscriptionProvider()
    return _transcription


def get_embedding() -> EmbeddingProvider:
    global _embedding
    if _embedding is None:
        _embedding = OpenAIEmbeddingProvider()
    return _embedding


def set_llm(provider: LLMProvider) -> None:
    global _llm
    _llm = provider


def set_transcription(provider: TranscriptionProvider) -> None:
    global _transcription
    _transcription = provider


def set_embedding(provider: EmbeddingProvider) -> None:
    global _embedding
    _embedding = provider
