"""Проверка структуры agent-wiki.

    python scripts/validate.py [--project NAME]

Проверяет:
- обязательные файлы проекта (index.md, overview.md);
- frontmatter у memory/ (name, description, type), decisions/, sessions/, specs/;
- имена файлов sessions/ и specs/ (YYYY-MM-DD-slug.md), decisions/ (NNNN-slug.md);
- размер index.md (config.json → index_max_lines);
- относительные markdown-ссылки ведут на существующие файлы;
- нет секретов и блоков <private>.
Код выхода 1, если есть ошибки (warning не влияет).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "приватный ключ"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub token"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "GitLab token"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"), "API key (OpenAI/Anthropic)"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[^\s'\"<>{}]{8,}"), "пароль/ключ в открытом виде"),
    (re.compile(r"<private>", re.I), "блок <private> не должен попадать в wiki"),
]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
DATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[\w.-]+\.md$")
ADR_RE = re.compile(r"^\d{4}-[\w.-]+\.md$")

REQUIRED_FM = {
    "memory": ("name", "description", "type"),
    "decisions": ("status", "date"),
    "sessions": ("date", "summary"),
    "specs": ("status", "date"),
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, path: Path, msg: str) -> None:
        self.errors.append(f"ERROR {rel(path)}: {msg}")

    def warn(self, path: Path, msg: str) -> None:
        self.warnings.append(f"WARN  {rel(path)}: {msg}")


def rel(path: Path) -> str:
    try:
        return path.relative_to(common.WIKI_ROOT).as_posix()
    except ValueError:
        return str(path)


def check_links(path: Path, text: str, rep: Report) -> None:
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for target in LINK_RE.findall(re.sub(r"`[^`]*`", "", line)):
            if re.match(r"^[a-z]+:", target) or target.startswith("#") or "{{" in target:
                continue
            target = target.split("#", 1)[0]
            if target and not (path.parent / target).exists():
                rep.err(path, f"битая ссылка → {target}")


def check_secrets(path: Path, text: str, rep: Report) -> None:
    for pat, label in SECRET_PATTERNS:
        if pat.search(text):
            rep.err(path, f"возможная утечка: {label}")


def check_project(pdir: Path, rep: Report, max_lines: int) -> None:
    is_template = pdir.name == "_template"
    for req in ("index.md", "overview.md"):
        if not (pdir / req).is_file():
            rep.err(pdir, f"нет {req}")
    index = pdir / "index.md"
    if index.is_file():
        n = len(index.read_text(encoding="utf-8").splitlines())
        if n > max_lines:
            rep.warn(index, f"{n} строк > {max_lines}: вынеси детали в отдельные файлы")
    if is_template:
        return
    for sub, fields in REQUIRED_FM.items():
        for f in (pdir / sub).glob("*.md"):
            if f.name == "README.md":
                continue
            fm = common.read_frontmatter(f)
            missing = [k for k in fields if not fm.get(k)]
            if missing:
                rep.err(f, f"во frontmatter нет: {', '.join(missing)}")
            if sub in ("sessions", "specs") and not DATED_RE.match(f.name):
                rep.err(f, "имя должно быть YYYY-MM-DD-slug.md")
            if sub == "decisions" and not ADR_RE.match(f.name):
                rep.err(f, "имя должно быть NNNN-slug.md")
            if sub == "memory" and fm.get("name") and fm["name"] != f.stem:
                rep.warn(f, f"name '{fm['name']}' не совпадает с именем файла")
    text = index.read_text(encoding="utf-8") if index.is_file() else ""
    for f in (pdir / "memory").glob("*.md"):
        if f.name != "README.md" and f"memory/{f.name}" not in text:
            rep.warn(f, "нет в index.md — запусти `wiki.py reindex`")


CONTEXT_STATUSES = ("requirement", "decision", "fact", "proposal")


def check_context(rep: Report) -> None:
    """context/: у каждого файла (кроме index.md) frontmatter status и description."""
    root = common.WIKI_ROOT / "context"
    if not root.is_dir():
        return
    for f in root.rglob("*.md"):
        if f.name in ("index.md", "README.md"):
            continue
        fm = common.read_frontmatter(f)
        if fm.get("status") not in CONTEXT_STATUSES:
            rep.err(f, f"status во frontmatter должен быть одним из {CONTEXT_STATUSES}")
        if not fm.get("description"):
            rep.err(f, "во frontmatter нет description")


def main(argv=None) -> int:
    common.setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    args = ap.parse_args(argv)
    rep = Report()
    max_lines = int(common.load_config().get("index_max_lines", 80))

    if args.project:
        projects = [common.project_dir(args.project)]
    else:
        projects = sorted(p for p in common.PROJECTS_DIR.iterdir() if p.is_dir())
    for pdir in projects:
        check_project(pdir, rep, max_lines)
    if not args.project:
        check_context(rep)

    roots = projects if args.project else [common.WIKI_ROOT]
    for root in roots:
        for f in root.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(common.WIKI_ROOT).parts):
                continue
            text = f.read_text(encoding="utf-8")
            check_links(f, text, rep)
            parts = f.relative_to(common.WIKI_ROOT).parts
            if parts[0] in ("projects", "context") and "_template" not in parts:
                check_secrets(f, text, rep)

    for line in rep.warnings + rep.errors:
        print(line)
    print(f"\nпроектов: {len(projects)}, ошибок: {len(rep.errors)}, предупреждений: {len(rep.warnings)}")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
