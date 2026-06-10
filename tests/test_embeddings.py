from codesteer_atlas.embeddings import EmbeddingEngine


def test_embedding_engine_singleton():
    """
    Testa se o EmbeddingEngine implementa corretamente o padrão Singleton,
    reutilizando a mesma instância em todas as chamadas.
    """
    engine1 = EmbeddingEngine()
    engine2 = EmbeddingEngine()

    assert engine1 is engine2


def test_lazy_loading_and_encoding():
    """
    Testa se o carregamento do modelo de embedding é preguiçoso (lazy loading)
    e se a codificação individual e em lote gera vetores com a dimensão correta (384).
    """
    engine = EmbeddingEngine()

    # 1. Antes de chamar encode, o modelo deve ser None (não carregado)
    # Resetamos o estado interno para o teste ficar isolado e independente da ordem de execução
    engine._model = None
    assert engine._model is None

    # 2. Chama encode de um único texto
    text = "def test_function(): pass"
    vector = engine.encode_single(text)

    # 3. O modelo agora deve estar inicializado
    assert engine._model is not None

    # 4. O vetor gerado deve ter exatamente 384 dimensões (tamanho do all-MiniLM-L6-v2)
    assert isinstance(vector, list)
    assert len(vector) == 384
    assert all(isinstance(val, float) for val in vector)


def test_batch_encoding():
    """
    Testa se a geração de embeddings em lote funciona gerando múltiplos vetores de dimensão 384.
    """
    engine = EmbeddingEngine()
    texts = ["hello world", "class User: pass", "def run(): print(1)"]

    vectors = engine.encode(texts, batch_size=2)

    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 384
        assert isinstance(vec, list)
