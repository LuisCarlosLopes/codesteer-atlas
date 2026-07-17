import argparse
import json
import shutil
import tempfile
from pathlib import Path

from codesteer_atlas.indexer import index_workspace


def _write_workspace(workspace: Path, file_count: int, functions_per_file: int) -> None:
    src_dir = workspace / "src"
    docs_dir = workspace / "docs"
    src_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    for index in range(file_count):
        lines = ["import os", ""]
        for fn_index in range(functions_per_file):
            lines.extend(
                [
                    f"def fn_{index}_{fn_index}():",
                    f"    return {index + fn_index}",
                    "",
                ]
            )
        (src_dir / f"mod_{index}.py").write_text("\n".join(lines), encoding="utf-8")

    (docs_dir / "overview.md").write_text(
        "# Overview\n\nVeja [[decision-001]].\n", encoding="utf-8"
    )
    (docs_dir / "decision-001.md").write_text(
        "# Decision 001\n\nContexto do benchmark.\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local de indexação do Atlas")
    parser.add_argument("--files", type=int, default=200, help="Quantidade de arquivos Python")
    parser.add_argument(
        "--functions-per-file", type=int, default=8, help="Funções sintéticas por arquivo"
    )
    args = parser.parse_args()

    temp_root = Path(tempfile.mkdtemp(prefix="atlas-bench-"))
    workspace = temp_root / "workspace"
    index_dir = temp_root / "index"
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        _write_workspace(workspace, args.files, args.functions_per_file)
        first = index_workspace(workspace, index_dir, report_progress=False)

        hot_file = workspace / "src" / "mod_0.py"
        hot_file.write_text(
            hot_file.read_text(encoding="utf-8") + "\n\ndef hot_path():\n    return 1\n",
            encoding="utf-8",
        )
        second = index_workspace(workspace, index_dir, report_progress=False)

        report = {
            "workspace": str(workspace),
            "index_dir": str(index_dir),
            "first_run": first.model_dump(),
            "second_run": second.model_dump(),
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
