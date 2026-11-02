"""Подключение agent-wiki к репозиторию (идемпотентно, можно запускать повторно).

Из корня целевого репозитория:

    git submodule add -b main <WIKI_GIT_URL> .agent-wiki
    python .agent-wiki/scripts/install.py

Что делает:
- пишет .agent-wiki.json (имя проекта, путь к wiki, raw URL);
- создаёт projects/<project>/ в wiki из шаблона (если его нет);
- вставляет/обновляет блок agent-wiki в AGENTS.md и CLAUDE.md (между маркерами, остальной текст не трогает);
- добавляет хуки в .claude/settings.json и .codex/hooks.json (merge, чужие хуки сохраняются);
- копирует skills wiki-* в .claude/skills/ и .agents/skills/.

Флаги: --project NAME, --python CMD, --no-claude, --no-codex, --no-hooks, --dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from wiki import init_project, reindex  # noqa: E402

TPL = common.WIKI_ROOT / "templates"
HOOK_MARK = "scripts/hooks/"


class Writer:
    def __init__(self, root: Path, dry: bool) -> None:
        self.root, self.dry = root, dry

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old == content:
            print(f"  = {rel}")
            return
        print(f"  {'~' if old is not None else '+'} {rel}")
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            common.write_text(path, content)


def is_ours(group: dict) -> bool:
    cmds = [h.get("command", "") for h in group.get("hooks", [])]
    return any(HOOK_MARK in c and any(name in c for name in ("session_start.py", "stop.py"))
               and any(f"--agent {agent}" in c for agent in ("claude", "codex")) for c in cmds)


def merge_hooks(existing: dict, ours: dict) -> dict:
    """Заменяет наши matcher-группы (по пути scripts/hooks/), остальные оставляет."""
    result = dict(existing)
    hooks = dict(result.get("hooks", {}))
    for event, groups in ours.get("hooks", {}).items():
        kept = [g for g in hooks.get(event, []) if not is_ours(g)]
        hooks[event] = kept + groups
    result["hooks"] = hooks
    return result


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        sys.exit(f"ошибка: {path} — невалидный JSON, поправь вручную и запусти снова")


def main(argv=None) -> int:
    common.setup_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--python", default="python" if os.name == "nt" else "python3")
    ap.add_argument("--no-claude", action="store_true")
    ap.add_argument("--no-codex", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-hooks", action="store_true", help="установить инструкции и skills без хуков")
    args = ap.parse_args(argv)

    consumer = common.find_consumer_root()
    if not consumer:
        sys.exit("ошибка: запусти из корня репозитория, к которому подключаешь wiki")
    consumer = consumer.resolve()
    cfg = common.load_config()
    ccfg = common.load_consumer_config(consumer)

    project = common.slugify(args.project or ccfg.get("project") or common.guess_project_name(consumer))
    try:
        wiki_rel = Path(os.path.relpath(common.WIKI_ROOT, consumer)).as_posix()
    except ValueError:  # другой диск на Windows
        wiki_rel = common.WIKI_ROOT.as_posix()
    raw = cfg["raw_base_url"]
    values = {"PROJECT": project, "WIKI": wiki_rel, "PY": args.python, "RAW": raw, "DATE": common.today()}

    print(f"репозиторий: {consumer}\nпроект: {project}\nwiki: {wiki_rel}\n")
    w = Writer(consumer, args.dry_run)

    ccfg.update({"project": project, "wiki_path": wiki_rel, "raw_base_url": raw})
    w.write(common.CONSUMER_CONFIG, json.dumps(ccfg, ensure_ascii=False, indent=2) + "\n")

    pdir = common.project_dir(project)
    if not pdir.exists():
        print(f"  + wiki: projects/{project}/ (из шаблона)")
        if not args.dry_run:
            init_project(project)
            reindex(pdir)

    def block(name: str) -> str:
        return common.render((TPL / "consumer" / name).read_text(encoding="utf-8"), values)

    def merge_md(rel: str, tpl: str) -> None:
        path = consumer / rel
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
        except UnicodeDecodeError:
            sys.exit(f"ошибка: {rel} не в UTF-8 — перекодируй файл в UTF-8 и запусти снова")
        w.write(rel, common.merge_marked_block(existing, block(tpl)))

    merge_md("AGENTS.md", "AGENTS.block.md")
    if not args.no_claude:
        merge_md("CLAUDE.md", "CLAUDE.block.md")

    def merge_json(rel: str, tpl: str) -> None:
        ours = json.loads(block(tpl))
        merged = merge_hooks(load_json(consumer / rel), ours)
        w.write(rel, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")

    if not args.no_claude and not args.no_hooks:
        merge_json(".claude/settings.json", "claude-settings.json")
    if not args.no_codex and not args.no_hooks:
        merge_json(".codex/hooks.json", "codex-hooks.json")

    skill_targets = []
    if not args.no_claude:
        skill_targets.append(".claude/skills")
    if not args.no_codex:
        skill_targets.append(".agents/skills")
    for skill in sorted((TPL / "skills").iterdir()):
        if not skill.is_dir():
            continue
        for target in skill_targets:
            for f in skill.rglob("*"):
                if f.is_file():
                    rel = f"{target}/{skill.name}/{f.relative_to(skill).as_posix()}"
                    w.write(rel, common.render(f.read_text(encoding="utf-8"), values))

    print(
        "\nПредварительный просмотр завершён." if args.dry_run else
        "\nГотово. Проверь изменения в потребляющем репозитории и в wiki.\n"
        "  Первая сессия: выполни wiki-init, затем reindex и validate.\n"
        "  Коммиты и push выполняются по задаче пользователя и правилам команды.\n"
        "  Для Codex проверь доверие к хукам через /hooks; без хуков читай AGENTS.md вручную."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
