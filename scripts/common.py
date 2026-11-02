"""Общие функции agent-wiki: пути, конфиг, определение проекта.

Только стандартная библиотека Python 3.9+, чтобы скрипты работали
в любом репозитории без установки зависимостей.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = WIKI_ROOT / "projects"
TEMPLATE_DIR = PROJECTS_DIR / "_template"
CONSUMER_CONFIG = ".agent-wiki.json"

MARK_START = "<!-- agent-wiki:start -->"
MARK_END = "<!-- agent-wiki:end -->"


def setup_utf8() -> None:
    """На Windows консоль по умолчанию не UTF-8 — кириллица ломается."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def load_config() -> dict:
    with open(WIKI_ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def git(*args: str, cwd: Path | str | None = None) -> str:
    """Запускает git и возвращает stdout ('' при ошибке)."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return out.stdout.rstrip()  # rstrip: у `git status --porcelain` значим ведущий пробел
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def git_run(
    *args: str, cwd: Path | str | None = None, timeout: float | None = None, env: dict | None = None
) -> tuple[bool, str]:
    """Запускает git, возвращает (успех, stdout+stderr) — для команд, где важен результат."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return out.returncode == 0, (out.stdout + out.stderr).strip()
    except FileNotFoundError:
        return False, "git не найден"
    except subprocess.TimeoutExpired:
        return False, f"таймаут {timeout} с"


def noninteractive_git_env() -> dict:
    """Окружение, в котором git не ждёт ввода логина или пароля, а сразу падает."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GCM_INTERACTIVE", "never")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def update_wiki(timeout: float = 10.0) -> str:
    """Подтягивает свежую wiki из origin, если это безопасно.

    Только fast-forward. Незакоммиченные правки git переносит сам, а если они
    задевают обновляемые файлы, отказывается — тогда возвращается подсказка.
    Неотправленные коммиты не трогаются. Возвращает строку для вывода
    ('' — wiki актуальна или remote нет).
    """
    root = WIKI_ROOT
    branch = load_config().get("default_branch", "main")
    remote_ref = f"origin/{branch}"
    if "origin" not in git("remote", cwd=root).split():
        return ""
    ok, out = git_run("fetch", "-q", "origin", branch, cwd=root, timeout=timeout, env=noninteractive_git_env())
    if not ok:
        first = out.splitlines()[0] if out else "нет ответа"
        return f"Не удалось проверить обновления wiki ({first}). Работаю с локальной копией."

    behind = int(git("rev-list", "--count", f"HEAD..{remote_ref}", cwd=root) or 0)
    ahead = int(git("rev-list", "--count", f"{remote_ref}..HEAD", cwd=root) or 0)
    if behind == 0:
        return ""
    hint = f"Wiki отстаёт от {remote_ref} на {behind} коммит(ов)"
    if ahead:
        return f"{hint} и содержит {ahead} неотправленных. Выполни `wiki.py sync`, он сделает pull --rebase."
    current = git("symbolic-ref", "-q", "--short", "HEAD", cwd=root)
    if current == branch:
        ok, out = git_run("merge", "-q", "--ff-only", remote_ref, cwd=root)
    elif not current:
        # сабмодуль в detached HEAD: переходим на ветку, если локальная ветка не потеряет коммитов
        local = git("rev-parse", "-q", "--verify", f"refs/heads/{branch}", cwd=root)
        if local and git("rev-list", "--count", f"{remote_ref}..{branch}", cwd=root) not in ("", "0"):
            return f"{hint}. Локальная ветка {branch} разошлась с {remote_ref}: выполни `wiki.py sync`."
        ok, out = git_run("checkout", "-q", "-B", branch, remote_ref, cwd=root)
    else:
        return f"{hint}. Сейчас открыта ветка {current}, обновление пропущено."
    if not ok:
        reason = out.splitlines()[0] if out else "ошибка git"
        return f"{hint}. Обновить не удалось, вероятно мешают локальные правки ({reason}). Выполни `wiki.py sync`."
    return f"Wiki обновлена из {remote_ref}: +{behind} коммит(ов). Ниже уже свежий контекст."


def find_consumer_root(cwd: Path | str | None = None) -> Path | None:
    """Корень репозитория, к которому подключена wiki.

    1. Если wiki — сабмодуль, это superproject.
    2. Иначе — git toplevel от cwd (wiki лежит рядом, например ../agent-wiki).
    """
    super_root = git("rev-parse", "--show-superproject-working-tree", cwd=WIKI_ROOT)
    if super_root:
        return Path(super_root)
    start = Path(cwd) if cwd else Path.cwd()
    top = git("rev-parse", "--show-toplevel", cwd=start)
    if top and Path(top).resolve() != WIKI_ROOT:
        return Path(top)
    return None


def load_consumer_config(consumer_root: Path | None) -> dict:
    if not consumer_root:
        return {}
    path = consumer_root / CONSUMER_CONFIG
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
     "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
))


def slugify(value: str, max_len: int = 60) -> str:
    """kebab-case латиницей: кириллица транслитерируется, остальное → '-'."""
    value = "".join(_TRANSLIT.get(ch, ch) for ch in value.strip().lower())
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip("-._")
    if len(value) > max_len:
        value = value[:max_len].rsplit("-", 1)[0]
    return value or "untitled"


def guess_project_name(consumer_root: Path) -> str:
    """Имя проекта: из origin URL, иначе имя папки."""
    url = git("remote", "get-url", "origin", cwd=consumer_root)
    if url:
        name = re.split(r"[/:]", url.rstrip("/"))[-1]
        name = re.sub(r"\.git$", "", name)
        if name:
            return slugify(name)
    return slugify(consumer_root.name)


def resolve_project(explicit: str | None = None, cwd: Path | str | None = None) -> str | None:
    """Имя проекта: аргумент → env AGENT_WIKI_PROJECT → .agent-wiki.json → имя репо."""
    if explicit:
        return explicit
    env = os.environ.get("AGENT_WIKI_PROJECT")
    if env:
        return env
    root = find_consumer_root(cwd)
    cfg = load_consumer_config(root)
    if cfg.get("project"):
        return cfg["project"]
    if root:
        return guess_project_name(root)
    return None


def project_dir(project: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", project):
        raise ValueError("имя проекта должно быть безопасным slug без каталогов")
    return PROJECTS_DIR / project


def today() -> str:
    return date.today().isoformat()


def write_text(path: Path, text: str) -> None:
    """UTF-8 + LF на любой ОС (Path.write_text на Windows пишет CRLF)."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def render(text: str, values: dict) -> str:
    for key, val in values.items():
        text = text.replace("{{" + key + "}}", val)
    return text


def read_frontmatter(path: Path) -> dict:
    """Простой парсер frontmatter: плоские `key: value` и один уровень вложенности."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict = {}
    parent = None
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([\w-]+):\s*(.*)$", line)
        if not m:
            continue
        indent, key, val = m.groups()
        val = val.strip()
        if val.startswith('"'):
            try:
                val = json.loads(val)
            except ValueError:
                val = val.strip('"')
        else:
            val = val.strip("'")
        if indent and parent is not None:
            data.setdefault(parent, {})
            if isinstance(data[parent], dict):
                data[parent][key] = val
        else:
            data[key] = val
            parent = key if val == "" else None
    return data


def merge_marked_block(existing: str, block: str) -> str:
    """Вставляет/заменяет блок между маркерами agent-wiki, не трогая остальной текст."""
    block = block.strip()
    if MARK_START in existing and MARK_END in existing:
        pre = existing.split(MARK_START, 1)[0]
        post = existing.split(MARK_END, 1)[1]
        return pre + block + post
    if not existing.strip():
        return block + "\n"
    return existing.rstrip() + "\n\n" + block + "\n"
