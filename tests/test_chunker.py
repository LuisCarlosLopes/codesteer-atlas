from unittest.mock import patch

import pytest

from codesteer_atlas.chunker import _CHUNK_MAX_CHARS, ASTChunker


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


# --- §3.3: imports multi-linguagem e declaração de namespace -------------------

_IMPORT_FIXTURES = {
    "svc.go": (
        'package main\n\nimport "fmt"\nimport (\n  "os"\n'
        '  svc "github.com/x/y/internal/svc"\n)\n',
        ["fmt", "os", "github.com/x/y/internal/svc"],
    ),
    "App.java": (
        "package com.example.app;\nimport com.example.core.Service;\n"
        "import static java.lang.Math.max;\nimport a.c.*;\n",
        ["com.example.core.Service", "java.lang.Math.max", "a.c"],
    ),
    "Home.cs": (
        "namespace MinhaApp.Web;\nusing System.Text;\nusing MinhaApp.Servicos;\n"
        "using Alias = MinhaApp.Outro;\nglobal using System;\n",
        ["System.Text", "MinhaApp.Servicos", "MinhaApp.Outro", "System"],
    ),
    "lib.rs": (
        "use crate::bar::Baz;\nuse std::collections::HashMap;\npub use self::thing::Other;\n",
        ["crate::bar::Baz", "std::collections::HashMap", "self::thing::Other"],
    ),
    "Main.kt": (
        "package com.example.app\nimport com.example.core.Service\n"
        "import java.util.List as JList\nimport c.d.*\n",
        ["com.example.core.Service", "java.util.List", "c.d"],
    ),
    "Kernel.php": (
        '<?php\nnamespace App\\Http;\nuse App\\Models\\User;\nrequire_once "bootstrap.php";\n',
        ["App\\Models\\User", "bootstrap.php"],
    ),
    "thing.rb": (
        'require "json"\nrequire_relative "lib/thing"\nputs "nao e import"\n',
        ["json", "lib/thing"],
    ),
    "App.swift": ("import Foundation\nimport MyModule\n", ["Foundation", "MyModule"]),
    "App.scala": (
        "package com.example.app\nimport com.example.core.Service\n"
        "import scala.collection.mutable._\n",
        ["com.example.core.Service", "scala.collection.mutable"],
    ),
    "web.ex": (
        "defmodule MyApp.Web do\n  import MyApp.Core\n  alias MyApp.Other\nend\n",
        ["MyApp.Core", "MyApp.Other"],
    ),
}


@pytest.mark.parametrize("file_name", sorted(_IMPORT_FIXTURES))
def test_extract_imports_cobre_as_linguagens_da_fase_3(tmp_path, file_name):
    """
    Antes da §3.3 todas estas linguagens retornavam `[]` — indexadas e buscáveis,
    mas mudas sobre como se conectam.
    """
    source, expected = _IMPORT_FIXTURES[file_name]
    target = tmp_path / file_name
    target.write_text(source, encoding="utf-8")

    assert ASTChunker().extract_imports(target) == expected


def test_extract_imports_de_linguagem_sem_entrada_na_tabela_retorna_vazio(tmp_path):
    source = tmp_path / "script.lua"
    source.write_text('local m = require("mod")\n', encoding="utf-8")

    assert ASTChunker().extract_imports(source) == []


@pytest.mark.parametrize(
    ("file_name", "source", "expected"),
    [
        ("App.java", "package com.example.app;\nclass App {}\n", "com.example.app"),
        ("Home.cs", "namespace MinhaApp.Web;\nclass Home {}\n", "MinhaApp.Web"),
        ("Block.cs", "namespace MinhaApp.Bloco\n{\n  class C {}\n}\n", "MinhaApp.Bloco"),
        ("Main.kt", "package com.example.app\nfun main() {}\n", "com.example.app"),
        ("App.scala", "package com.example.app\nobject App\n", "com.example.app"),
    ],
)
def test_extract_package_declaration_nas_quatro_linguagens(tmp_path, file_name, source, expected):
    target = tmp_path / file_name
    target.write_text(source, encoding="utf-8")

    assert ASTChunker().extract_package_declaration(target) == expected


@pytest.mark.parametrize(
    ("file_name", "source"),
    [
        ("mod.py", "import os\n"),
        ("main.go", 'package main\nimport "fmt"\n'),
        ("Solto.java", "class Solto {}\n"),
        ("inexistente.txt", ""),
    ],
)
def test_extract_package_declaration_retorna_none_fora_da_familia(tmp_path, file_name, source):
    target = tmp_path / file_name
    target.write_text(source, encoding="utf-8")

    assert ASTChunker().extract_package_declaration(target) is None


def test_extract_package_declaration_em_arquivo_ausente_nao_levanta(tmp_path):
    assert ASTChunker().extract_package_declaration(tmp_path / "sumiu.java") is None
