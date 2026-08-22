from unittest.mock import patch

import pytest

from codesteer_atlas.chunker import _CHUNK_MAX_CHARS, ASTChunker
from codesteer_atlas.config import MAX_CALLS_PER_CHUNK


def test_chunk_python_file_with_classes_and_functions(tmp_path):
    """
    Testa se o ASTChunker consegue extrair classes, funções e métodos
    de um arquivo Python e atribuir o escopo correto.
    """
    code_content = """
class Calculator:
    def add(self, a, b):
        return a + b

def global_function():
    return "hello"
"""
    # Cria o arquivo temporário
    test_file = tmp_path / "math_utils.py"
    test_file.write_text(code_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Esperamos 3 chunks: a classe Calculator, o método add e a função global_function
    assert len(chunks) == 3

    # Valida a classe
    class_chunk = next(c for c in chunks if c.scope_type == "class")
    assert class_chunk.scope_name == "Calculator"
    assert "class Calculator" in class_chunk.content

    # Valida o método da classe (com escopo acumulado)
    method_chunk = next(c for c in chunks if c.scope_type == "method")
    assert method_chunk.scope_name == "Calculator.add"
    assert "def add" in method_chunk.content

    # Valida a função global
    func_chunk = next(c for c in chunks if c.scope_type == "function")
    assert func_chunk.scope_name == "global_function"
    assert "def global_function" in func_chunk.content


def test_chunk_python_file_fallback_to_module(tmp_path):
    """
    Testa se o ASTChunker ativa o fallback para nível de módulo ('module')
    quando o arquivo de código Python não possui classes ou funções nomeadas.
    """
    code_content = """# Script sequencial de atribuições comuns
x = 10
y = 20
print(x + y)
"""
    test_file = tmp_path / "script.py"
    test_file.write_text(code_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Esperamos apenas 1 chunk representando o módulo (arquivo) inteiro
    assert len(chunks) == 1
    assert chunks[0].scope_type == "module"
    assert chunks[0].scope_name == "script"
    assert chunks[0].start_line == 1
    assert "print(x + y)" in chunks[0].content


def test_chunk_file_truncation(tmp_path):
    """
    Testa se arquivos/símbolos que excedem o limite são truncados mantendo
    a integridade das primeiras e últimas linhas.
    """
    # Cria um arquivo longo com mais de 1000 caracteres
    lines = [f"line_number_{i} = {i}" for i in range(150)]
    code_content = "def long_function():\n    " + "\n    ".join(lines) + "\n    return True\n"

    test_file = tmp_path / "large_file.py"
    test_file.write_text(code_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    assert len(chunks) == 1
    # Verifica que o conteúdo foi truncado adicionando o comentário
    assert "# ... [conteúdo truncado para respeitar limites do modelo] ..." in chunks[0].content
    # Verifica se a primeira linha (cabeçalho) e a última (retorno) foram preservadas
    assert "def long_function():" in chunks[0].content
    assert "return True" in chunks[0].content


def test_chunk_python_long_function_keeps_rationale_refs_before_truncation(tmp_path):
    lines = [f"    value_{i} = {i}" for i in range(80)]
    lines.insert(40, "    # WHY: manter cache local para evitar nova busca")
    code_content = "def long_function():\n" + "\n".join(lines) + "\n    return True\n"

    test_file = tmp_path / "large_file.py"
    test_file.write_text(code_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    assert len(chunks) == 1
    assert "# ... [conteúdo truncado para respeitar limites do modelo] ..." in chunks[0].content
    assert chunks[0].references == ["why:manter cache local para evitar nova busca"]


def test_chunk_without_rationale_has_empty_references(tmp_path):
    test_file = tmp_path / "plain.py"
    test_file.write_text("def run():\n    return 1\n", encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    assert chunks[0].references == []


def test_extract_imports_returns_python_and_typescript_targets(tmp_path):
    py_file = tmp_path / "service.py"
    py_file.write_text(
        "import pkg.module\nfrom .local import helper\nfrom pkg.sub import thing\n",
        encoding="utf-8",
    )
    ts_file = tmp_path / "app.ts"
    ts_file.write_text(
        'import x from "./lib";\nimport { y } from "../shared";\nimport "react";\n',
        encoding="utf-8",
    )

    chunker = ASTChunker()

    assert chunker.extract_imports(py_file) == ["pkg.module", ".local", "pkg.sub"]
    assert chunker.extract_imports(ts_file) == ["./lib", "../shared", "react"]


def test_chunk_markdown_file_with_headings(tmp_path):
    """
    Testa se o ASTChunker divide corretamente um arquivo Markdown
    com base em seus cabeçalhos (#) em seções semânticas.
    """
    markdown_content = """# Main Title
Introductory text paragraph 1.
Introductory text paragraph 2.

## Section 1
Content of section 1.
Some more details here.

### Subsection 1.1
Detailing subsection 1.1.
"""
    test_file = tmp_path / "doc.md"
    test_file.write_text(markdown_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Esperamos 3 chunks correspondentes a:
    # 1. Main Title
    # 2. Section 1
    # 3. Subsection 1.1
    assert len(chunks) == 3

    # Valida o primeiro cabeçalho
    assert chunks[0].scope_type == "section"
    assert chunks[0].scope_name == "Main Title"
    assert "Introductory text paragraph 1." in chunks[0].content

    # Valida o segundo cabeçalho
    assert chunks[1].scope_type == "section"
    assert chunks[1].scope_name == "Section 1"
    assert "Content of section 1." in chunks[1].content

    # Valida a subseção
    assert chunks[2].scope_type == "section"
    assert chunks[2].scope_name == "Subsection 1.1"
    assert "Detailing subsection 1.1." in chunks[2].content


def test_chunk_plain_text_file(tmp_path):
    """
    Testa se o ASTChunker divide corretamente um arquivo de texto simples (.txt)
    baseado em parágrafos agrupados.
    """
    # Cria parágrafos longos para forçar a divisão
    para1 = "Paragraph 1 is a relatively short paragraph."
    para2 = "Paragraph 2 is very long. " + ("A" * 600)  # ~620 chars
    para3 = "Paragraph 3 is also very long. " + ("B" * 600)  # ~625 chars
    
    text_content = f"{para1}\n\n{para2}\n\n{para3}"
    test_file = tmp_path / "notes.txt"
    test_file.write_text(text_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Esperamos 2 chunks:
    # Chunk 1: contendo para1 e para2 (juntos somam ~670 chars, menor que 1000)
    # Chunk 2: contendo para3 (para3 + para2 passaria de 1000, então quebra)
    assert len(chunks) == 2

    assert chunks[0].scope_type == "chunk"
    assert chunks[0].scope_name == "notes_chunk_1"
    assert para1 in chunks[0].content
    assert para2 in chunks[0].content

    assert chunks[1].scope_type == "chunk"
    assert chunks[1].scope_name == "notes_chunk_2"
    assert para3 in chunks[1].content


def test_chunk_csharp_file(tmp_path):
    """
    Testa se o ASTChunker consegue extrair classes, interfaces e métodos
    de um arquivo C#.
    """
    csharp_content = """
    namespace MyCompany.Models
    {
        public interface IRepository<T>
        {
            T GetById(int id);
        }

        public class UserRepository : IRepository<User>
        {
            public User GetById(int id)
            {
                return new User();
            }
        }
    }
    """
    test_file = tmp_path / "UserRepository.cs"
    test_file.write_text(csharp_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Esperamos encontrar:
    # 1. A interface IRepository
    # 2. O método GetById na interface
    # 3. A classe UserRepository
    # 4. O método GetById na classe
    assert len(chunks) >= 4

    # Verifica interface
    interface_chunk = next(c for c in chunks if c.scope_name == "IRepository" or c.scope_name.endswith("IRepository"))
    assert interface_chunk.scope_type == "class"
    assert "interface IRepository" in interface_chunk.content

    # Verifica classe
    class_chunk = next(c for c in chunks if c.scope_name == "UserRepository" or c.scope_name.endswith("UserRepository"))
    assert class_chunk.scope_type == "class"
    assert "class UserRepository" in class_chunk.content


def test_chunk_java_file(tmp_path):
    """
    Testa se o ASTChunker consegue extrair classes, enums e métodos
    de um arquivo Java.
    """
    java_content = """
    package com.mycompany.app;

    public enum Status {
        ACTIVE, INACTIVE
    }

    public class AppService {
        private String name;

        public AppService(String name) {
            this.name = name;
        }

        public void process() {
            System.out.println("Processing...");
        }
    }
    """
    test_file = tmp_path / "AppService.java"
    test_file.write_text(java_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Esperamos encontrar:
    # 1. O enum Status
    # 2. A classe AppService
    # 3. O construtor AppService
    # 4. O método process
    assert len(chunks) >= 4

    # Verifica enum (mapeado para classe)
    enum_chunk = next(c for c in chunks if c.scope_name == "Status")
    assert enum_chunk.scope_type == "class"

    # Verifica classe AppService
    class_chunk = next(c for c in chunks if c.scope_name == "AppService")
    assert class_chunk.scope_type == "class"

    # Verifica construtor e método
    method_chunk = next(c for c in chunks if "process" in c.scope_name)
    assert method_chunk.scope_type == "method"


def test_chunk_react_jsx_file(tmp_path):
    """
    Testa se o ASTChunker consegue extrair classes e funções
    de um arquivo React JSX utilizando o parser de javascript.
    """
    jsx_content = """
    import React from 'react';

    function WelcomeButton(props) {
        return <button>Welcome, {props.name}</button>;
    }

    class WelcomeMessage extends React.Component {
        render() {
            return <h1>Hello Component</h1>;
        }
    }
    """
    test_file = tmp_path / "Welcome.jsx"
    test_file.write_text(jsx_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Esperamos encontrar a função WelcomeButton e a classe WelcomeMessage
    assert len(chunks) >= 2
    
    func_chunk = next(c for c in chunks if c.scope_name == "WelcomeButton")
    assert func_chunk.scope_type == "function"
    assert "WelcomeButton" in func_chunk.content

    class_chunk = next(c for c in chunks if c.scope_name == "WelcomeMessage")
    assert class_chunk.scope_type == "class"


def test_chunk_flutter_dart_file(tmp_path):
    """
    Testa se o ASTChunker divide um arquivo Dart/Flutter
    em parágrafos através da lógica textual secundária.
    """
    dart_content = """
    import 'package:flutter/material.dart';

    void main() => runApp(MyApp());

    class MyApp extends StatelessWidget {
      @override
      Widget build(BuildContext context) {
        return MaterialApp(
          home: Text('Flutter App'),
        );
      }
    }
    """
    test_file = tmp_path / "main.dart"
    test_file.write_text(dart_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    # Como .dart é tratado como texto/parágrafo, esperamos chunking por blocos de parágrafos
    assert len(chunks) >= 1
    assert chunks[0].language == "dart"
    assert "class MyApp" in chunks[0].content


def test_chunk_xml_file(tmp_path):
    """
    Testa se arquivos XML são chunkados por parágrafo com a linguagem correta.
    """
    xml_content = """
    <config>
        <setting name="port">8080</setting>
        <setting name="host">localhost</setting>
    </config>

    <database>
        <adapter>postgresql</adapter>
        <database>lancedb</database>
    </database>
    """
    test_file = tmp_path / "config.xml"
    test_file.write_text(xml_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    assert len(chunks) >= 1
    assert chunks[0].language == "xml"
    assert any("postgresql" in c.content for c in chunks)


def test_chunk_sql_multiple_statements(tmp_path):
    """Extrai um chunk por statement DDL/DML com nomes semânticos."""
    sql_content = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    email VARCHAR(255)
);

CREATE VIEW active_users AS
SELECT id, email FROM users WHERE active = true;

SELECT id, email FROM users WHERE active = true;
"""
    test_file = tmp_path / "schema.sql"
    test_file.write_text(sql_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    assert len(chunks) == 3
    assert all(c.language == "sql" for c in chunks)

    table_chunk = next(c for c in chunks if c.scope_type == "table")
    assert table_chunk.scope_name == "users"
    assert "CREATE TABLE users" in table_chunk.content

    view_chunk = next(c for c in chunks if c.scope_type == "view")
    assert view_chunk.scope_name == "active_users"
    assert "CREATE VIEW active_users" in view_chunk.content

    query_chunk = next(c for c in chunks if c.scope_type == "query")
    assert query_chunk.scope_name == "select_users"
    assert "SELECT id, email" in query_chunk.content


def test_chunk_sql_large_statement_splits_by_lines(tmp_path):
    """Statements SQL grandes são particionados por linhas (~1000 chars)."""
    body_lines = [f"    col_{i} INT," for i in range(120)]
    sql_content = "CREATE TABLE wide_table (\n" + "\n".join(body_lines) + "\n    id INT\n);"

    test_file = tmp_path / "wide.sql"
    test_file.write_text(sql_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    assert len(chunks) > 1
    assert all(c.scope_type == "table" for c in chunks)
    assert all(c.scope_name.startswith("wide_table") for c in chunks)
    assert all(len(c.content) <= _CHUNK_MAX_CHARS + 100 for c in chunks)


def test_chunk_sql_fallback_to_text_when_unparseable(tmp_path):
    """Conteúdo sem statements reconhecíveis cai no chunking textual."""
    sql_content = "this is not valid sql at all !!! ###"
    test_file = tmp_path / "broken.sql"
    test_file.write_text(sql_content, encoding="utf-8")

    chunker = ASTChunker()
    chunks = chunker.chunk_file(test_file, repo_name="test-repo")

    assert len(chunks) >= 1
    assert all(c.language == "sql" for c in chunks)
    assert all(c.scope_type == "chunk" for c in chunks)


def test_verify_parser_api_aceita_ambiente_correto():
    """No ambiente suportado a verificação passa e é feita uma única vez."""
    from codesteer_atlas.chunker import ASTChunker, _CompatParser

    chunker = ASTChunker()
    assert chunker._api_verified is False
    parser = chunker._get_parser("python")
    assert isinstance(parser, _CompatParser)
    assert chunker._api_verified is True
    assert chunker._classic_api is False


def test_verify_parser_api_aceita_api_classica_bytes():
    """
    language-pack ≥1.13 devolve `tree_sitter.Parser` clássico via uvx; o chunker
    precisa adaptar, não abortar — era o bug de instalação reportado no Windows.
    """
    from codesteer_atlas.chunker import ASTChunker, _CompatNode, _CompatParser

    class _Point:
        """Simula tree_sitter.Point clássico (indexável; .row também existe)."""

        def __init__(self, row, column):
            self.row = row
            self.column = column

        def __getitem__(self, index):
            return (self.row, self.column)[index]

    class _ClassicNode:
        def __init__(self, node_type="module", children=None, start=(0, 0), end=(0, 1)):
            self.type = node_type
            self.children = children or []
            self.child_count = len(self.children)
            self.start_byte = 0
            self.end_byte = 1
            self.start_point = _Point(*start)
            self.end_point = _Point(*end)

        def child(self, index):
            return self.children[index]

    class _ClassicTree:
        def __init__(self, root):
            self.root_node = root

    class _ClassicParser:
        def parse(self, source):
            if isinstance(source, str):
                raise TypeError("source must be a bytestring or a callable, not str")
            ident = _ClassicNode("identifier", start=(0, 4), end=(0, 7))
            ident.start_byte = 4
            ident.end_byte = 7
            func = _ClassicNode(
                "function_definition",
                children=[ident],
                start=(0, 0),
                end=(1, 4),
            )
            func.start_byte = 0
            func.end_byte = len(source)
            root = _ClassicNode("module", children=[func], start=(0, 0), end=(1, 4))
            root.end_byte = len(source)
            return _ClassicTree(root)

    chunker = ASTChunker()
    assert chunker._verify_parser_api(_ClassicParser()) is True
    assert chunker._classic_api is True

    wrapped = _CompatParser(_ClassicParser(), classic=True)
    tree = wrapped.parse("def foo():\n    pass\n")
    root = tree.root_node()
    assert isinstance(root, _CompatNode)
    assert root.kind() == "module"
    assert root.child_count() == 1
    assert root.child(0).kind() == "function_definition"
    assert root.child(0).start_position().row == 0


def test_verify_parser_api_levanta_erro_acionavel_em_api_incompativel():
    """
    Parser que não fala nem nativo nem clássico precisa falhar alto, com uma
    mensagem acionável — nunca virar falha silenciosa por arquivo.
    """
    from codesteer_atlas.chunker import ASTChunker, IncompatibleParserError

    class _ParserQuebrado:
        def parse(self, source):
            raise TypeError("source must be a bytestring or a callable, not str")

    chunker = ASTChunker()
    with pytest.raises(IncompatibleParserError) as exc:
        chunker._verify_parser_api(_ParserQuebrado())

    mensagem = str(exc.value)
    assert "tree-sitter-language-pack" in mensagem
    assert "uv cache clean" in mensagem


def test_chunk_file_propaga_erro_de_api_incompativel(tmp_path):
    """
    O erro de ambiente não pode ser confundido com "arquivo problemático": ele sobe
    por `chunk_file` para o indexador poder abortar a execução inteira.
    """
    from codesteer_atlas.chunker import ASTChunker, IncompatibleParserError

    source = tmp_path / "app.py"
    source.write_text("def f():\n    pass\n", encoding="utf-8")

    chunker = ASTChunker()
    with patch.object(
        ASTChunker, "_verify_parser_api", side_effect=IncompatibleParserError("boom")
    ), pytest.raises(IncompatibleParserError):
        chunker.chunk_file(source, "repo")


def test_chunk_file_com_parser_classico_real(tmp_path):
    """
    Integração: language-pack ≥1.13 (API clássica) deve chunkar Python de ponta a ponta.
    Pulado quando o ambiente local ainda está no binding nativo (uv.lock = 1.8.x).
    """
    from importlib.metadata import version

    from tree_sitter_language_pack import get_parser

    from codesteer_atlas.chunker import ASTChunker

    raw = get_parser("python")
    try:
        raw.parse("x = 1\n")
        pytest.skip("ambiente com API nativa; regressão clássica coberta pelo mock")
    except TypeError:
        # Esperado na API clássica: parse(str) levanta TypeError; seguimos com o teste.
        pass

    source = tmp_path / "app.py"
    source.write_text(
        "class A:\n    def m(self):\n        pass\n\ndef f():\n    return 1\n",
        encoding="utf-8",
    )
    chunks = ASTChunker().chunk_file(source, "repo")
    names = {c.scope_name for c in chunks}
    assert "A" in names
    assert "A.m" in names
    assert "f" in names
    assert version("tree-sitter-language-pack")  # só para deixar a dep explícita no teste


# ---------------------------------------------------------------------------
# Extração de chamadas da AST (RF11-RF12 / R-CALL-01..04)
# ---------------------------------------------------------------------------


def _calls_by_scope(tmp_path, filename, source):
    chunker = ASTChunker()
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    return {chunk.scope_name: chunk.calls for chunk in chunker.chunk_file(path, "repo")}


def test_ca18_classe_nao_herda_as_chamadas_dos_proprios_metodos(tmp_path):
    """
    CA18 — RF12: a chamada pertence ao símbolo mais interno que a contém. Se a
    poda nas fronteiras de símbolo aninhado sumir, a classe passa a acumular as
    chamadas de todos os métodos e vira um hub artificial.
    """
    source = (
        "class Engine:\n"
        "    LIMITE = compute_limit()\n"
        "\n"
        "    def start(self):\n"
        "        return spin_up()\n"
        "\n"
        "    def stop(self):\n"
        "        return wind_down()\n"
    )
    calls = _calls_by_scope(tmp_path, "engine.py", source)

    assert calls["Engine.start"] == ["spin_up"]
    assert calls["Engine.stop"] == ["wind_down"]
    # a classe fica só com o que está no corpo dela, fora dos métodos
    assert calls["Engine"] == ["compute_limit"]


def test_ca19_ruido_e_descartado_na_extracao(tmp_path):
    """CA19 — builtins e métodos de coleção não chegam ao índice. [R-CALL-04]"""
    source = (
        "def run(items):\n"
        "    print(len(items))\n"
        "    items.append(1)\n"
        "    items.sort()\n"
        "    return processar(items)\n"
    )
    calls = _calls_by_scope(tmp_path, "run.py", source)

    assert calls["run"] == ["processar"]


def test_ca19_ruido_compara_por_casefold(tmp_path):
    """`ToString` e `tostring` são a mesma entrada da lista."""
    source = "class A {\n    void go() { var x = ToString(); helper(); }\n}\n"
    calls = _calls_by_scope(tmp_path, "A.java", source)

    assert "ToString" not in calls["A.go"]
    assert "helper" in calls["A.go"]


def test_ca20_teto_de_chamadas_por_chunk(tmp_path):
    """CA20 — L01 limita a lista; sem teto, um .js minificado sozinho estoura o grafo."""
    chamadas = "\n".join(f"    dominio_{i:03d}()" for i in range(80))
    calls = _calls_by_scope(tmp_path, "wide.py", f"def run():\n{chamadas}\n")

    assert len(calls["run"]) == MAX_CALLS_PER_CHUNK
    # corta o excedente, mantendo as primeiras ocorrências
    assert calls["run"][0] == "dominio_000"
    assert calls["run"][-1] == f"dominio_{MAX_CALLS_PER_CHUNK - 1:03d}"


def test_ca21_ordem_e_de_primeira_ocorrencia(tmp_path):
    """CA21 — ordem estável e legível: a do arquivo, não a da varredura da AST."""
    source = (
        "def run():\n"
        "    zebra()\n"
        "    alfa()\n"
        "    meio()\n"
    )
    calls = _calls_by_scope(tmp_path, "ordem.py", source)

    assert calls["run"] == ["zebra", "alfa", "meio"]


def test_ca21_extracao_e_deterministica(tmp_path):
    """Duas extrações do mesmo arquivo devem ser idênticas."""
    source = "def run():\n    a_fn()\n    b_fn()\n    a_fn()\n"
    primeira = _calls_by_scope(tmp_path, "det.py", source)
    segunda = _calls_by_scope(tmp_path, "det.py", source)

    assert primeira == segunda


def test_ca22_chamada_repetida_aparece_uma_vez(tmp_path):
    """CA22 — dedup dentro do chunk; a aresta é a mesma qualquer que seja a contagem."""
    source = "def run():\n    processar()\n    processar()\n    processar()\n"
    calls = _calls_by_scope(tmp_path, "dedup.py", source)

    assert calls["run"] == ["processar"]


def test_receptor_e_descartado_fica_so_o_ultimo_identificador(tmp_path):
    """R-CALL-01 — `self.storage.get_manifest()` vira `get_manifest`."""
    source = "def run(self):\n    return self.storage.get_manifest()\n"
    calls = _calls_by_scope(tmp_path, "receptor.py", source)

    assert calls["run"] == ["get_manifest"]


def test_extracao_nao_le_o_content_truncado(tmp_path):
    """
    RE08 — o `content` persistido passa por `_truncate_content`. Uma chamada no
    miolo de um símbolo grande tem de sobreviver assim mesmo.
    """
    # A chamada fica no MIOLO: `_truncate_content` preserva as 7 primeiras linhas
    # e as 3 últimas, então só o miolo prova que a leitura vem da AST.
    antes = "\n".join(f"    x_{i} = {i}" for i in range(200))
    depois = "\n".join(f"    y_{i} = {i}" for i in range(200))
    source = f"def enorme():\n{antes}\n    alvo_no_miolo()\n{depois}\n    return 0\n"
    chunker = ASTChunker()
    path = tmp_path / "enorme.py"
    path.write_text(source, encoding="utf-8")

    chunk = next(c for c in chunker.chunk_file(path, "repo") if c.scope_name == "enorme")

    assert len(chunk.content) <= _CHUNK_MAX_CHARS
    assert "alvo_no_miolo" not in chunk.content
    assert "alvo_no_miolo" in chunk.calls


def test_rf18_linguagem_sem_ramo_ast_nao_produz_calls(tmp_path):
    """RF18 — sem símbolo AST não há chamador; a lista tem de sair vazia."""
    chunker = ASTChunker()
    path = tmp_path / "notas.md"
    path.write_text("# Titulo\n\nchamar_alguem() no texto\n", encoding="utf-8")

    assert all(chunk.calls == [] for chunk in chunker.chunk_file(path, "repo"))


def test_h4_chunk_module_de_script_participa_como_chamador(tmp_path):
    """H4 — script sequencial não tem símbolo; o chunk `module` carrega as chamadas."""
    chunker = ASTChunker()
    path = tmp_path / "script.py"
    path.write_text("configurar()\nexecutar()\n", encoding="utf-8")

    chunks = chunker.chunk_file(path, "repo")

    assert len(chunks) == 1
    assert chunks[0].scope_type == "module"
    assert chunks[0].calls == ["configurar", "executar"]


def test_ca13_extracao_cobre_as_seis_linguagens_com_simbolo_ast(tmp_path):
    """
    CA13 — cada linguagem tem seu próprio nó de chamada na AST (`call`,
    `call_expression`, `invocation_expression`, `method_invocation`). Um `kind`
    errado em `_CALL_NODE_KINDS` produz silenciosamente zero chamadas para
    aquela linguagem inteira.
    """
    casos = {
        "a.py": ("def run():\n    return alvo_py()\n", "run", "alvo_py"),
        "a.js": ("function run() {\n  return alvoJs();\n}\n", "run", "alvoJs"),
        "a.ts": ("function run(): number {\n  return alvoTs();\n}\n", "run", "alvoTs"),
        "a.go": ("package main\n\nfunc Run() int {\n\treturn alvoGo()\n}\n", "Run", "alvoGo"),
        "A.cs": (
            "class A {\n    void Run() { var x = AlvoCs(); }\n}\n",
            "A.Run",
            "AlvoCs",
        ),
        "A.java": (
            "class A {\n    void run() { int x = alvoJava(); }\n}\n",
            "A.run",
            "alvoJava",
        ),
    }
    chunker = ASTChunker()

    for filename, (source, scope, esperado) in casos.items():
        path = tmp_path / filename
        path.write_text(source, encoding="utf-8")
        calls = {chunk.scope_name: chunk.calls for chunk in chunker.chunk_file(path, "repo")}
        assert esperado in calls.get(scope, []), f"{filename}: {calls}"
