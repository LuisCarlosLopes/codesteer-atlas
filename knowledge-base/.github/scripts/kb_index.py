#!/usr/bin/env python3
"""Regenera index.md e atualiza estatísticas no README.md da Base Cognitiva."""

from __future__ import annotations

import glob
import os
import re
from collections import defaultdict
from datetime import date

BASE = os.environ.get("KB_BASE", "knowledge-base")
EXCLUDED = {"README.md", "index.md", "CONTRIBUTING.md", "glossary.md", "mindmap.md", "note.md"}
QUADRANTES = ("decisions", "specs", "system", "guides", "ops")
HOJE = date.today().isoformat()

# Tabela "Encontre o que você precisa" — ordem fixa (kb-init / kb-index)
FIND_TABLE_ROWS = [
    ("decisions", "Por que algo é assim?", "[[decisions/README]]", "ADRs, regras de negócio"),
    ("specs", "O que o sistema faz?", "[[specs/README]]", "Features, APIs, casos de uso"),
    ("system", "Como o sistema existe hoje?", "[[system/README]]", "Serviços, tabelas, infraestrutura"),
    ("guides", "Como trabalhar dentro da arquitetura?", "[[guides/README]]", "How-tos, fluxos, componentes"),
    ("ops", "Como operar em produção?", "[[ops/README]]", "Runbooks, incidentes, lições"),
]


def _fm_field(fm: str, field: str) -> str:
    m = re.search(rf"^{field}:\s*\"?(.+?)\"?\s*$", fm, re.M)
    return m.group(1).strip('"') if m else ""


def collect_notes() -> list[dict]:
    notas = []
    base_name = os.path.basename(BASE)
    for path in sorted(glob.glob(f"{BASE}/**/*.md", recursive=True)):
        nome = os.path.basename(path)
        if nome in EXCLUDED or "/meta/templates/" in path.replace("\\", "/"):
            continue
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        m = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not m:
            continue
        fm = m.group(1)
        parts = path.replace("\\", "/").split("/")
        quadrante = parts[1] if len(parts) > 2 and parts[0] == base_name else ""
        if quadrante not in QUADRANTES:
            continue
        meta_block = ""
        meta_m = re.search(r"^meta:\s*\n((?:  .+\n)*)", fm, re.M)
        if meta_m:
            meta_block = meta_m.group(1)
        pattern_violated = ""
        pv = re.search(r"pattern_violated:\s*\"?([^\"\n]+)", meta_block)
        if pv:
            pattern_violated = pv.group(1).strip()
        notas.append(
            {
                "path": path,
                "name": os.path.splitext(nome)[0],
                "id": _fm_field(fm, "id"),
                "type": _fm_field(fm, "type"),
                "title": _fm_field(fm, "title"),
                "status": _fm_field(fm, "status"),
                "quadrante": quadrante,
                "source": _fm_field(fm, "source") or "greenfield",
                "migration_status": _fm_field(fm, "migration_status"),
                "legacy_system": _fm_field(fm, "legacy_system"),
                "pattern_violated": pattern_violated,
                "wikilinks": re.findall(r"\[\[([^\]|#]+)", content),
            }
        )
    return notas


def _link(name: str) -> str:
    """Wikilink seguro para células de tabela Markdown (sem alias — o | quebra a tabela)."""
    return f"[[{name}]]"


def _wikilink_prose(name: str, title: str) -> str:
    """Wikilink com alias — apenas fora de tabelas (listas, parágrafos)."""
    return f"[[{name}|{title}]]"


def _compute_connections(notas: list[dict]) -> dict[str, int]:
    nome_map = {n["name"]: n for n in notas}
    conexoes: dict[str, int] = defaultdict(int)
    for nota in notas:
        for wl in nota["wikilinks"]:
            alvo = os.path.splitext(os.path.basename(wl.strip()))[0]
            if alvo in nome_map:
                conexoes[alvo] += 1
    return dict(conexoes)


def build_index(notas: list[dict], conexoes: dict[str, int]) -> str:
    nome_map = {n["name"]: n for n in notas}
    lines = [
        "# Índice da Base Cognitiva",
        f"> Gerado automaticamente em {HOJE} · {len(notas)} notas",
        "",
        "## Grafo por quadrante",
        "",
    ]

    for q in QUADRANTES:
        qnotas = [n for n in notas if n["quadrante"] == q]
        lines.append(f"### {q}/ — {len(qnotas)} notas")
        lines.append("")
        if qnotas:
            lines.append("| ID | Título | Type | Status | Conexões |")
            lines.append("|---|---|---|---|---|")
            for n in sorted(qnotas, key=lambda x: x["id"]):
                conn = conexoes.get(n["name"], 0)
                lines.append(
                    f"| {_link(n['name'])} | {n['title']} | {n['type']} | "
                    f"{n['status']} | {conn} |"
                )
        else:
            lines.append("_Nenhuma nota ainda._")
        lines.append("")

    top = sorted(conexoes.items(), key=lambda x: x[1], reverse=True)[:10]
    lines.extend(
        [
            "## Nós mais conectados",
            "",
            "> Os nós com mais wikilinks apontando para eles são os pilares do domínio.",
            "> Comece por eles para entender a arquitetura.",
            "",
        ]
    )
    if top:
        lines.append("| ID | Título | Conexões recebidas |")
        lines.append("|---|---|---|")
        for name, count in top:
            n = nome_map[name]
            lines.append(f"| {_link(name)} | {n['title']} | {count} |")
    else:
        lines.append("_Grafo ainda sem conexões suficientes._")
    lines.append("")

    ai = [n for n in notas if n["type"] == "ai-correction"]
    lines.extend(
        [
            "## Padrões mais violados pela IA",
            "",
            "> Extraído de decisions/ai-corrections/ — alimenta o system prompt do agente.",
            "",
        ]
    )
    if ai:
        lines.append("| ID | Título | Padrão violado |")
        lines.append("|---|---|---|")
        for n in ai:
            lines.append(
                f"| {_link(n['name'])} | {n['title']} | {n['pattern_violated'] or '—'} |"
            )
    else:
        lines.append("_Nenhuma correção de IA registrada._")
    lines.append("")

    legado = [n for n in notas if n["source"] == "legacy"]
    if legado:
        por_status = defaultdict(int)
        for n in legado:
            por_status[n["migration_status"] or "pending"] += 1
        total = len(legado)
        lines.extend(
            [
                "## Status de Migração do Legado",
                "",
                "> Notas com `source: legacy`, criadas via `kb-legacy`.",
                "",
                "| Status | Notas | % |",
                "|---|---|---|",
            ]
        )
        for status in ("pending", "validated", "adapted", "discarded"):
            n = por_status.get(status, 0)
            pct = round(100 * n / total) if total else 0
            lines.append(f"| {status} | {n} | {pct}% |")
        sistemas = sorted({n["legacy_system"] for n in legado if n["legacy_system"]})
        lines.append("")
        lines.append(f"Sistemas legados mapeados: {', '.join(sistemas) or 'nenhum'}")
        discarded = [n for n in legado if n["migration_status"] == "discarded"]
        if discarded:
            lines.extend(["", "### Descartadas (discarded) — não reimplementar", ""])
            lines.append("| ID | Título |")
            lines.append("|---|---|")
            for n in discarded:
                lines.append(f"| {_link(n['name'])} | {n['title']} |")

    return "\n".join(lines) + "\n"


def _replace_section(content: str, heading: str, new_body: str, *, until_heading: str | None = None) -> str:
    """Substitui o corpo de uma seção ## heading até a próxima ## ou EOF."""
    if until_heading:
        pattern = rf"(## {re.escape(heading)}\n\n)(.*?)(\n\n## {re.escape(until_heading)})"
    else:
        pattern = rf"(## {re.escape(heading)}\n\n)(.*?)(\n\n## |\Z)"
    return re.sub(pattern, rf"\1{new_body}\n\3", content, count=1, flags=re.DOTALL)


def update_readme(notas: list[dict], conexoes: dict[str, int], nome_map: dict) -> None:
    readme_path = f"{BASE}/README.md"
    with open(readme_path, encoding="utf-8") as fh:
        content = fh.read()

    por_q = defaultdict(int)
    for n in notas:
        por_q[n["quadrante"]] += 1

    # Header único — remove linhas duplicadas de contagem
    content = re.sub(
        r"(# Base Cognitiva — CodeSteer Atlas\n\n)"
        r"(?:> \d+ notas · última atualização: \d{4}-\d{2}-\d{2}\n\n)*",
        rf"\1> {len(notas)} notas · última atualização: {HOJE}\n\n",
        content,
        count=1,
    )

    # Tabela "Encontre o que você precisa" — reescrita completa (evita corromper wikilinks)
    find_rows = ["| Pergunta | Quadrante | Notas | Comece por |", "| -------- | --------- | ----- | ---------- |"]
    for q, pergunta, quadrante_link, comecar in FIND_TABLE_ROWS:
        find_rows.append(f"| {pergunta} | {quadrante_link} | {por_q[q]} | {comecar} |")
    find_rows.append(
        "| O que significa este termo? | [[meta/glossary]] | — | Termos com âncoras |"
    )
    find_table = "\n".join(find_rows)
    content = _replace_section(content, "Encontre o que você precisa", find_table, until_heading="Novo no projeto")

    # Notas mais conectadas — sem alias em tabela (| quebra células Markdown)
    top = sorted(conexoes.items(), key=lambda x: x[1], reverse=True)[:5]
    top_lines = ["| ID | Título | Conexões |", "| -- | ------ | -------- |"]
    for name, count in top:
        n = nome_map[name]
        top_lines.append(f"| {_link(name)} | {n['title']} | {count} |")
    content = _replace_section(
        content,
        "Notas mais conectadas",
        "\n".join(top_lines),
        until_heading="Padrões mais violados pela IA",
    )

    # Padrões IA — preencher de ai-corrections quando existirem
    ai = [n for n in notas if n["type"] == "ai-correction"]
    if ai:
        ai_lines = ["| Padrão violado | ID | Título |", "| -------------- | -- | ------ |"]
        for n in ai:
            ai_lines.append(
                f"| {n['pattern_violated'] or '—'} | {_link(n['name'])} | {n['title']} |"
            )
        content = _replace_section(
            content,
            "Padrões mais violados pela IA",
            "\n".join(ai_lines),
            until_heading="Como usar no Obsidian",
        )

    with open(readme_path, "w", encoding="utf-8") as fh:
        fh.write(content)


def main() -> None:
    notas = collect_notes()
    nome_map = {n["name"]: n for n in notas}
    conexoes = _compute_connections(notas)

    with open(f"{BASE}/index.md", "w", encoding="utf-8") as fh:
        fh.write(build_index(notas, conexoes))

    update_readme(notas, conexoes, nome_map)

    top3 = sorted(conexoes.items(), key=lambda x: x[1], reverse=True)[:3]
    print(f"✅ kb-index: {len(notas)} notas · {HOJE}")
    for name, count in top3:
        print(f"   · {_link(name)} — {count} conexões")


if __name__ == "__main__":
    main()
