"""Assert every relative markdown link in the repo resolves — file and anchor — and
that every `docs/` path written in a source file names a file.

Root-relative paths are the recurring failure: `](docs/runbooks/redeploy.md)` is
correct in `DEPLOY_CHECKLIST.md` at the repo root and wrong in any file under
`docs/`, where it resolves against the containing directory. `/deploy-archive`
copies the checklist body two levels down, which is how such links get there.

Anchors are checked with a reimplementation of GitHub's heading slug: lowercase,
drop everything outside `[a-z0-9 _-]`, spaces to hyphens. It does not model the
`-1` suffix GitHub appends to a repeated heading, so a link that needs one will
report a false failure; add the disambiguation to the heading text instead.

Code in markdown is not scanned: a fenced block or an inline code span is
illustrating link syntax, not linking — this file and the `/deploy-archive` command
both spell out `](docs/...)` forms that must not be resolved. External (`http`,
`mailto:`) links are not fetched.

Source files (`_SOURCE_SUFFIXES`, and `Makefile`) name pages by repo-root path in
error messages, `--help` text and comments. Those paths are checked for the file
only.
"""

from __future__ import annotations

import os
import re
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_CODE_SPAN_RE = re.compile(r"(`+)(?:.|\n)*?\1")
_HEADING_RE = re.compile(r"^#{1,6} (.+)$")
_INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_DOC_PATH_RE = re.compile(r"(?<![\w./-])docs/[\w./-]+\.md\b")

_EXCLUDED_DIRS = frozenset({".git", ".venv", "node_modules", "target"})
_SOURCE_SUFFIXES = frozenset({".py", ".rs", ".sh", ".toml", ".yaml", ".yml"})


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: link text kept, punctuation dropped."""
    text = _INLINE_LINK_RE.sub(r"\1", heading)
    return re.sub(r"[^a-z0-9 _-]", "", text.lower()).strip().replace(" ", "-")


@cache
def _anchors(path: Path) -> frozenset[str]:
    out, fenced = set(), False
    for line in path.read_text(errors="replace").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if m := _HEADING_RE.match(line):
            out.add(_slug(m.group(1)))
    return frozenset(out)


def _prose(text: str) -> str:
    """The text with fenced blocks and inline code spans removed."""
    return _CODE_SPAN_RE.sub("", _FENCE_RE.sub("", text))


def _markdown_files() -> list[Path]:
    return sorted(
        p for p in REPO_ROOT.rglob("*.md") if not any(part in _EXCLUDED_DIRS for part in p.parts)
    )


def _source_files() -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        out.extend(
            Path(root) / name
            for name in files
            if name == "Makefile" or Path(name).suffix in _SOURCE_SUFFIXES
        )
    return sorted(out)


def test_relative_markdown_links_resolve() -> None:
    broken: list[str] = []
    for md in _markdown_files():
        rel = md.relative_to(REPO_ROOT)
        for m in _LINK_RE.finditer(_prose(md.read_text(errors="replace"))):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, anchor = target.partition("#")
            resolved = (md.parent / path) if path else md
            if not resolved.exists():
                broken.append(f"{rel}  ->  {target}   (no such file)")
            elif anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved):
                broken.append(f"{rel}  ->  {target}   (no such anchor)")

    assert not broken, "broken markdown links:\n  " + "\n  ".join(broken)


def test_doc_paths_in_source_exist() -> None:
    missing = [
        f"{src.relative_to(REPO_ROOT)}  ->  {m.group(0)}"
        for src in _source_files()
        for m in _DOC_PATH_RE.finditer(src.read_text(errors="replace"))
        if not (REPO_ROOT / m.group(0)).is_file()
    ]
    assert not missing, "docs paths in source that name no file:\n  " + "\n  ".join(missing)
