"""
embeddings/providers/__init__.py
================================
Providers concrets pour les embeddings + fabrique (factory).
Types supportes : "hash" (lite, offline), "sentence-transformer" (local full),
"remote" (API OpenAI-compatible).

Imports paresseux (lazy) : importer ce package ne tire JAMAIS torch.
Le module lourd sentence_transformer n'est charge qu'a l'usage reel.
"""

__all__ = [
    "SentenceTransformerProvider",
    "HashEmbeddingProvider",
    "RemoteEmbeddingProvider",
    "create_embedding_provider",
]


def __getattr__(name: str):
    if name == "SentenceTransformerProvider":
        from embeddings.providers.sentence_transformer import SentenceTransformerProvider
        return SentenceTransformerProvider
    if name == "HashEmbeddingProvider":
        from embeddings.providers.hash_provider import HashEmbeddingProvider
        return HashEmbeddingProvider
    if name == "RemoteEmbeddingProvider":
        from embeddings.providers.remote_provider import RemoteEmbeddingProvider
        return RemoteEmbeddingProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def create_embedding_provider(model_def: dict, emit_func=None):
    """Cree le bon provider depuis un dict de config (payload runtime.configure).

    Exemples :
      {"type": "hash"}
      {"type": "sentence-transformer", "id": "sentence-transformers/all-MiniLM-L6-v2"}
      {"type": "remote", "id": "text-embedding-3-small", "api_key": "...", "base_url": "..."}
    Sans "type" : devine depuis l'id (retro-compatibilite).
    """
    if not isinstance(model_def, dict):
        raise ValueError("model_def doit etre un dict")
    ptype = (model_def.get("type") or "").lower().strip()
    if not ptype:
        mid = str(model_def.get("id") or "")
        if mid.startswith("remote:") or "embedding-3" in mid or "voyage" in mid:
            ptype = "remote"
        elif mid in ("lite-hash", "hash", "lite"):
            ptype = "hash"
        else:
            ptype = "sentence-transformer"
    if ptype in ("hash", "lite", "lite-hash"):
        from embeddings.providers.hash_provider import HashEmbeddingProvider
        return HashEmbeddingProvider(dim=int(model_def.get("dim", 256)))
    if ptype == "remote":
        from embeddings.providers.remote_provider import RemoteEmbeddingProvider
        return RemoteEmbeddingProvider(
            model_id=model_def.get("id", "text-embedding-3-small").replace("remote:", ""),
            api_key=model_def.get("api_key", ""),
            base_url=model_def.get("base_url", "https://api.openai.com/v1"),
            display_name=model_def.get("display_name"),
        )
    # defaut : local full (charge torch uniquement ici, a l'usage reel)
    from embeddings.providers.sentence_transformer import SentenceTransformerProvider
    return SentenceTransformerProvider(
        model_id=model_def.get("id", "sentence-transformers/all-MiniLM-L6-v2"),
        display_name=model_def.get("display_name", model_def.get("id", "")),
        prefix_query=model_def.get("prefix_query", ""),
        prefix_passage=model_def.get("prefix_passage", ""),
        emit_func=emit_func,
    )