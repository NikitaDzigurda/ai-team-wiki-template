"""Stop hook для Claude Code и Codex.

Если за сессию менялся код репозитория, а память проекта в wiki — нет,
один раз за сессию просит агента сохранить итог (decision: block).
Формат ответа одинаковый для Claude Code и Codex.

Отключить: config.json → "stop_reminder": false, или env AGENT_WIKI_STOP=off.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from session_start import read_stdin, state_path  # noqa: E402

IGNORED_PREFIXES = (".claude/", ".codex/", ".agents/", "AGENTS.md", "CLAUDE.md", ".agent-wiki.json")


def changed_code_since(consumer: Path, wiki_rel: str, start: float) -> list[str]:
    out = common.git("status", "--porcelain", "--untracked-files=all", cwd=consumer)
    changed = []
    for line in out.splitlines():
        rel = line[3:].strip().strip('"')
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        rel_posix = rel.replace("\\", "/")
        if rel_posix.startswith(wiki_rel.rstrip("/")) or rel_posix.startswith(IGNORED_PREFIXES):
            continue
        p = consumer / rel
        try:
            if not p.exists() or p.stat().st_mtime >= start:
                changed.append(rel_posix)
        except OSError:
            continue
    return changed


def wiki_updated_since(pdir: Path, start: float) -> bool:
    if not pdir.is_dir():
        return False
    for p in pdir.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime >= start:
                return True
        except OSError:
            continue
    return False


def main() -> int:
    common.setup_utf8()
    if os.environ.get("AGENT_WIKI_STOP", "").lower() in ("0", "off", "false", "no"):
        return 0
    cfg = common.load_config()
    if not cfg.get("stop_reminder", True):
        return 0

    payload = read_stdin()
    if payload.get("stop_hook_active"):
        return 0
    session_id = payload.get("session_id")
    if not session_id:
        return 0

    sp = state_path(session_id)
    try:
        state = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # SessionStart не отработал (например, хук добавили посреди сессии) — начинаем отсчёт сейчас
        state = {"start": time.time(), "reminded": False}
        sp.write_text(json.dumps(state), encoding="utf-8")
        return 0
    if state.get("reminded"):
        return 0

    cwd = payload.get("cwd")
    consumer = common.find_consumer_root(cwd)
    if not consumer:
        return 0
    ccfg = common.load_consumer_config(consumer)
    wiki_rel = (ccfg.get("wiki_path") or cfg.get("submodule_path", ".agent-wiki")).replace("\\", "/")
    project = state.get("project") or common.resolve_project(cwd=cwd)
    if not project:
        return 0

    start = float(state.get("start", time.time()))
    changed = changed_code_since(consumer, wiki_rel, start)
    if not changed or wiki_updated_since(common.project_dir(project), start):
        return 0

    state["reminded"] = True
    sp.write_text(json.dumps(state), encoding="utf-8")

    sample = ", ".join(changed[:5]) + (" …" if len(changed) > 5 else "")
    reason = (
        f"[agent-wiki] В этой сессии изменены файлы ({sample}), а память проекта `{project}` "
        f"в `{wiki_rel}/projects/{project}/` не обновлялась. "
        "Если задача завершена — выполни skill `wiki-session-end` (итог сессии, новые факты, "
        "решения, грабли; затем reindex и sync). Если работа ещё идёт или сохранять нечего — "
        "ответь одной строкой «память: без изменений» и продолжай. Напоминание показывается один раз за сессию."
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[agent-wiki] stop error: {exc}", file=sys.stderr)
        sys.exit(0)
