import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import List, Optional, Tuple

from tree_sitter_language_pack import get_parser

from codesteer_atlas.config import SUPPORTED_EXTENSIONS
from codesteer_atlas.models import CodeChunk
from codesteer_atlas.rationale import extract_rationale_refs, serialize_rationale_refs

# Tamanho máximo de conteúdo por chunk (~256 tokens para all-MiniLM-L6-v2)
_CHUNK_MAX_CHARS = 1000

# Mapeamento de nós Tree-sitter SQL → tipo de escopo semântico
_SQL_STATEMENT_SCOPE_TYPES = {
    "create_table": "table",
    "create_view": "view",
    "create_function": "function",
    "create_procedure": "procedure",
    "create_index": "index",
    "create_schema": "schema",
    "create_type": "type",
    "create_sequence": "sequence",
    "alter_table": "alter",
    "drop_table": "drop",
    "drop_view": "drop",
    "drop_function": "drop",
    "drop_index": "drop",
    "select": "query",
    "insert": "query",
    "update": "query",
    "delete": "query",
    "merge": "query",
    "truncate": "query",
}

# @MindContext: kinds de nó de import por linguagem (§3.3). Cada entrada foi SONDADA
# na gramática real do tree_sitter_language_pack — os nomes divergem entre gramáticas
# e a leitura literal do roadmap erraria em pelo menos quatro delas.
# @MindWhy: Go expõe um `import_spec` por import (o `import_declaration` agrupa vários);
# Ruby e Elixir não têm nó de import — `require`/`import` são `call` genéricos, filtrados
# por palavra-chave em `_IMPORT_CALL_KEYWORDS`.
_IMPORT_NODE_KINDS: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement", "export_statement"},
    "typescript": {"import_statement", "export_statement"},
    "go": {"import_spec"},
    "java": {"import_declaration"},
    "csharp": {"using_directive"},
    "rust": {"use_declaration"},
    "kotlin": {"import_header"},
    "php": {
        "namespace_use_clause",
        "include_expression",
        "include_once_expression",
        "require_expression",
        "require_once_expression",
    },
    "ruby": {"call"},
    "swift": {"import_declaration"},
    "scala": {"import_declaration"},
    "elixir": {"call"},
}

# Linguagens sem nó de import dedicado: aceita só o `call` cuja chamada é uma diretiva.
_IMPORT_CALL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ruby": ("require_relative", "require", "load"),
    "elixir": ("import", "alias", "require", "use"),
}

# Tokens que prefixam o alvo do import e não fazem parte dele.
_IMPORT_DROP_TOKENS = frozenset(
    {
        "import",
        "using",
        "use",
        "global",
        "static",
        "pub",
        "extern",
        "crate",
        "require",
        "require_once",
        "require_relative",
        "include",
        "include_once",
        "load",
        "alias",
        "function",
        "const",
    }
)

# Sufixos de wildcard/selector que não pertencem ao caminho do módulo
# (`import a.c.*` em Java, `mutable._` em Scala, `c.d.*` em Kotlin).
_IMPORT_TRAILING_NOISE = ("::*", ".*", "._", "*", "\\", ".", "::")

# @MindContext: kinds de declaração de namespace/package (DECISÃO-003), também sondados.
# Kotlin usa `package_header` e Scala `package_clause` — NÃO `package_declaration`,
# como a premissa de architecture.md supunha.
_PACKAGE_DECLARATION_NODE_KINDS: dict[str, set[str]] = {
    "java": {"package_declaration"},
    "csharp": {"file_scoped_namespace_declaration", "namespace_declaration"},
    "kotlin": {"package_header"},
    "scala": {"package_clause"},
}

# Filho que carrega o nome qualificado dentro do nó de declaração. Decodificar o nó
# inteiro não serve: em C# o `namespace_declaration` de bloco contém todo o corpo.
_PACKAGE_NAME_NODE_KINDS = frozenset(
    {"scoped_identifier", "qualified_name", "package_identifier", "identifier"}
)

_QUOTED_IMPORT_RE = re.compile(r"""["']([^"']+)["']""")


def _normalize_import_target(statement: str) -> Optional[str]:
    """
    Reduz o texto cru de um nó de import ao alvo do módulo/arquivo.

    Cobre as quatro formas encontradas na sondagem: alvo entre aspas (Go, Ruby,
    include/require do PHP), alias por `=` (C#), prefixo de palavra-chave
    (`import static`, `pub use`, `global using`) e selector `{...}` (Scala, PHP).
    """
    text = statement.strip().rstrip(";").strip()
    if not text:
        return None

    quoted = _QUOTED_IMPORT_RE.search(text)
    if quoted:
        return quoted.group(1).strip() or None

    # `import c.d.{E, F}` / `use App\{A, B}`: o grupo não faz parte do caminho
    text = re.split(r"[{(]", text, maxsplit=1)[0].strip()

    tokens = text.split()
    while tokens and tokens[0].casefold() in _IMPORT_DROP_TOKENS:
        tokens.pop(0)
    if not tokens:
        return None
    target = tokens[tokens.index("=") + 1] if "=" in tokens else tokens[0]

    changed = True
    while changed:
        changed = False
        for noise in _IMPORT_TRAILING_NOISE:
            if target.endswith(noise) and len(target) > len(noise):
                target = target[: -len(noise)]
                changed = True
    return target.strip() or None


class IncompatibleParserError(RuntimeError):
    """
    Ambiente cujo parser não expõe nem a API nativa do language-pack
    (`parse(str)` / `root_node()` / `kind()`) nem a clássica do `tree-sitter`
    (`parse(bytes)` / `root_node` / `type`).

    Levantada UMA vez na criação do primeiro parser: sem isso um ambiente
    quebrado produz índice vazio e ainda assim reporta sucesso [D].
    """


class _Point:
    """Posição (row, column) desacoplada do `tree_sitter.Point` nativo."""

    __slots__ = ("row", "column")

    def __init__(self, row: int, column: int):
        self.row = row
        self.column = column


def _normalize_point(point) -> _Point:
    """
    Extrai row/column sem usar `Point.row` da API clássica do tree-sitter.

    Com tree-sitter ≥0.26 + language-pack ≥1.13 (Parser clássico que o
    `uvx --from git+...` resolve ao ignorar o uv.lock), acessar `Point.row`
    no meio do walk da AST pode corromper o estado nativo no Windows e
    abortar o processo com access violation no próximo `child()`. A
    indexação `point[0]`/`point[1]` é segura nesse binding; a API nativa
    do language-pack 1.8–1.12 expõe `.row` sem `__getitem__`.
    """
    try:
        return _Point(point[0], point[1])
    except TypeError:
        return _Point(point.row, point.column)


class _CompatParser:
    """Normaliza parser nativo (str) e clássico (bytes) numa API única."""

    def __init__(self, parser, *, classic: bool):
        self._parser = parser
        self.classic = classic

    def parse(self, source_text: str):
        if self.classic:
            # Mantém os bytes vivos pelo lifetime da Tree (input emprestado).
            source_bytes = source_text.encode("utf-8")
            tree = self._parser.parse(source_bytes)
            if tree is None:
                return None
            return _CompatTree(tree, classic=True, source_bytes=source_bytes)

        tree = self._parser.parse(source_text)
        if tree is None:
            return None
        return _CompatTree(tree, classic=False)


class _CompatTree:
    def __init__(self, tree, *, classic: bool, source_bytes: Optional[bytes] = None):
        self._tree = tree
        self.classic = classic
        # Referência explícita evita UAF se o binding clássico só emprestar o buffer.
        self._source_bytes = source_bytes

    def root_node(self):
        raw = self._tree.root_node if self.classic else self._tree.root_node()
        return _CompatNode(raw, classic=self.classic)


class _CompatNode:
    def __init__(self, node, *, classic: bool):
        self._node = node
        self.classic = classic

    def kind(self) -> str:
        return self._node.type if self.classic else self._node.kind()

    def child_count(self) -> int:
        return self._node.child_count if self.classic else self._node.child_count()

    def child(self, index: int):
        raw = self._node.child(index)
        if raw is None:
            return None
        return _CompatNode(raw, classic=self.classic)

    def start_byte(self) -> int:
        return self._node.start_byte if self.classic else self._node.start_byte()

    def end_byte(self) -> int:
        return self._node.end_byte if self.classic else self._node.end_byte()

    def start_position(self):
        if self.classic:
            return _normalize_point(self._node.start_point)
        return self._node.start_position()

    def end_position(self):
        if self.classic:
            return _normalize_point(self._node.end_point)
        return self._node.end_position()


def _installed_language_pack_version() -> str:
    try:
        from importlib.metadata import version

        return version("tree-sitter-language-pack")
    except Exception:
        return "desconhecida"


def _probe_parser_classic(parser) -> bool:
    """
    Detecta qual API o `get_parser()` devolveu.

    - language-pack 1.8–1.12: binding nativo (`parse(str)`, métodos).
    - language-pack ≥1.13: volta a devolver `tree_sitter.Parser` clássico
      (`parse(bytes)`, properties) — e `uvx --from git+...` resolve isso porque
      ignora o `uv.lock`.
    """
    try:
        tree = parser.parse("x = 1\n")
    except TypeError as exc:
        message = str(exc).lower()
        if "byte" not in message and "str" not in message:
            raise IncompatibleParserError(
                "API do Tree-sitter incompatível com este chunker "
                f"(tree-sitter-language-pack instalada: {_installed_language_pack_version()}). "
                f"Erro ao verificar: {exc}."
            ) from exc
        try:
            tree = parser.parse(b"x = 1\n")
            root = tree.root_node
            if getattr(root, "type", None) is None:
                raise TypeError("root_node.type ausente na API clássica")
        except Exception as classic_exc:
            raise IncompatibleParserError(
                "API do Tree-sitter incompatível com este chunker "
                f"(tree-sitter-language-pack instalada: {_installed_language_pack_version()}). "
                "Nem parse(str)/kind() (nativa) nem parse(bytes)/type (clássica) funcionaram. "
                f"Erro ao verificar: {classic_exc}. "
                "Se o servidor MCP foi registrado com 'uvx --from git+...', limpe o cache "
                "do uv (uv cache clean) e reinstale."
            ) from classic_exc
        return True

    try:
        root = tree.root_node()
        root.kind()
    except Exception as exc:
        raise IncompatibleParserError(
            "API do Tree-sitter incompatível com este chunker "
            f"(tree-sitter-language-pack instalada: {_installed_language_pack_version()}). "
            "parse(str) respondeu, mas root_node()/kind() falharam. "
            f"Erro ao verificar: {exc}."
        ) from exc
    return False


class ASTChunker:
    """
    Responsável por parsear arquivos de código-fonte usando Tree-sitter e extrair
    símbolos sintáticos (classes, funções, métodos) como chunks de contexto coerentes.
    """

    def __init__(self):
        # Dicionário para armazenar parsers cacheificados por linguagem
        self.parsers = {}
        self._api_verified = False
        self._classic_api: Optional[bool] = None

    def _verify_parser_api(self, parser) -> bool:
        """
        Detecta, uma única vez, se o parser é nativo ou clássico.

        Retorna True quando a API clássica (`parse(bytes)`) está em uso.
        """
        if self._api_verified:
            return bool(self._classic_api)

        classic = _probe_parser_classic(parser)
        self._classic_api = classic
        self._api_verified = True
        return classic

    def _get_parser(self, language_name: str):
        """Retorna o parser correspondente para a linguagem ou cria um novo."""
        if language_name not in self.parsers:
            try:
                raw = get_parser(language_name)
            except Exception:
                raw = None

            if raw is None:
                self.parsers[language_name] = None
            else:
                classic = self._verify_parser_api(raw)
                self.parsers[language_name] = _CompatParser(raw, classic=classic)

        return self.parsers[language_name]

    def _generate_chunk_id(
        self, content: str, file_path: str, start_line: int, end_line: int
    ) -> str:
        """Gera um hash único SHA-256 para identificar o chunk de código."""
        hasher = hashlib.sha256()
        hasher.update(content.encode("utf-8"))
        hasher.update(file_path.encode("utf-8"))
        hasher.update(f"{start_line}-{end_line}".encode("utf-8"))
        return hasher.hexdigest()[:16]

    def _truncate_content(self, content: str, max_chars: int = _CHUNK_MAX_CHARS) -> str:
        """
        Trunca o código preservando as primeiras e últimas linhas se exceder
        a estimativa de tamanho em caracteres (~4 caracteres por token).
        """
        if len(content) <= max_chars:
            return content

        lines = content.splitlines()
        if len(lines) <= 10:
            return content

        # Mantém as primeiras 7 linhas (assinatura, docstring) e as últimas 3 linhas (retorno)
        header_lines = lines[:7]
        footer_lines = lines[-3:]

        truncated = (
            header_lines
            + ["# ... [conteúdo truncado para respeitar limites do modelo] ..."]
            + footer_lines
        )
        return "\n".join(truncated)

    def _extract_symbol_name(self, node, source_text: str) -> str:
        """Extrai o nome do símbolo a partir de nós identificadores do Tree-sitter."""
        # Procura por nós filhos do tipo identifier para nomear o símbolo
        # Na API tree-sitter v0.25+, tudo são métodos.
        source_bytes = source_text.encode("utf-8")
        for i in range(node.child_count()):
            child = node.child(i)
            if child.kind() in ("identifier", "property_identifier", "field_identifier"):
                return source_bytes[child.start_byte():child.end_byte()].decode("utf-8", errors="ignore")
        return "anonymous"

    def _walk_tree(
        self,
        node,
        source_text: str,
        language: str,
        parent_scope: str = "",
        chunks: Optional[List[Tuple[int, int, str, str, str]]] = None,
    ) -> List[Tuple[int, int, str, str, str]]:
        """
        Percorre recursivamente a árvore AST identificando nós de interesse
        e acumulando os escopos para nomenclatura hierárquica.
        Retorna uma lista de tuplas: (start_line, end_line, scope_type, scope_name, content)
        """
        if chunks is None:
            chunks = []

        node_type = node.kind()
        is_symbol = False
        scope_type = ""
        current_scope = parent_scope

        # Mapeamento de nós de interesse dependendo da linguagem
        if language == "python":
            if node_type == "class_definition":
                is_symbol = True
                scope_type = "class"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name
            elif node_type == "function_definition":
                is_symbol = True
                # Se estiver dentro de uma classe, é um método
                scope_type = "method" if parent_scope else "function"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name

        elif language in ("javascript", "typescript"):
            if node_type in ("class_declaration",):
                is_symbol = True
                scope_type = "class"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name
            elif node_type in ("function_declaration",):
                is_symbol = True
                scope_type = "function"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name
            elif node_type in ("method_definition",):
                is_symbol = True
                scope_type = "method"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name

        elif language == "go":
            if node_type == "type_declaration":
                is_symbol = True
                scope_type = "class"  # Structs/Interfaces são mapeadas para classes
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name
            elif node_type == "function_declaration":
                is_symbol = True
                scope_type = "function"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name
            elif node_type == "method_declaration":
                is_symbol = True
                scope_type = "method"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name

        elif language == "csharp":
            if node_type in ("class_declaration", "interface_declaration", "struct_declaration", "record_declaration"):
                is_symbol = True
                scope_type = "class"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name
            elif node_type in ("method_declaration", "constructor_declaration", "destructor_declaration"):
                is_symbol = True
                scope_type = "method" if parent_scope else "function"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name

        elif language == "java":
            if node_type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
                is_symbol = True
                scope_type = "class"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name
            elif node_type in ("method_declaration", "constructor_declaration"):
                is_symbol = True
                scope_type = "method" if parent_scope else "function"
                name = self._extract_symbol_name(node, source_text)
                current_scope = f"{parent_scope}.{name}" if parent_scope else name

        if is_symbol:
            # Extrai o texto do nó a partir dos bytes (API tree-sitter v0.25+)
            source_bytes = source_text.encode("utf-8")
            content = source_bytes[node.start_byte():node.end_byte()].decode("utf-8", errors="ignore")

            # Linhas são 0-indexed no tree-sitter, convertemos para 1-indexed
            start_line = node.start_position().row + 1
            end_line = node.end_position().row + 1

            chunks.append((start_line, end_line, scope_type, current_scope, content))

        # Continua a busca nos filhos
        for i in range(node.child_count()):
            child = node.child(i)
            self._walk_tree(child, source_text, language, current_scope, chunks)

        return chunks

    def chunk_file(self, file_path: Path, repo_name: str) -> List[CodeChunk]:
        """
        Lê um arquivo de código, faz o parse AST e retorna a lista de CodeChunks extraídos.
        """
        if not file_path.exists():
            return []

        ext = file_path.suffix.lower()
        language = SUPPORTED_EXTENSIONS.get(ext)
        if not language:
            return []

        # Desvia o fluxo para os métodos de chunking textual e markdown não-AST
        if language == "markdown":
            return self._chunk_markdown(file_path, repo_name)
        elif language == "sql":
            return self._chunk_sql(file_path, repo_name)
        elif language in ("text", "xml", "razor", "dart", "pascal", "vb6"):
            return self._chunk_text(file_path, repo_name, language)

        parser = self._get_parser(language)
        if not parser:
            # Silenciosamente ignora se o parser não estiver disponível (log no stderr tratado fora)
            return []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source_text = f.read()
        except Exception:
            return []

        # `_CompatParser` aceita str e adapta nativa/clássica por baixo
        tree = parser.parse(source_text)
        if not tree:
            return []

        # Extrai símbolos estruturados da árvore AST
        symbols = self._walk_tree(tree.root_node(), source_text, language)

        chunks = []
        timestamp = datetime.now(timezone.utc).isoformat()
        relative_path = PurePath(
            file_path.relative_to(file_path.parents[len(file_path.parents) - 1])
            if file_path.is_absolute()
            else file_path
        ).as_posix()
        # Substitui caminhos relativos ao workspace para manter padrão amigável
        # (será limpo para ser relativo ao diretório indexado no indexer)
        # [L] file_path sempre persistido em formato POSIX, independente do OS de origem

        for start_line, end_line, scope_type, scope_name, content in symbols:
            references = serialize_rationale_refs(extract_rationale_refs(content))
            truncated_content = self._truncate_content(content)
            chunk_id = self._generate_chunk_id(
                truncated_content, relative_path, start_line, end_line
            )

            chunks.append(
                CodeChunk(
                    id=chunk_id,
                    file_path=relative_path,
                    repo=repo_name,
                    start_line=start_line,
                    end_line=end_line,
                    scope_type=scope_type,
                    scope_name=scope_name,
                    language=language,
                    content=truncated_content,
                    indexed_at=timestamp,
                    references=references,
                )
            )

        # Se nenhum símbolo AST foi extraído (ex: script top-level sequencial),
        # gera um chunk 'module' representando o arquivo inteiro
        if not chunks and len(source_text) > 0:
            # Conta o número de linhas
            lines = source_text.splitlines()
            total_lines = len(lines)

            references = serialize_rationale_refs(extract_rationale_refs(source_text))
            truncated_content = self._truncate_content(source_text)
            chunk_id = self._generate_chunk_id(truncated_content, relative_path, 1, total_lines)

            chunks.append(
                CodeChunk(
                    id=chunk_id,
                    file_path=relative_path,
                    repo=repo_name,
                    start_line=1,
                    end_line=total_lines if total_lines > 0 else 1,
                    scope_type="module",
                    scope_name=file_path.stem,
                    language=language,
                    content=truncated_content,
                    indexed_at=timestamp,
                    references=references,
                )
            )

        return chunks

    def _decode_node(self, node, source_bytes: bytes) -> str:
        """Extrai o texto de um nó Tree-sitter a partir dos bytes do source original."""
        return source_bytes[node.start_byte() : node.end_byte()].decode("utf-8", errors="ignore")

    def _collect_nodes_by_kind(self, node, kinds: set[str], found: Optional[List] = None) -> List:
        if found is None:
            found = []
        if node.kind() in kinds:
            found.append(node)
        for i in range(node.child_count()):
            self._collect_nodes_by_kind(node.child(i), kinds, found)
        return found

    def _extract_python_imports(self, tree, source_text: str) -> List[str]:
        source_bytes = source_text.encode("utf-8")
        imports: List[str] = []
        seen = set()
        for node in self._collect_nodes_by_kind(
            tree.root_node(), {"import_statement", "import_from_statement"}
        ):
            for i in range(node.child_count()):
                child = node.child(i)
                if child.kind() not in {"dotted_name", "relative_import"}:
                    continue
                value = self._decode_node(child, source_bytes).strip()
                if not value or value in seen:
                    continue
                seen.add(value)
                imports.append(value)
                if node.kind() == "import_from_statement":
                    break
        return imports

    def _extract_js_ts_imports(self, tree, source_text: str) -> List[str]:
        source_bytes = source_text.encode("utf-8")
        imports: List[str] = []
        seen = set()
        for node in self._collect_nodes_by_kind(tree.root_node(), {"import_statement", "export_statement"}):
            statement = self._decode_node(node, source_bytes)
            match = re.search(r'["\']([^"\']+)["\']', statement)
            if not match:
                continue
            value = match.group(1).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            imports.append(value)
        return imports

    def _extract_generic_imports(self, tree, source_text: str, language: str) -> List[str]:
        """Extrai alvos crus para as linguagens despachadas por `_IMPORT_NODE_KINDS`."""
        source_bytes = source_text.encode("utf-8")
        keywords = _IMPORT_CALL_KEYWORDS.get(language)
        imports: List[str] = []
        seen = set()
        for node in self._collect_nodes_by_kind(tree.root_node(), _IMPORT_NODE_KINDS[language]):
            statement = self._decode_node(node, source_bytes)
            if keywords:
                head = statement.lstrip().split(maxsplit=1)[0] if statement.strip() else ""
                if head.rstrip("(").split("(")[0] not in keywords:
                    continue
            value = _normalize_import_target(statement)
            if not value or value in seen:
                continue
            seen.add(value)
            imports.append(value)
        return imports

    def _parse_source(self, file_path: Path, language: str):
        """Lê e parseia um arquivo com o parser cacheado. `(tree, source_text)` ou `None`."""
        parser = self._get_parser(language)
        if not parser:
            return None

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source_text = f.read()
        except Exception:
            return None

        try:
            tree = parser.parse(source_text)
        except Exception:
            return None
        if not tree:
            return None
        return tree, source_text

    def extract_imports(self, file_path: Path) -> List[str]:
        """
        Retorna alvos crus de import, despachados por `_IMPORT_NODE_KINDS` (§3.3).

        Linguagem sem entrada na tabela retorna `[]` — degradação silenciosa aqui é
        intencional; quem declara a lacuna é `resolution_coverage` (Princípio VI).
        """
        if not file_path.exists():
            return []

        ext = file_path.suffix.lower()
        language = SUPPORTED_EXTENSIONS.get(ext)
        if language not in _IMPORT_NODE_KINDS:
            return []

        parsed = self._parse_source(file_path, language)
        if parsed is None:
            return []
        tree, source_text = parsed

        if language == "python":
            return self._extract_python_imports(tree, source_text)
        if language in {"javascript", "typescript"}:
            return self._extract_js_ts_imports(tree, source_text)
        return self._extract_generic_imports(tree, source_text, language)

    def extract_package_declaration(self, file_path: Path) -> Optional[str]:
        """
        Retorna o namespace/package declarado no arquivo (Java, C#, Kotlin, Scala).

        É o outro lado da aresta em linguagens cujo namespace não é o caminho
        (DECISÃO-003): `using MinhaApp.Servicos` só resolve consultando quem declara
        aquele namespace. `None` para qualquer outra linguagem, arquivo sem declaração
        ou parse falho.
        """
        if not file_path.exists():
            return None

        ext = file_path.suffix.lower()
        language = SUPPORTED_EXTENSIONS.get(ext)
        if language is None:
            return None
        kinds = _PACKAGE_DECLARATION_NODE_KINDS.get(language)
        if not kinds:
            return None

        parsed = self._parse_source(file_path, language)
        if parsed is None:
            return None
        tree, source_text = parsed
        source_bytes = source_text.encode("utf-8")

        for node in self._collect_nodes_by_kind(tree.root_node(), kinds):
            for i in range(node.child_count()):
                child = node.child(i)
                if child.kind() not in _PACKAGE_NAME_NODE_KINDS:
                    continue
                value = self._decode_node(child, source_bytes).strip()
                if value:
                    return value
        return None

    def _first_sql_statement_child(self, statement_node):
        """Retorna o primeiro filho semântico de um nó `statement` (ignora ';')."""
        for i in range(statement_node.child_count()):
            child = statement_node.child(i)
            if child.kind() != ";":
                return child
        return None

    def _find_sql_identifier(self, node, source_bytes: bytes) -> str | None:
        """Busca recursivamente o primeiro identifier dentro de object_reference ou relation."""
        if node.kind() in ("object_reference", "relation"):
            for i in range(node.child_count()):
                child = node.child(i)
                if child.kind() == "identifier":
                    return source_bytes[child.start_byte() : child.end_byte()].decode(
                        "utf-8", errors="ignore"
                    )
        for i in range(node.child_count()):
            found = self._find_sql_identifier(node.child(i), source_bytes)
            if found:
                return found
        return None

    def _extract_sql_statement_name(
        self, statement_node, stmt_kind: str, source_bytes: bytes, index: int
    ) -> str:
        """Deriva um nome legível para o statement SQL (tabela, view, query, etc.)."""
        inner = self._first_sql_statement_child(statement_node)
        if inner is None:
            return f"statement_{index}"

        if stmt_kind == "create_index":
            for i in range(inner.child_count()):
                child = inner.child(i)
                if child.kind() == "identifier":
                    return source_bytes[child.start_byte() : child.end_byte()].decode(
                        "utf-8", errors="ignore"
                    )

        named = self._find_sql_identifier(inner, source_bytes)
        if named:
            if stmt_kind == "select":
                return f"select_{named}"
            return named

        if stmt_kind == "select":
            for i in range(statement_node.child_count()):
                child = statement_node.child(i)
                if child.kind() == "from":
                    from_name = self._find_sql_identifier(child, source_bytes)
                    if from_name:
                        return f"select_{from_name}"

        scope_type = _SQL_STATEMENT_SCOPE_TYPES.get(stmt_kind, "statement")
        return f"{scope_type}_{index}"

    def _split_oversized_lines(
        self, content: str, base_start_line: int
    ) -> List[Tuple[int, int, str]]:
        """
        Divide conteúdo SQL grande em blocos de até ~1000 caracteres,
        preservando limites de linha (não corta linha ao meio).
        """
        if len(content) <= _CHUNK_MAX_CHARS:
            return [(base_start_line, base_start_line + content.count("\n"), content)]

        lines = content.splitlines(keepends=True)
        if len(lines) == 1 and len(content) > _CHUNK_MAX_CHARS:
            truncated = self._truncate_content(content)
            return [
                (
                    base_start_line,
                    base_start_line + truncated.count("\n"),
                    truncated,
                )
            ]

        parts: List[Tuple[int, int, str]] = []
        current_parts: List[str] = []
        current_len = 0
        chunk_start_line = base_start_line

        for line in lines:
            if current_len + len(line) > _CHUNK_MAX_CHARS and current_parts:
                chunk_text = "".join(current_parts)
                stripped = chunk_text.rstrip("\n")
                chunk_end_line = chunk_start_line + stripped.count("\n")
                parts.append((chunk_start_line, chunk_end_line, stripped))
                chunk_start_line = chunk_end_line + 1
                current_parts = [line]
                current_len = len(line)
            else:
                current_parts.append(line)
                current_len += len(line)

        if current_parts:
            chunk_text = "".join(current_parts)
            stripped = chunk_text.rstrip("\n")
            chunk_end_line = chunk_start_line + stripped.count("\n")
            parts.append((chunk_start_line, chunk_end_line, stripped))

        return parts

    def _chunk_sql(self, file_path: Path, repo_name: str) -> List[CodeChunk]:
        """
        Divide arquivos `.sql` em chunks por statement (DDL/DML) via Tree-sitter.
        Statements grandes são particionados por linhas; fallback textual se o parse falhar.
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source_text = f.read()
        except Exception:
            return []

        if not source_text.strip():
            return []

        relative_path = PurePath(file_path).as_posix()
        timestamp = datetime.now(timezone.utc).isoformat()

        parser = self._get_parser("sql")
        if not parser:
            return self._chunk_text_content(source_text, file_path, repo_name, "sql")

        tree = parser.parse(source_text)
        if not tree:
            return self._chunk_text_content(source_text, file_path, repo_name, "sql")

        statement_nodes = [
            tree.root_node().child(i)
            for i in range(tree.root_node().child_count())
            if tree.root_node().child(i).kind() == "statement"
        ]

        if not statement_nodes:
            return self._chunk_text_content(source_text, file_path, repo_name, "sql")

        source_bytes = source_text.encode("utf-8")
        chunks: List[CodeChunk] = []

        for index, statement_node in enumerate(statement_nodes, start=1):
            content = self._decode_node(statement_node, source_bytes).strip()
            if not content:
                continue

            inner = self._first_sql_statement_child(statement_node)
            stmt_kind = inner.kind() if inner is not None else "statement"
            scope_type = _SQL_STATEMENT_SCOPE_TYPES.get(stmt_kind, "statement")
            scope_name = self._extract_sql_statement_name(
                statement_node, stmt_kind, source_bytes, index
            )
            base_start_line = statement_node.start_position().row + 1

            parts = self._split_oversized_lines(content, base_start_line)
            for part_index, (start_line, end_line, part_content) in enumerate(parts, start=1):
                part_name = (
                    f"{scope_name} (Parte {part_index})"
                    if len(parts) > 1
                    else scope_name
                )
                chunk_id = self._generate_chunk_id(
                    part_content, relative_path, start_line, end_line
                )
                chunks.append(
                    CodeChunk(
                        id=chunk_id,
                        file_path=relative_path,
                        repo=repo_name,
                        start_line=start_line,
                        end_line=end_line if end_line >= start_line else start_line,
                        scope_type=scope_type,
                        scope_name=part_name,
                        language="sql",
                        content=part_content,
                        indexed_at=timestamp,
                    )
                )

        return chunks

    def _chunk_markdown(self, file_path: Path, repo_name: str) -> List[CodeChunk]:
        """
        Divide um arquivo Markdown (.md) em seções baseadas em cabeçalhos (#).
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source_content = f.read()
        except Exception:
            return []

        if not source_content:
            return []

        relative_path = PurePath(file_path).as_posix()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Encontra cabeçalhos como "# Título", "## Subtítulo" etc. no início de linhas
        pattern = re.compile(r'^(#+\s+.+)$', re.MULTILINE)
        matches = list(pattern.finditer(source_content))

        if not matches:
            # Se não houver cabeçalhos, processa o arquivo como texto simples
            return self._chunk_text_content(source_content, file_path, repo_name, "markdown")

        sections = []
        # Divide o conteúdo com base nos índices das correspondências de cabeçalho
        for i, match in enumerate(matches):
            start_pos = match.start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(source_content)
            section_content = source_content[start_pos:end_pos].strip()
            heading_text = match.group(1).strip()
            
            # Limpa o heading de '#' extras (ex: '## Decisão' -> 'Decisão')
            clean_heading = heading_text.lstrip('#').strip()
            sections.append((clean_heading, section_content, start_pos, end_pos))

        # Primeiras linhas antes do primeiro cabeçalho (se houver conteúdo relevante)
        intro_content = source_content[0:matches[0].start()].strip()
        if intro_content:
            sections.insert(0, ("Intro", intro_content, 0, matches[0].start()))

        chunks = []
        for scope_name, section_content, start_pos, end_pos in sections:
            # Calcula as linhas de início e fim no arquivo original
            start_line = source_content[:start_pos].count('\n') + 1
            end_line = source_content[:end_pos].count('\n') + 1

            # Se a seção for muito grande (mais de 1000 caracteres), quebramos por parágrafos
            if len(section_content) > 1000:
                paragraphs = section_content.split("\n\n")
                current_chunk_parts: List[str] = []
                current_len = 0
                chunk_index = 1
                last_sec_search_index = 0
                
                for paragraph in paragraphs:
                    paragraph = paragraph.strip()
                    if not paragraph:
                        continue
                    if current_len + len(paragraph) > 1000 and current_chunk_parts:
                        chunk_text = "\n\n".join(current_chunk_parts)
                        chunk_offset = section_content.find(chunk_text, last_sec_search_index)
                        if chunk_offset == -1:
                            chunk_offset = last_sec_search_index
                        chunk_start_line = start_line + section_content[:chunk_offset].count('\n')
                        chunk_end_line = chunk_start_line + chunk_text.count('\n')
                        
                        chunk_id = self._generate_chunk_id(chunk_text, relative_path, chunk_start_line, chunk_end_line)
                        chunks.append(
                            CodeChunk(
                                id=chunk_id,
                                file_path=relative_path,
                                repo=repo_name,
                                start_line=chunk_start_line,
                                end_line=chunk_end_line,
                                scope_type="section",
                                scope_name=f"{scope_name} (Parte {chunk_index})",
                                language="markdown",
                                content=chunk_text,
                                indexed_at=timestamp,
                            )
                        )
                        last_sec_search_index = chunk_offset + len(chunk_text)
                        current_chunk_parts = [paragraph]
                        current_len = len(paragraph)
                        chunk_index += 1
                    else:
                        current_chunk_parts.append(paragraph)
                        current_len += len(paragraph) + 2 # conta \n\n
                
                if current_chunk_parts:
                    chunk_text = "\n\n".join(current_chunk_parts)
                    chunk_offset = section_content.find(chunk_text, last_sec_search_index)
                    if chunk_offset == -1:
                        chunk_offset = last_sec_search_index
                    chunk_start_line = start_line + section_content[:chunk_offset].count('\n')
                    chunk_end_line = chunk_start_line + chunk_text.count('\n')
                    chunk_id = self._generate_chunk_id(chunk_text, relative_path, chunk_start_line, chunk_end_line)
                    chunks.append(
                        CodeChunk(
                            id=chunk_id,
                            file_path=relative_path,
                            repo=repo_name,
                            start_line=chunk_start_line,
                            end_line=chunk_end_line,
                            scope_type="section",
                            scope_name=f"{scope_name} (Parte {chunk_index})" if chunk_index > 1 else scope_name,
                            language="markdown",
                            content=chunk_text,
                            indexed_at=timestamp,
                        )
                    )
            else:
                chunk_id = self._generate_chunk_id(section_content, relative_path, start_line, end_line)
                chunks.append(
                    CodeChunk(
                        id=chunk_id,
                        file_path=relative_path,
                        repo=repo_name,
                        start_line=start_line,
                        end_line=end_line,
                        scope_type="section",
                        scope_name=scope_name,
                        language="markdown",
                        content=section_content,
                        indexed_at=timestamp,
                    )
                )

        return chunks

    def _chunk_text(self, file_path: Path, repo_name: str, language: str) -> List[CodeChunk]:
        """
        Divide um arquivo de texto simples ou código sem AST em parágrafos e agrupa em chunks.
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source_content = f.read()
        except Exception:
            return []

        return self._chunk_text_content(source_content, file_path, repo_name, language)

    def _chunk_text_content(self, source_content: str, file_path: Path, repo_name: str, language: str) -> List[CodeChunk]:
        """
        Lógica comum para chunking de texto simples baseado em parágrafos.
        """
        if not source_content:
            return []

        relative_path = PurePath(file_path).as_posix()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Divide por parágrafos
        paragraphs = source_content.split("\n\n")
        chunks = []
        
        current_chunk_parts: List[str] = []
        current_len = 0
        chunk_index = 1
        last_search_index = 0
        
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            
            # Se adicionar este parágrafo passar de 1000 caracteres, fecha o chunk anterior
            if current_len + len(paragraph) > 1000 and current_chunk_parts:
                chunk_text = "\n\n".join(current_chunk_parts)
                start_char_pos = source_content.find(chunk_text, last_search_index)
                if start_char_pos == -1:
                    start_char_pos = last_search_index
                start_line = source_content[:start_char_pos].count('\n') + 1
                end_line = start_line + chunk_text.count('\n')
                
                chunk_id = self._generate_chunk_id(chunk_text, relative_path, start_line, end_line)
                
                chunks.append(
                    CodeChunk(
                        id=chunk_id,
                        file_path=relative_path,
                        repo=repo_name,
                        start_line=start_line,
                        end_line=end_line,
                        scope_type="chunk",
                        scope_name=f"{file_path.stem}_chunk_{chunk_index}",
                        language=language,
                        content=chunk_text,
                        indexed_at=timestamp,
                    )
                )
                last_search_index = start_char_pos + len(chunk_text)
                current_chunk_parts = [paragraph]
                current_len = len(paragraph)
                chunk_index += 1
            else:
                current_chunk_parts.append(paragraph)
                current_len += len(paragraph) + 2 # conta \n\n
                
        # Adiciona o último chunk
        if current_chunk_parts:
            chunk_text = "\n\n".join(current_chunk_parts)
            start_char_pos = source_content.find(chunk_text, last_search_index)
            if start_char_pos == -1:
                start_char_pos = last_search_index
            start_line = source_content[:start_char_pos].count('\n') + 1
            end_line = start_line + chunk_text.count('\n')
            
            chunk_id = self._generate_chunk_id(chunk_text, relative_path, start_line, end_line)
            chunks.append(
                CodeChunk(
                    id=chunk_id,
                    file_path=relative_path,
                    repo=repo_name,
                    start_line=start_line,
                    end_line=end_line,
                    scope_type="chunk",
                    scope_name=f"{file_path.stem}_chunk_{chunk_index}" if chunk_index > 1 else file_path.stem,
                    language=language,
                    content=chunk_text,
                    indexed_at=timestamp,
                )
            )
            
        return chunks
