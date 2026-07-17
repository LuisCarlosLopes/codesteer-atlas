#!/usr/bin/env python3
"""Valida front matter, IDs duplicados e wikilinks quebrados na Base Cognitiva."""

from __future__ import annotations

import glob
import os
import re
import sys

BASE = os.environ.get("CB_BASE", "cognitive-base")
EXCLUDED = {
    "README.md",
    "index.md",
    "CONTRIBUTING.md",
    "glossary.md",
    "mindmap.md",
    "note.md",
}
REQUIRED_FIELDS = ("id", "type", "title", "status", "created", "author")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def is_note(path: str) -> bool:
    name = os.path.basename(path)
    if name in EXCLUDED:
        return False
    if "/meta/templates/" in path.replace("\\", "/"):
        return False
    return path.endswith(".md")


def main() -> int:
    errors: list[str] = []
    notes = [f for f in glob.glob(f"{BASE}/**/*.md", recursive=True) if is_note(f)]

    # Front matter obrigatório
    for path in notes:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        if not content.startswith("---"):
            errors.append(f"SEM FRONT MATTER: {path}")
            continue
        for field in REQUIRED_FIELDS:
            if not re.search(rf"^{field}:", content, re.MULTILINE):
                errors.append(f"FALTA '{field}': {path}")

    # IDs duplicados
    ids: dict[str, list[str]] = {}
    for path in notes:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r"^id:\s*(\S+)", fh.read(), re.MULTILINE)
        if m:
            ids.setdefault(m.group(1), []).append(path)
    for note_id, paths in ids.items():
        if len(paths) > 1:
            errors.append(f"ID DUPLICADO '{note_id}': {', '.join(paths)}")

    # Wikilinks quebrados
    all_names = {
        os.path.splitext(os.path.basename(f))[0]
        for f in glob.glob(f"{BASE}/**/*.md", recursive=True)
    }
    skip_wikilink_paths = {"/meta/templates/", "/.github/"}
    for path in glob.glob(f"{BASE}/**/*.md", recursive=True):
        norm = path.replace("\\", "/")
        if any(marker in norm for marker in skip_wikilink_paths):
            continue
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for m in WIKILINK.finditer(line):
                    target = m.group(1).strip()
                    if target.endswith("/"):
                        continue
                    name = os.path.splitext(os.path.basename(target))[0]
                    if name in {"nome-do-arquivo"}:
                        continue
                    if name not in all_names:
                        errors.append(
                            f"WIKILINK QUEBRADO [[{target}]] em {path}:{lineno}"
                        )
                    # Alias [[x|y]] em linha de tabela Markdown quebra a célula
                    if line.strip().startswith("|") and re.search(
                        r"\[\[[^\]]+\|[^\]]+\]\]", line
                    ):
                        errors.append(
                            f"WIKILINK COM ALIAS EM TABELA (use [[arquivo]] + coluna Título): "
                            f"{path}:{lineno}"
                        )

    if errors:
        print(f"❌ {len(errors)} problema(s) encontrado(s):\n")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"✅ Base Cognitiva OK ({len(notes)} nota(s) verificada(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
