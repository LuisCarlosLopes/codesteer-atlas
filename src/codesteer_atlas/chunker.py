import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import List, Tuple
from tree_sitter_language_pack import get_parser
from codesteer_atlas.config import SUPPORTED_EXTENSIONS
from codesteer_atlas.models import CodeChunk


class ASTChunker:
    """
    Responsável por parsear arquivos de código-fonte usando Tree-sitter e extrair
    símbolos sintáticos (classes, funções, métodos) como chunks de contexto coerentes.
    """

    def __init__(self):
        # Dicionário para armazenar parsers cacheificados por linguagem
        self.parsers = {}

    def _get_parser(self, language_name: str):
        """Retorna o parser correspondente para a linguagem ou cria um novo."""
        if language_name not in self.parsers:
            try:
                self.parsers[language_name] = get_parser(language_name)
            except Exception:
                # Se falhar em carregar a gramática, retorna None
                self.parsers[language_name] = None
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

    def _truncate_content(self, content: str, max_chars: int = 1000) -> str:
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
        chunks: List[Tuple[int, int, str, str, str]] = None,
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

        # API tree-sitter v0.22+: parse() aceita str diretamente
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
                )
            )

        # Se nenhum símbolo AST foi extraído (ex: script top-level sequencial),
        # gera um chunk 'module' representando o arquivo inteiro
        if not chunks and len(source_text) > 0:
            # Conta o número de linhas
            lines = source_text.splitlines()
            total_lines = len(lines)

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
                current_chunk_parts = []
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
        
        current_chunk_parts = []
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
