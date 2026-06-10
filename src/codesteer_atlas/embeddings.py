from typing import List

# Nome do modelo no formato esperado pelo fastembed (ONNX) - DECISAO-001
# Mesmo modelo (all-MiniLM-L6-v2, 384 dims) usado anteriormente via sentence-transformers
FASTEMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingEngine:
    """
    Motor de geração de embeddings vetoriais locais utilizando o modelo all-MiniLM-L6-v2
    via fastembed (ONNX Runtime, CPU). Implementa o padrão Singleton e Lazy Loading
    para garantir carregamento tardio (apenas na primeira chamada de encode) e
    inicialização rápida do servidor MCP.
    """

    _instance = None
    _model = None

    def __new__(cls, *args, **kwargs):
        # Padrão Singleton: garante apenas uma instância em memória no processo
        if cls._instance is None:
            cls._instance = super(EmbeddingEngine, cls).__new__(cls)
        return cls._instance

    def _load_model(self):
        """Lazy loading: carrega o modelo de embeddings apenas quando necessário."""
        if self._model is None:
            # Importação local tardia para evitar atraso síncrono no startup do servidor
            from fastembed import TextEmbedding

            # Carrega o modelo de embeddings (all-MiniLM-L6-v2) via ONNX Runtime em CPU local
            # O fastembed carrega do cache local (~/.cache/fastembed) se já estiver baixado
            self._model = TextEmbedding(model_name=FASTEMBED_MODEL_NAME)

    def encode(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Gera embeddings vetoriais para uma lista de textos em lote (batch).
        Ideal para uso durante a indexação inicial.
        """
        if not texts:
            return []

        self._load_model()

        # fastembed.embed retorna um generator de numpy.ndarray (float32)
        embeddings = self._model.embed(texts, batch_size=batch_size)
        return [vector.tolist() for vector in embeddings]

    def encode_single(self, text: str) -> List[float]:
        """
        Gera o embedding vetorial para um único texto (ex: query de busca).
        """
        self._load_model()

        embeddings = list(self._model.embed([text]))
        return embeddings[0].tolist()
