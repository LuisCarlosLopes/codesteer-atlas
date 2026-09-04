"""Geração semântica, cache por símbolo e sumários hierárquicos."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from codesteer_atlas.config import (
    CURRENT_INDEX_VERSION,
    SEMANTIC_ENV_FLAG,
    SEMANTIC_FILENAME,
    SEMANTIC_MAX_SUMMARY_CHARS,
    SEMANTIC_MAX_TEXT_CHARS,
)
from codesteer_atlas.models import CodeChunk
from codesteer_atlas.origin import OriginResolver

ELIGIBLE_SCOPE_TYPES = frozenset({"class", "function", "method", "module"})


def semantic_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    values = environ if environ is not None else os.environ
    return values.get(SEMANTIC_ENV_FLAG) == "1"


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def cache_key(chunk: CodeChunk) -> tuple[str, str, str]:
    return (chunk.file_path, chunk.scope_name, content_hash(chunk.content))


def normalize_purpose(raw: Any) -> str:
    if isinstance(raw, Mapping):
        parts = []
        for key in ("what", "purpose", "invariants", "side_effects", "effects"):
            value = raw.get(key)
            if isinstance(value, list):
                value = "; ".join(item for item in value if isinstance(item, str))
            if isinstance(value, str) and value:
                parts.append(value)
        raw = " — ".join(parts)
    elif raw is None:
        raw = ""
    elif not isinstance(raw, str):
        return ""
    text = " ".join(raw.replace("\x00", " ").split())
    if text.startswith("{"):
        try:
            return normalize_purpose(json.loads(text))
        except json.JSONDecodeError:
            pass
    return text[:SEMANTIC_MAX_TEXT_CHARS].strip()


@dataclass
class SemanticGeneration:
    status: str = "disabled"
    generated: int = 0
    reused: int = 0
    attempted: int = 0
    failed: int = 0
    origin: Optional[str] = None
    egress: Optional[str] = None
    origins: list[str] = field(default_factory=list)
    egresses: list[str] = field(default_factory=list)


class ProseGenerator:
    """Gera propósito somente para escopos estruturais elegíveis."""

    def __init__(self, resolver: OriginResolver, embedding_engine: Any = None) -> None:
        self.resolver = resolver
        self.embedding_engine = embedding_engine

    def generate_purposes(
        self,
        chunks: list[CodeChunk],
        cache: Optional[Mapping[tuple[str, str, str], Mapping[str, Any]]] = None,
    ) -> SemanticGeneration:
        result = SemanticGeneration()
        cache = cache or {}
        misses: list[CodeChunk] = []
        for chunk in chunks:
            if chunk.scope_type not in ELIGIBLE_SCOPE_TYPES:
                continue
            key = cache_key(chunk)
            old = cache.get(key)
            old_purpose = normalize_purpose(old.get("purpose")) if old else ""
            if old and old_purpose and old.get("purpose_hash") == key[2]:
                chunk.purpose = old_purpose
                chunk.purpose_hash = key[2]
                chunk.purpose_vector = old.get("purpose_vector")
                result.reused += 1
                continue
            misses.append(chunk)

        result.attempted = len(misses)
        for chunk in misses:
            prompt = (
                "Descreva este símbolo em linguagem natural, cobrindo o que faz, "
                "invariantes e efeitos colaterais. Responda somente com o propósito.\n\n"
                f"Símbolo: {chunk.scope_name}\nTipo: {chunk.scope_type}\n"
                f"Linguagem: {chunk.language}\nCódigo:\n{chunk.content}"
            )
            generated = self.resolver.generate(
                {
                    "prompt": prompt,
                    "content": chunk.content,
                    "scope_name": chunk.scope_name,
                    "scope_type": chunk.scope_type,
                    "language": chunk.language,
                }
            )
            if generated is None:
                result.failed += 1
                continue
            purpose = normalize_purpose(generated.text)
            result.origin = generated.origin
            result.egress = generated.egress
            if generated.origin not in result.origins:
                result.origins.append(generated.origin)
            if generated.egress not in result.egresses:
                result.egresses.append(generated.egress)
            if not purpose:
                result.failed += 1
                continue
            chunk.purpose = purpose
            chunk.purpose_hash = content_hash(chunk.content)
            result.generated += 1

        generated_chunks = [chunk for chunk in misses if chunk.purpose]
        if generated_chunks:
            try:
                engine = self.embedding_engine
                if engine is None:
                    from codesteer_atlas.embeddings import EmbeddingEngine

                    engine = EmbeddingEngine()
                vectors = engine.encode([chunk.purpose for chunk in generated_chunks])
                for chunk, vector in zip(generated_chunks, vectors, strict=True):
                    chunk.purpose_vector = vector
            except Exception as error:
                print(
                    f"[atlas] Vetores de propósito indisponíveis ({type(error).__name__}).",
                    file=sys.stderr,
                )

        if result.attempted == 0:
            result.status = "ok" if result.reused else "no_origin"
        elif result.generated and result.failed:
            result.status = "partial"
        elif result.generated:
            result.status = "ok"
        else:
            result.status = "failed" if self.resolver.resolve() else "no_origin"
        return result


def _layer_key(file_path: str) -> str:
    parts = file_path.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] in {"src", "app", "apps", "packages", "lib", "libs", "pkg", "cmd", "internal", "modules", "services", "components"} and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


@dataclass
class SummaryBundle:
    file_summaries: dict[str, dict[str, str]] = field(default_factory=dict)
    layer_summaries: dict[str, dict[str, str]] = field(default_factory=dict)
    file_generated: int = 0
    file_reused: int = 0
    layer_generated: int = 0
    layer_reused: int = 0


class HierarchySummarizer:
    """Sobe propósitos utilizáveis para arquivo e camada com cache derivado."""

    def __init__(self, resolver: OriginResolver) -> None:
        self.resolver = resolver

    def _summary(
        self,
        label: str,
        purposes: list[tuple[str, str]],
        old: Mapping[str, Any],
        bundle: SummaryBundle,
        kind: str,
    ) -> Optional[dict[str, str]]:
        if not purposes:
            return None
        key = content_hash("|".join(f"{identity}:{value}" for identity, value in purposes))
        previous = old.get(label) if isinstance(old, Mapping) else None
        if isinstance(previous, Mapping) and previous.get("cache_key") == key and previous.get("summary"):
            if kind == "arquivo":
                bundle.file_reused += 1
            else:
                bundle.layer_reused += 1
            return {"summary": str(previous["summary"]), "cache_key": key}
        prompt = (
            f"Resuma em linguagem natural o {kind} '{label}' usando os propósitos "
            "abaixo, preservando responsabilidade e invariantes:\n" +
            "\n".join(f"- {identity}: {value}" for identity, value in purposes)
        )
        generated = self.resolver.generate({"prompt": prompt, "content": prompt, "scope_name": label, "scope_type": kind, "language": "text"})
        summary = normalize_purpose(generated.text) if generated else ""
        if not summary:
            return None
        summary = summary[:SEMANTIC_MAX_SUMMARY_CHARS].strip()
        if kind == "arquivo":
            bundle.file_generated += 1
        else:
            bundle.layer_generated += 1
        return {"summary": summary, "cache_key": key}

    def summarize(self, rows: Iterable[Mapping[str, Any]], previous: Optional[Mapping[str, Any]] = None) -> SummaryBundle:
        previous = previous or {}
        bundle = SummaryBundle()
        by_file: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            purpose = normalize_purpose(row.get("purpose"))
            if purpose:
                identity = f"{row.get('scope_name', '')}:{row.get('purpose_hash', '')}"
                by_file.setdefault(str(row.get("file_path", "")), []).append((identity, purpose))
        old_files = previous.get("file_summaries", {})
        for file_path, purposes in by_file.items():
            summary = self._summary(file_path, purposes, old_files, bundle, "arquivo")
            if summary:
                bundle.file_summaries[file_path] = summary

        by_layer: dict[str, list[tuple[str, str]]] = {}
        for file_path, entry in bundle.file_summaries.items():
            by_layer.setdefault(_layer_key(file_path), []).append((file_path, entry["summary"]))
        old_layers = previous.get("layer_summaries", {})
        for layer_path, summaries in by_layer.items():
            summary = self._summary(layer_path, summaries, old_layers, bundle, "camada")
            if summary:
                bundle.layer_summaries[layer_path] = summary
        return bundle


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temp_path, path)


def load_semantic_sidecar(index_dir: Path) -> Optional[dict[str, Any]]:
    path = Path(index_dir) / SEMANTIC_FILENAME
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_semantic_sidecar(index_dir: Path, payload: Mapping[str, Any]) -> Path:
    path = Path(index_dir) / SEMANTIC_FILENAME
    _write_json_atomic(path, payload)
    return path


def semantic_index_state(index_dir: Path, manifest: Any = None) -> tuple[str, Optional[str]]:
    if manifest is None:
        return "absent", "index_missing"
    version = tuple(int(part) if part.isdigit() else 0 for part in str(manifest.index_version).split("."))
    if version < (2, 3, 0):
        return "legacy", "index_version_below_2_3_0"
    sidecar = load_semantic_sidecar(index_dir)
    if sidecar is None:
        return "absent", "sidecar_unreadable"
    if int(sidecar.get("usable_purpose_count", 0)) < 1:
        return "absent", "no_prose"
    return "ready", None


def build_sidecar(
    index_dir: Path,
    rows: Iterable[Mapping[str, Any]],
    generation: SemanticGeneration,
    resolver: OriginResolver,
    previous: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    rows = list(rows)
    previous = previous or {}
    if generation.status == "disabled":
        summaries = SummaryBundle(
            file_summaries=dict(previous.get("file_summaries") or {}),
            layer_summaries=dict(previous.get("layer_summaries") or {}),
        )
    else:
        summaries = HierarchySummarizer(resolver).summarize(rows, previous)
    usable = sum(1 for row in rows if normalize_purpose(row.get("purpose")))
    resolver_origins = getattr(resolver, "used_origins", [])
    resolver_egresses = getattr(resolver, "used_egresses", [])
    origins = list(resolver_origins) if isinstance(resolver_origins, list) else []
    egresses = list(resolver_egresses) if isinstance(resolver_egresses, list) else []
    for origin in generation.origins:
        if origin not in origins:
            origins.append(origin)
    for egress in generation.egresses:
        if egress not in egresses:
            egresses.append(egress)
    description = resolver.describe()
    if isinstance(description, tuple) and len(description) == 2:
        configured_origin, configured_egress = description
    else:
        configured_origin = configured_egress = None
    current_origin = (
        origins[0]
        if len(origins) == 1
        else "mixed"
        if origins
        else configured_origin or generation.origin
    )
    current_egress = (
        egresses[0]
        if len(egresses) == 1
        else "mixed"
        if egresses
        else configured_egress or generation.egress
    )
    payload = {
        "schema_version": "1.0",
        "index_version": CURRENT_INDEX_VERSION,
        "usable_purpose_count": usable,
        "file_summaries": summaries.file_summaries,
        "layer_summaries": summaries.layer_summaries,
        "last_generation": {
            "status": generation.status,
            "origin": current_origin if origins else generation.origin,
            "egress": current_egress if egresses else generation.egress,
            "origins": origins,
            "egresses": egresses,
            "semantic_generated": generation.generated,
            "semantic_reused": generation.reused,
            "semantic_file_generated": summaries.file_generated,
            "semantic_file_reused": summaries.file_reused,
            "semantic_layer_generated": summaries.layer_generated,
            "semantic_layer_reused": summaries.layer_reused,
        },
        "origin": current_origin,
        "egress": current_egress,
        "origins": origins,
        "egresses": egresses,
    }
    write_semantic_sidecar(index_dir, payload)
    return payload
