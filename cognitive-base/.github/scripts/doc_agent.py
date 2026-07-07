#!/usr/bin/env python3
"""Gera rascunho de nota da Base Cognitiva a partir de metadados de um PR."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date

BASE = os.environ.get("CB_BASE", "cognitive-base")
AUTHOR = os.environ.get("KB_AUTHOR", "@luiscarloslopes")
TODAY = date.today().isoformat()

MAPPING = [
    (lambda t, labels, files: "ai-correction" in labels,
     "decisions/ai-corrections", "ai-correction", "dec"),
    (lambda t, labels, files: t.startswith("feat:"),
     "specs", "feature", "spc"),
    (lambda t, labels, files: t.startswith("fix:") and any(
        "traceback" in open(f, encoding="utf-8", errors="ignore").read().lower()
        for f in files if os.path.isfile(f)
    ), "ops", "incident", "ops"),
    (lambda t, labels, files: t.startswith("refactor:") and "breaking" in t.lower(),
     "decisions", "adr", "dec"),
    (lambda t, labels, files: t.startswith("chore(migration):") or any(
        f.endswith(".sql") for f in files
    ), "system", "table", "sys"),
    (lambda t, labels, files: "guide" in labels or t.startswith("docs(guide):"),
     "guides", "how-to", "gd"),
]


def next_id(prefix: str) -> str:
    pattern = re.compile(rf"^id:\s*{prefix}-(\d+)", re.MULTILINE)
    max_num = 0
    for root, _, files in os.walk(BASE):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                for m in pattern.finditer(fh.read()):
                    max_num = max(max_num, int(m.group(1)))
    return f"{prefix}-{max_num + 1:03d}"


def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "rascunho"


def classify(title: str, labels: list[str], files: list[str]) -> tuple[str, str, str] | None:
    for pred, quadrant, note_type, prefix in MAPPING:
        try:
            if pred(title, labels, files):
                return quadrant, note_type, prefix
        except OSError:
            continue
    return None


def build_draft(
    title: str,
    body: str,
    quadrant: str,
    note_type: str,
    prefix: str,
) -> tuple[str, str]:
    note_id = next_id(prefix)
    clean_title = re.sub(
        r"^(feat|fix|refactor|chore|docs)(\([^)]+\))?:\s*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip() or title
    filename = f"{note_id}-{slugify(clean_title)}.md"
    escaped_title = title.replace('"', '\\"')
    content = f"""---
id: {note_id}
type: {note_type}
title: "{clean_title}"
status: draft
created: "{TODAY}"
updated: "{TODAY}"
author: "{AUTHOR}"
links: []
tags: []
source: greenfield
migration_status: ""
meta:
  generated_by: doc-agent
  pr_title: "{escaped_title}"
---

# {clean_title}

## Contexto

> Gerado automaticamente a partir do PR. Revise e complete antes de promover a `approved`.

{body.strip() or "_Sem descrição no PR — preencher manualmente._"}

## Conteúdo Principal

> Preencher conforme o type `{note_type}`.

## Notas Relacionadas

## Histórico

| Versão | Data   | Autor   | Descrição              |
| ------ | ------ | ------- | ---------------------- |
| 1.0.0  | {TODAY} | {AUTHOR} | Rascunho gerado por doc-agent |
"""
    return os.path.join(BASE, quadrant, filename), content


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        print("GITHUB_EVENT_PATH não definido", file=sys.stderr)
        return 1

    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)

    pr = event.get("pull_request", event)
    title = pr.get("title", "")
    body = pr.get("body") or ""
    labels = [lbl["name"] for lbl in pr.get("labels", [])]
    files = [
        f["filename"]
        for f in pr.get("files", [])
        if isinstance(f, dict) and "filename" in f
    ]

    result = classify(title, labels, files)
    if not result:
        print("Nenhum padrão de PR mapeado para nota — doc-agent ignorado.")
        return 0

    quadrant, note_type, prefix = result
    path, content = build_draft(title, body, quadrant, note_type, prefix)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        print(f"Nota já existe: {path}")
        return 0

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)

    print(f"Rascunho criado: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
