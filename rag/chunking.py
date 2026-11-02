"""Turn Python source and Markdown docs into retrievable chunks."""
import ast
from dataclasses import dataclass


@dataclass
class Chunk:
    id: str
    source: str  # file name
    kind: str  # "code" or "doc"
    title: str  # function name, or "document title / heading"
    text: str


def chunk_python(source_text: str, filename: str) -> list:
    """One chunk per top-level function or class (exact, using the AST)."""
    tree = ast.parse(source_text)
    lines = source_text.splitlines()
    chunks = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            text = "\n".join(lines[start - 1 : node.end_lineno])
            chunks.append(Chunk(id=f"{filename}#{node.name}", source=filename, kind="code", title=node.name, text=text))
    return chunks


def chunk_markdown(text: str, filename: str) -> list:
    """One chunk per '## ' section; the '# ' line is the document title."""
    doc_title = filename
    sections = []
    heading, body = "intro", []
    for line in text.splitlines():
        if line.startswith("# ") and doc_title == filename:
            doc_title = line[2:].strip()
            continue
        if line.startswith("## "):
            if any(part.strip() for part in body):
                sections.append((heading, body))
            heading, body = line[3:].strip(), []
            continue
        body.append(line)
    if any(part.strip() for part in body):
        sections.append((heading, body))
    chunks = []
    for heading, body in sections:
        title = f"{doc_title} / {heading}"
        chunks.append(Chunk(id=f"{filename}#{heading}", source=filename, kind="doc", title=title,
                            text=title + "\n" + "\n".join(body).strip()))
    return chunks