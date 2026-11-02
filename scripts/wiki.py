"""CLI agent-wiki: поиск и запись памяти проекта.

    python .agent-wiki/scripts/wiki.py <команда> [аргументы]

Команды:
    where                       путь к папке проекта в wiki
    init <project>              создать проект из projects/_template
    recall <запрос> [--all] [--full]
                                поиск по памяти проекта (--all — по всем проектам)
    new-memory <slug> -t TYPE -d "описание"
                                новый факт в memory/ (TYPE: fact|feedback|reference|context)
    new-session <slug> -s "итог в одну строку"
    new-adr "<заголовок>" [--slug english-slug]
    new-spec <slug> -s "цель"
    reindex                     пересобрать авто-разделы index.md
    validate                    проверить структуру (validate.py)
    update                      подтянуть свежую wiki (fast-forward, локальные правки сохраняются)
    sync [-m MSG] [--push]      pull --rebase, commit изменений проекта, опционально push

Проект определяется из --project, env AGENT_WIKI_PROJECT или .agent-wiki.json.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

AUTO_START = "<!-- auto:index:start -->"
AUTO_END = "<!-- auto:index:end -->"
MEMORY_TYPES = ("fact", "feedback", "reference", "context")


def die(msg: str) -> None:
    print(f"ошибка: {msg}", file=sys.stderr)
    sys.exit(1)


def require_project(args) -> tuple[str, Path]:
    project = common.resolve_project(getattr(args, "project", None))
    if not project:
        die("проект не определён: укажи --project или запусти install.py")
    pdir = common.project_dir(project)
    if not pdir.is_dir():
        die(f"нет проекта {project}: python scripts/wiki.py init {project}")
    return project, pdir


def write_new(path: Path, content: str) -> None:
    if path.exists():
        die(f"файл уже существует: {path} — обнови его вместо создания нового")
    path.parent.mkdir(parents=True, exist_ok=True)
    common.write_text(path, content)
    print(path)


# --- команды -----------------------------------------------------------------

def cmd_where(args) -> None:
    _, pdir = require_project(args)
    print(pdir)


def init_project(project: str) -> Path:
    pdir = common.project_dir(project)
    if pdir.exists():
        return pdir
    shutil.copytree(common.TEMPLATE_DIR, pdir)
    values = {"PROJECT": project, "DATE": common.today()}
    for f in pdir.rglob("*.md"):
        common.write_text(f, common.render(f.read_text(encoding="utf-8"), values))
    return pdir


def cmd_init(args) -> None:
    project = common.slugify(args.name)
    existed = common.project_dir(project).exists()
    pdir = init_project(project)
    print(("уже есть: " if existed else "создан: ") + str(pdir))


def cmd_recall(args) -> None:
    query = args.query.lower()
    terms = [t for t in re.split(r"\s+", query) if t]
    if args.all:
        roots = [p for p in common.PROJECTS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")]
        roots += [common.WIKI_ROOT / "standards", common.WIKI_ROOT / "context"]
    else:
        _, pdir = require_project(args)
        roots = [pdir, common.WIKI_ROOT / "context", common.WIKI_ROOT / "standards"]

    hits = []
    for root in roots:
        for f in sorted(root.rglob("*.md")):
            text = f.read_text(encoding="utf-8")
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score == 0 or not all(t in low for t in terms):
                continue
            fm = common.read_frontmatter(f)
            lines = [
                (i + 1, ln.strip())
                for i, ln in enumerate(text.splitlines())
                if any(t in ln.lower() for t in terms)
            ]
            hits.append((score, f, fm, lines))

    if not hits:
        print("ничего не найдено")
        return
    hits.sort(key=lambda h: -h[0])
    for score, f, fm, lines in hits[: args.limit]:
        rel = f.relative_to(common.WIKI_ROOT).as_posix()
        desc = fm.get("description") or fm.get("summary") or ""
        print(f"{rel}" + (f" — {desc}" if desc else ""))
        shown = lines if args.full else lines[:3]
        for n, ln in shown:
            print(f"    {n}: {ln[:160]}")


def frontmatter(fields: dict) -> str:
    body = "\n".join(f"{k}: {json.dumps(str(v), ensure_ascii=False)}" for k, v in fields.items())
    return f"---\n{body}\n---\n"


def cmd_new_memory(args) -> None:
    _, pdir = require_project(args)
    if args.type not in MEMORY_TYPES:
        die(f"type должен быть одним из {MEMORY_TYPES}")
    slug = common.slugify(args.slug)
    fm = frontmatter({
        "name": slug,
        "description": args.description,
        "type": args.type,
        "created": common.today(),
        "updated": common.today(),
    })
    body = (
        "\n<Факт одним-двумя абзацами.>\n\n"
        "**Почему:** <откуда известно / почему важно>\n\n"
        "**Как применять:** <что делать агенту в следующий раз>\n"
    )
    write_new(pdir / "memory" / f"{slug}.md", fm + body)
    reindex(pdir)


def cmd_new_session(args) -> None:
    _, pdir = require_project(args)
    slug = common.slugify(args.slug)
    fm = frontmatter({"date": common.today(), "summary": args.summary, "agent": args.agent})
    body = (
        f"\n# {common.today()} — {args.summary}\n\n"
        "## Цель\n\n-\n\n"
        "## Сделано\n\n-\n\n"
        "## Решения и находки\n\n- (ссылки на memory/, decisions/, gotchas.md)\n\n"
        "## Открытые вопросы / следующий шаг\n\n-\n"
    )
    write_new(pdir / "sessions" / f"{common.today()}-{slug}.md", fm + body)
    reindex(pdir)


def cmd_new_adr(args) -> None:
    _, pdir = require_project(args)
    ddir = pdir / "decisions"
    nums = [int(m.group(1)) for p in ddir.glob("*.md") if (m := re.match(r"(\d{4})-", p.name))]
    num = max(nums, default=0) + 1
    slug = common.slugify(args.slug or args.title)
    fm = frontmatter({"status": "proposed", "date": common.today(), "description": args.title})
    body = (
        f"\n# {num:04d}. {args.title}\n\n"
        "## Контекст\n\n<Какая проблема, ограничения.>\n\n"
        "## Решение\n\n<Что выбрали.>\n\n"
        "## Альтернативы\n\n- <вариант> — <почему нет>\n\n"
        "## Последствия\n\n- <что стало проще/сложнее>\n"
    )
    write_new(ddir / f"{num:04d}-{slug}.md", fm + body)
    reindex(pdir)


def cmd_new_spec(args) -> None:
    _, pdir = require_project(args)
    slug = common.slugify(args.slug)
    fm = frontmatter({"status": "draft", "date": common.today(), "description": args.summary})
    body = (
        f"\n# {args.summary}\n\n"
        "## Зачем\n\n<Проблема и ожидаемый результат.>\n\n"
        "## Объём\n\n- Входит:\n- Не входит:\n\n"
        "## План\n\n1.\n\n"
        "## Как проверить\n\n-\n"
    )
    write_new(pdir / "specs" / f"{common.today()}-{slug}.md", fm + body)
    reindex(pdir)


def list_entries(pdir: Path, sub: str, limit: int | None = None, newest_first: bool = False) -> list[str]:
    files = sorted((p for p in (pdir / sub).glob("*.md") if p.name != "README.md"), reverse=newest_first)
    if limit:
        files = files[:limit]
    out = []
    for f in files:
        fm = common.read_frontmatter(f)
        desc = fm.get("description") or fm.get("summary") or ""
        status = fm.get("status")
        tag = f" `{status}`" if status and sub in ("decisions", "specs") else ""
        out.append(f"- [{f.stem}]({sub}/{f.name}){tag}" + (f" — {desc}" if desc else ""))
    return out


def reindex(pdir: Path) -> None:
    index = pdir / "index.md"
    text = index.read_text(encoding="utf-8") if index.exists() else f"# {pdir.name}\n"
    blocks = [AUTO_START, "<!-- Генерируется `wiki.py reindex`, руками не править. -->", ""]
    for title, sub, limit, newest in (
        ("Память", "memory", None, False),
        ("Решения (ADR)", "decisions", None, False),
        ("Спеки", "specs", 5, True),
        ("Сессии (последние)", "sessions", 5, True),
    ):
        entries = list_entries(pdir, sub, limit, newest)
        blocks.append(f"### {title}")
        blocks.append("")
        blocks.extend(entries or ["- пока пусто"])
        blocks.append("")
    blocks.append(AUTO_END)
    auto = "\n".join(blocks)
    if AUTO_START in text and AUTO_END in text:
        pre = text.split(AUTO_START, 1)[0]
        post = text.split(AUTO_END, 1)[1]
        text = pre + auto + post
    else:
        text = text.rstrip() + "\n\n" + auto + "\n"
    common.write_text(index, text)


def cmd_reindex(args) -> None:
    _, pdir = require_project(args)
    reindex(pdir)
    print(f"обновлён {pdir / 'index.md'}")


def cmd_validate(args) -> None:
    import validate
    sys.exit(validate.main([]))


def cmd_update(args) -> None:
    timeout = float(common.load_config().get("session_start", {}).get("update_timeout", 10))
    print(common.update_wiki(timeout) or "wiki актуальна")


def cmd_sync(args) -> None:
    project, pdir = require_project(args)
    root = common.WIKI_ROOT
    branch = common.load_config().get("default_branch", "main")

    def run(*a: str, fatal: bool = True) -> str:
        ok, out = common.git_run(*a, cwd=root)
        if not ok and fatal:
            die(f"git {' '.join(a)}:\n{out}")
        return out if ok else ""

    top = run("rev-parse", "--show-toplevel", fatal=False)
    if not top or Path(top).resolve() != root.resolve():
        die("sync требует отдельный Git-репозиторий wiki (например, сабмодуль)")
    staged = run("diff", "--cached", "--name-only", "-z").split("\0")
    if any(name and not name.startswith(f"projects/{project}/") for name in staged):
        die("в индексе Git подготовлены файлы вне текущего проекта; сохрани их отдельно")
    for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply"):
        marker_path = Path(run("rev-parse", "--git-path", marker))
        if not marker_path.is_absolute():
            marker_path = root / marker_path
        if marker_path.exists():
            die("в wiki не завершён merge или rebase; сначала разреши конфликт")
    import validate
    if validate.main([]):
        die("проверка wiki не пройдена; коммит и push отменены")

    current = run("symbolic-ref", "-q", "--short", "HEAD", fatal=False)
    if not current:
        print(f"detached HEAD → checkout {branch}")
        if run("show-ref", "--verify", f"refs/heads/{branch}", fatal=False):
            # Не переключаемся с локальных detached-коммитов на другую историю.
            if run("rev-list", "--count", f"{branch}..HEAD") != "0":
                die("detached HEAD содержит отдельные коммиты; выбери ветку вручную")
            run("checkout", branch)
        else:
            run("checkout", "-b", branch)
        current = branch

    run("add", "--", f"projects/{project}")
    if not run("diff", "--cached", "--name-only"):
        print("нечего коммитить")
    else:
        msg = args.message or f"wiki({project}): обновление памяти {common.today()}"
        run("commit", "-m", msg)
        print(f"commit: {msg}")

    if "origin" in run("remote", fatal=False).split():
        # При ошибке pull немедленно выходим: локальный коммит остаётся сохранён.
        run("pull", "--rebase", "--autostash", "origin", current)
    if args.push:
        run("push", "origin", f"HEAD:refs/heads/{current}")
        print(f"push в origin/{current} выполнен")
        print("Если wiki — сабмодуль, сохрани его указатель в основном репозитории.")


def main(argv=None) -> None:
    common.setup_utf8()
    ap = argparse.ArgumentParser(prog="wiki.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", help="имя проекта в wiki")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("where").set_defaults(fn=cmd_where)
    s = sub.add_parser("init"); s.add_argument("name"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("recall")
    s.add_argument("query"); s.add_argument("--all", action="store_true"); s.add_argument("--full", action="store_true")
    s.add_argument("--limit", type=int, default=15); s.set_defaults(fn=cmd_recall)
    s = sub.add_parser("new-memory")
    s.add_argument("slug"); s.add_argument("-t", "--type", default="fact"); s.add_argument("-d", "--description", required=True)
    s.set_defaults(fn=cmd_new_memory)
    s = sub.add_parser("new-session")
    s.add_argument("slug"); s.add_argument("-s", "--summary", required=True); s.add_argument("--agent", default="unknown")
    s.set_defaults(fn=cmd_new_session)
    s = sub.add_parser("new-adr"); s.add_argument("title")
    s.add_argument("--slug", help="английский slug для имени файла (иначе транслит заголовка)")
    s.set_defaults(fn=cmd_new_adr)
    s = sub.add_parser("new-spec")
    s.add_argument("slug"); s.add_argument("-s", "--summary", required=True); s.set_defaults(fn=cmd_new_spec)
    sub.add_parser("reindex").set_defaults(fn=cmd_reindex)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    sub.add_parser("update").set_defaults(fn=cmd_update)
    s = sub.add_parser("sync"); s.add_argument("-m", "--message"); s.add_argument("--push", action="store_true")
    s.set_defaults(fn=cmd_sync)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
