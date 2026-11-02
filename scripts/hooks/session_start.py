"""SessionStart hook для Claude Code и Codex.

Печатает в stdout компактный контекст (оба агента добавляют plain-text stdout
в контекст сессии): индекс проекта, индекс стандартов, грабли, последние сессии.
Детали агент открывает сам по ссылкам — progressive disclosure.

Перед этим подтягивает свежую wiki из origin (только fast-forward при чистом
дереве, см. common.update_wiki). Также запоминает время старта сессии для stop.py.
"""
from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402


def read_stdin() -> dict:
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return {}


def state_path(session_id: str) -> Path:
    d = Path(tempfile.gettempdir()) / "agent-wiki"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((str(common.WIKI_ROOT) + "\0" + session_id).encode("utf-8")).hexdigest()
    return d / f"{key}.json"


def section(title: str, path: Path, rel: str, max_lines: int | None = None) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… (обрезано, полный файл: {rel})"]
    return f"### {title} — `{rel}`\n\n" + "\n".join(lines).strip() + "\n"


def recent_sessions(pdir: Path, wiki_rel: str, project: str, n: int) -> str:
    sdir = pdir / "sessions"
    files = sorted((p for p in sdir.glob("*.md") if p.name != "README.md"), reverse=True)[:n]
    if not files:
        return ""
    out = ["### Последние сессии"]
    for f in files:
        fm = common.read_frontmatter(f)
        summary = fm.get("summary") or fm.get("description") or ""
        out.append(f"- `{wiki_rel}/projects/{project}/sessions/{f.name}` — {summary}".rstrip(" —"))
    return "\n".join(out) + "\n"


def main() -> int:
    common.setup_utf8()
    payload = read_stdin()
    cwd = payload.get("cwd")
    cfg = common.load_config()
    ss = cfg.get("session_start", {})

    consumer = common.find_consumer_root(cwd)
    ccfg = common.load_consumer_config(consumer)
    project = common.resolve_project(cwd=cwd)
    wiki_rel = ccfg.get("wiki_path") or cfg.get("submodule_path", ".agent-wiki")

    session_id = payload.get("session_id")
    if session_id:
        try:
            state = state_path(session_id)
            if not state.exists():
                state.write_text(
                    json.dumps({"start": time.time(), "project": project, "reminded": False}),
                    encoding="utf-8",
                )
        except OSError:
            pass

    parts = ["## Agent Wiki — контекст проекта", ""]
    # после сжатия контекста сессия та же — не тратим секунды на сеть
    if ss.get("auto_update", False) and payload.get("source") != "compact":
        try:
            note = common.update_wiki(float(ss.get("update_timeout", 10)))
        except Exception as exc:  # обновление необязательно, контекст важнее
            note = f"Проверка обновлений wiki упала: {exc}"
        if note:
            parts += [note, ""]
    if not project:
        parts.append("Проект не определён: запусти `python " + wiki_rel + "/scripts/install.py`.")
        print("\n".join(parts))
        return 0

    pdir = common.project_dir(project)
    if not pdir.is_dir():
        parts.append(
            f"В wiki нет проекта `{project}`. Создай его: "
            f"`python {wiki_rel}/scripts/wiki.py init {project}`, затем выполни skill `wiki-init`."
        )
        print("\n".join(parts))
        return 0

    parts.append(
        f"Проект: `{project}`. Wiki: `{wiki_rel}/projects/{project}/`. "
        f"Ниже только индексы — нужные файлы открывай сам. "
        f"Поиск: `python {wiki_rel}/scripts/wiki.py recall \"<запрос>\"`. "
        f"Правила записи памяти — в AGENTS.md (блок agent-wiki)."
    )
    index_text = (pdir / "index.md").read_text(encoding="utf-8") if (pdir / "index.md").is_file() else ""
    if "Заполни через skill `wiki-init`" in index_text:
        parts.append("")
        parts.append(
            "**Проект ещё не описан в wiki.** Для первой настройки выполни skill `wiki-init` "
            "(изучить репозиторий и заполнить overview/architecture/conventions)."
        )
    parts.append("")
    max_lines = int(cfg.get("index_max_lines", 80))
    parts.append(section("Индекс проекта", pdir / "index.md", f"{wiki_rel}/projects/{project}/index.md", max_lines))
    if ss.get("include_gotchas", True):
        parts.append(section("Грабли", pdir / "gotchas.md", f"{wiki_rel}/projects/{project}/gotchas.md", 40))
    parts.append(recent_sessions(pdir, wiki_rel, project, int(ss.get("recent_sessions", 3))))
    if ss.get("include_context_index", True):
        parts.append(section("Контекст продукта", common.WIKI_ROOT / "context" / "index.md", f"{wiki_rel}/context/index.md", 40))
    if ss.get("include_standards_index", True):
        parts.append(section("Стандарты", common.WIKI_ROOT / "standards" / "index.md", f"{wiki_rel}/standards/index.md", 40))

    print("\n".join(p for p in parts if p is not None).strip())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # хук не должен ломать сессию
        print(f"[agent-wiki] session_start error: {exc}", file=sys.stderr)
        sys.exit(0)
