#!/usr/bin/env python3
"""Valida a saúde da Base Cognitiva (checks do cb-audit).

Falha (exit 1) apenas em achados críticos:
  - front matter obrigatório ausente/incompleto
  - IDs duplicados
  - wikilinks quebrados

Demais categorias (atenção / sugestão) são impressas como aviso e não
quebram o CI — alinhado ao relatório do skill cb-audit.

Uso local (na raiz do repositório):
  python cognitive-base/.github/scripts/validate_kb.py
  python cognitive-base/.github/scripts/validate_kb.py --base cognitive-base
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import defaultdict
from datetime import date
from typing import Any

ARTEFATOS = {
    "README.md",
    "index.md",
    "CONTRIBUTING.md",
    "glossary.md",
    "mindmap.md",
    "note.md",
}

PREFIXO_POR_QUADRANTE = {
    "decisions": "dec-",
    "specs": "spc-",
    "system": "sys-",
    "guides": "gd-",
    "ops": "ops-",
}

STATUS_VALIDOS = {"draft", "approved", "superseded"}
CAMPOS_OBRIGATORIOS = ("id", "type", "title", "status", "created", "author")

SECOES_AI_CORRECTION = [
    "O que aconteceu",
    "O que foi gerado",
    "O que deveria",
    "Por que a IA errou",
    "Como evitar",
]

SUBPASTAS_POR_TIPO_PROIBIDAS = {
    "adrs",
    "business-rules",
    "features",
    "use-cases",
    "runbooks",
    "incidents",
    "apis",
    "events",
}

# Categorias que quebram o CI (🔴 Crítico no cb-audit)
CRITICOS = ("frontmatter", "ids_duplicados", "wikilinks_quebrados")

# Placeholders do template — não contam como link quebrado
WIKILINK_PLACEHOLDERS = {"nome-do-arquivo"}


def fm_field(fm: str, key: str, default: str = "") -> str:
    match = re.search(rf'^{key}:\s*"?(.+?)"?\s*$', fm, re.M)
    return match.group(1).strip('"').strip() if match else default


def norm(path: str) -> str:
    return path.replace("\\", "/")


def quadrante_de(base: str, path: str) -> str:
    rel = os.path.relpath(path, base).replace("\\", "/")
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else ""


def collect_notas(base: str, achados: dict[str, list[str]]) -> list[dict[str, Any]]:
    notas: list[dict[str, Any]] = []

    for path in sorted(glob.glob(os.path.join(base, "**", "*.md"), recursive=True)):
        nome = os.path.basename(path)
        if nome in ARTEFATOS:
            continue
        if "/meta/templates/" in norm(path):
            continue

        with open(path, encoding="utf-8") as fh:
            content = fh.read()

        fm_match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not fm_match:
            achados["frontmatter"].append(f"SEM FRONT MATTER: {path}")
            continue
        fm = fm_match.group(1)

        for campo in CAMPOS_OBRIGATORIOS:
            if not re.search(rf"^{campo}:", fm, re.M):
                achados["frontmatter"].append(f"FALTA '{campo}': {path}")

        nome_curto = os.path.splitext(nome)[0]
        notas.append(
            {
                "path": path,
                "nome": nome_curto,
                "quadrante": quadrante_de(base, path),
                "id": fm_field(fm, "id"),
                "type": fm_field(fm, "type"),
                "status": fm_field(fm, "status"),
                "created": fm_field(fm, "created"),
                "updated": fm_field(fm, "updated"),
                "wikilinks": re.findall(r"\[\[([^\]|#]+)", content),
                "content": content,
            }
        )

    return notas


def check_ids_duplicados(notas: list[dict[str, Any]], achados: dict[str, list[str]]) -> None:
    contagem_id: dict[str, list[str]] = defaultdict(list)
    for n in notas:
        if n["id"]:
            contagem_id[n["id"]].append(n["path"])
    for id_, paths in contagem_id.items():
        if len(paths) > 1:
            achados["ids_duplicados"].append(f"{id_}: {', '.join(paths)}")


def check_prefixo_quadrante(notas: list[dict[str, Any]], achados: dict[str, list[str]]) -> None:
    for n in notas:
        prefixo = PREFIXO_POR_QUADRANTE.get(n["quadrante"])
        if prefixo and n["id"] and not n["id"].startswith(prefixo):
            achados["prefixo_errado"].append(
                f"{n['path']} (esperado '{prefixo}*', achou '{n['id']}')"
            )


def check_status_invalido(notas: list[dict[str, Any]], achados: dict[str, list[str]]) -> None:
    for n in notas:
        if n["status"] and n["status"] not in STATUS_VALIDOS:
            achados["status_invalido"].append(f"{n['path']}: status='{n['status']}'")


def check_wikilinks_quebrados(base: str, notas: list[dict[str, Any]], achados: dict[str, list[str]]) -> None:
    # Inclui artefatos (README, glossary, …) — wikilinks para eles são válidos.
    nomes_existentes = {
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(base, "**", "*.md"), recursive=True)
    }
    skip_markers = ("/meta/templates/", "/.github/")

    for path in glob.glob(os.path.join(base, "**", "*.md"), recursive=True):
        npath = norm(path)
        if any(marker in npath for marker in skip_markers):
            continue
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for alvo in re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", line):
                    alvo = alvo.strip()
                    if alvo.endswith("/"):
                        continue
                    nome_alvo = os.path.splitext(os.path.basename(alvo))[0]
                    if nome_alvo in WIKILINK_PLACEHOLDERS:
                        continue
                    if nome_alvo not in nomes_existentes:
                        achados["wikilinks_quebrados"].append(
                            f"[[{alvo}]] em {path}:{lineno}"
                        )


def check_notas_orfas(notas: list[dict[str, Any]], achados: dict[str, list[str]]) -> None:
    mencionados = {
        os.path.splitext(os.path.basename(wl))[0] for n in notas for wl in n["wikilinks"]
    }
    for n in notas:
        if n["nome"] not in mencionados and n["quadrante"] != "meta":
            achados["orfas"].append(n["path"])


def check_notas_estagnadas(notas: list[dict[str, Any]], achados: dict[str, list[str]]) -> str:
    hoje = date.today()
    for n in notas:
        data_ref = n["updated"] or n["created"]
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", data_ref or "")
        if not m:
            continue
        dias = (hoje - date(*map(int, m.groups()))).days
        if n["status"] == "draft" and dias > 30:
            achados["draft_estagnado"].append(f"{n['path']} ({dias} dias)")
        elif n["status"] == "approved" and dias > 90:
            achados["approved_sem_revisao"].append(f"{n['path']} ({dias} dias)")
    return str(hoje)


def check_subpasta_por_tipo(base: str, achados: dict[str, list[str]]) -> None:
    for d in glob.glob(os.path.join(base, "**", ""), recursive=True):
        nome_dir = norm(d).rstrip("/").split("/")[-1].lower()
        if nome_dir in SUBPASTAS_POR_TIPO_PROIBIDAS:
            achados["subpasta_por_tipo"].append(d)


def check_adr_sem_howto(notas: list[dict[str, Any]], achados: dict[str, list[str]]) -> None:
    ids_guides_mencionados = {
        os.path.splitext(os.path.basename(wl))[0]
        for n in notas
        if n["quadrante"] == "guides"
        for wl in n["wikilinks"]
    }
    for n in notas:
        if (
            n["quadrante"] == "decisions"
            and n["type"] in ("adr", "dev-pattern")
            and n["nome"] not in ids_guides_mencionados
        ):
            achados["adr_sem_howto"].append(n["path"])


def check_ai_correction_incompleta(
    notas: list[dict[str, Any]], achados: dict[str, list[str]]
) -> None:
    for n in notas:
        if n["type"] == "ai-correction":
            for secao in SECOES_AI_CORRECTION:
                if secao not in n["content"]:
                    achados["ai_correction_incompleta"].append(
                        f"{n['path']}: falta '{secao}'"
                    )


def run_checks(base: str) -> dict[str, Any]:
    achados: dict[str, list[str]] = defaultdict(list)
    notas = collect_notas(base, achados)

    check_ids_duplicados(notas, achados)
    check_prefixo_quadrante(notas, achados)
    check_status_invalido(notas, achados)
    check_wikilinks_quebrados(base, notas, achados)
    check_notas_orfas(notas, achados)
    hoje = check_notas_estagnadas(notas, achados)
    check_subpasta_por_tipo(base, achados)
    check_adr_sem_howto(notas, achados)
    check_ai_correction_incompleta(notas, achados)

    return {"hoje": hoje, "total": len(notas), **achados}


def _print_bloco(titulo: str, itens: list[str]) -> None:
    if not itens:
        return
    print(f"\n{titulo} ({len(itens)}):")
    for item in itens:
        print(f"  - {item}")


def report(result: dict[str, Any]) -> int:
    criticos: list[str] = []
    for key in CRITICOS:
        criticos.extend(result.get(key, []))

    atencao_keys = (
        "draft_estagnado",
        "approved_sem_revisao",
        "prefixo_errado",
        "status_invalido",
        "subpasta_por_tipo",
        "ai_correction_incompleta",
    )
    sugestao_keys = ("orfas", "adr_sem_howto")

    n_atencao = sum(len(result.get(k, [])) for k in atencao_keys)
    n_sugestao = sum(len(result.get(k, [])) for k in sugestao_keys)

    print(
        f"Relatório de Auditoria — Base Cognitiva\n"
        f"Data: {result.get('hoje')} · Notas analisadas: {result.get('total', 0)}"
    )

    _print_bloco("CRITICO", criticos)
    for key in atencao_keys:
        _print_bloco(f"ATENCAO/{key}", result.get(key, []))
    for key in sugestao_keys:
        _print_bloco(f"SUGESTAO/{key}", result.get(key, []))

    print(
        f"\nResumo: {len(criticos)} críticos · {n_atencao} atenção · {n_sugestao} sugestões"
    )

    if criticos:
        print("Saude geral: Critica — CI falha.")
        return 1

    if n_atencao:
        print("Saude geral: Requer atencao — CI passa (avisos acima).")
    else:
        print("Saude geral: Boa.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida a Base Cognitiva (cb-audit CI)")
    parser.add_argument(
        "--base",
        default=os.environ.get("CB_BASE", "cognitive-base"),
        help="Caminho da pasta cognitive-base (default: cognitive-base)",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.base):
        print(f"Erro: diretório não encontrado: {args.base}", file=sys.stderr)
        return 1

    return report(run_checks(args.base))


if __name__ == "__main__":
    sys.exit(main())
