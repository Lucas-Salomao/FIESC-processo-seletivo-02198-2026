"""Cliente Vertex AI (Gemini) — geração e embeddings.

Interface fina e substituível: os testes injetam um FakeLLMClient com a mesma
assinatura, então nenhum teste de CI chama a API real.
"""

from functools import lru_cache
from typing import Protocol

from src.core.config import get_settings


class LLMClient(Protocol):
    def generate(self, prompt: str, system: str | None = None) -> str: ...
    def embed(self, texts: list[str], task_type: str) -> list[list[float]]: ...
    def transcribe(self, image_png: bytes) -> str: ...


class GeminiClient:
    """Implementação real sobre google-genai apontando para o Vertex AI."""

    def __init__(self) -> None:
        from google import genai

        from src.core.config import ensure_google_env

        ensure_google_env()
        s = get_settings()
        self._settings = s
        self._client = genai.Client(
            vertexai=True,
            project=s.google_cloud_project,
            location=s.google_cloud_location,
        )

    def generate(self, prompt: str, system: str | None = None) -> str:
        from google.genai import types

        s = self._settings
        response = self._client.models.generate_content(
            model=s.gemini_chat_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                system_instruction=system,
            ),
        )
        return response.text or ""

    def transcribe(self, image_png: bytes) -> str:
        """OCR de página de documento via Gemini multimodal (PDFs escaneados)."""
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._settings.gemini_chat_model,
            contents=[
                types.Part.from_bytes(data=image_png, mime_type="image/png"),
                "Transcreva TODO o texto desta página de documento técnico, fielmente, "
                "em Markdown simples. Preserve títulos numerados (ex.: '4.1 Título') em "
                "linhas próprias e listas como itens. Não adicione comentários.",
            ],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return response.text or ""

    def embed(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        from google.genai import types

        s = self._settings
        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=s.embedding_dim,
        )
        # O Vertex atende 1 instância por request p/ gemini-embedding-* —
        # enviar lista retorna silenciosamente apenas 1 embedding.
        out: list[list[float]] = []
        for text in texts:
            result = self._client.models.embed_content(
                model=s.gemini_embedding_model, contents=text, config=config
            )
            out.extend(e.values for e in result.embeddings)
        if len(out) != len(texts):
            raise RuntimeError(f"Embeddings retornados ({len(out)}) != textos ({len(texts)}).")
        return out


@lru_cache
def get_llm_client() -> LLMClient:
    return GeminiClient()
