"""
Reordenação pós-RRF por cross-encoder ONNX — singleton de carga preguiçosa.

Espelha `embeddings.py`: o modelo só entra em memória na primeira chamada de
`rerank`, o import do fastembed fica dentro da carga, e a exceção de carga
propaga para o chamador decidir o fallback (Princípio VI).
"""

import os
import threading
from typing import TYPE_CHECKING, List, Optional

from codesteer_atlas.config import (
    CROSS_ENCODER_DEFAULT_MODEL,
    CROSS_ENCODER_MAX_DOC_CHARS,
    RERANK_MODEL_ENV_FLAG,
)

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder


class CrossEncoderReranker:
    # @MindContext: Cross-encoder ONNX opt-in que reordena o pool pós-RRF
    # @MindDecision: singleton + lock iguais a EmbeddingEngine para não atrasar o startup
    # @MindRisk: import no topo quebraria o servidor se o piso de fastembed não tiver TextCrossEncoder
    """Reordenador por atenção conjunta query×documento, desligado até a primeira chamada."""

    _instance: Optional["CrossEncoderReranker"] = None
    _model: Optional["TextCrossEncoder"] = None
    _load_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(CrossEncoderReranker, cls).__new__(cls)
        return cls._instance

    def _load_model(self) -> "TextCrossEncoder":
        """Carrega o cross-encoder sob lock; a exceção sobe para o chamador."""
        if self._model is not None:
            return self._model

        with self._load_lock:
            if self._model is not None:
                return self._model

            import onnxruntime as ort
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            # Mesmo silenciamento de stdout do EmbeddingEngine (Princípio IV).
            ort.set_default_logger_severity(3)

            model_name = os.environ.get(RERANK_MODEL_ENV_FLAG) or CROSS_ENCODER_DEFAULT_MODEL
            self._model = TextCrossEncoder(model_name=model_name, threads=1)
            return self._model

    def rerank(self, query: str, results: List) -> List:
        """
        Reordena o pool pela pontuação do modelo, da maior para a menor.

        Pool vazio ou de um item devolve a entrada inalterada, sem carregar o modelo.
        `content=None` vira string vazia — o modelo não recebe None.
        """
        if len(results) <= 1:
            return results

        model = self._load_model()
        documents = []
        for result in results:
            content = getattr(result, "content", None) or ""
            if len(content) > CROSS_ENCODER_MAX_DOC_CHARS:
                content = content[:CROSS_ENCODER_MAX_DOC_CHARS]
            documents.append(content)

        scores = list(model.rerank(query, documents))
        ranked = sorted(
            zip(scores, range(len(results)), results, strict=True),
            key=lambda row: (-row[0], row[1]),
        )
        return [row[2] for row in ranked]
