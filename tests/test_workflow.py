"""Изолированные проверки реальной установки и обмена через локальный Git."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wiki-workflow-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wiki = self.root / 'team knowledge'
        shutil.copytree(SOURCE, self.wiki, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        self.app = self.root / 'demo app'
        self.app.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        PYTHONUTF8='1', AGENT_WIKI_STOP='on')
        self.env.pop('AGENT_WIKI_PROJECT', None)
        for path in (self.wiki, self.app):
            self.git(path, 'init', '-q', '-b', 'main')
            self.git(path, 'config', 'user.name', 'Template Test')
            self.git(path, 'config', 'user.email', 'test@example.invalid')
            self.git(path, 'config', 'commit.gpgsign', 'false')
        self.git(self.wiki, 'add', '.')
        self.git(self.wiki, 'commit', '-qm', 'initial template')

    def run_cmd(self, args, cwd=None, data=None, ok=True):
        out = subprocess.run([str(a) for a in args], cwd=cwd or self.app, env=self.env,
                             input=data, capture_output=True, text=True, encoding='utf-8')
        if ok:
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out

    def git(self, cwd, *args, **kw):
        return self.run_cmd(['git', *args], cwd=cwd, **kw)

    def install(self, *args, ok=True):
        return self.run_cmd([sys.executable, self.wiki / 'scripts/install.py', '--project', 'demo-app',
                             '--python', 'python3', *args], ok=ok)

    def cli(self, *args, ok=True):
        return self.run_cmd([sys.executable, self.wiki / 'scripts/wiki.py', *args], ok=ok)

    def hook(self, name, payload):
        return self.run_cmd([sys.executable, self.wiki / ('scripts/hooks/' + name + '.py')],
                            data=json.dumps(payload))

    def test_install_is_idempotent_and_preserves_user_content(self):
        (self.app / 'AGENTS.md').write_text('# Local instructions\nKeep this.\n', encoding='utf-8')
        own = {'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'echo user-hook'}]}
        (self.app / '.claude').mkdir()
        (self.app / '.claude/settings.json').write_text(
            json.dumps({'custom': True, 'hooks': {'SessionStart': [own]}}), encoding='utf-8')
        self.install()
        snapshot = {p.relative_to(self.app): p.read_bytes() for p in self.app.rglob('*')
                    if p.is_file() and '.git' not in p.relative_to(self.app).parts}
        self.install()
        self.assertEqual(snapshot, {p.relative_to(self.app): p.read_bytes() for p in self.app.rglob('*')
                                   if p.is_file() and '.git' not in p.relative_to(self.app).parts})
        self.assertIn('Keep this.', (self.app / 'AGENTS.md').read_text(encoding='utf-8'))
        cfg = json.loads((self.app / '.claude/settings.json').read_text(encoding='utf-8'))
        self.assertTrue(cfg['custom'])
        self.assertIn(own, cfg['hooks']['SessionStart'])
        self.assertEqual(len(cfg['hooks']['SessionStart']), 2)
        for tool in ('.agents', '.claude'):
            self.assertEqual(len(list((self.app / tool / 'skills').glob('*/SKILL.md'))), 4)
        self.cli('validate')

    def test_dry_run_and_agent_selection(self):
        self.install('--dry-run')
        self.assertFalse((self.app / 'AGENTS.md').exists())
        self.assertFalse((self.wiki / 'projects/demo-app').exists())
        self.install('--no-claude', '--no-hooks')
        self.assertFalse((self.app / '.claude').exists())
        self.assertFalse((self.app / '.codex').exists())
        self.assertTrue((self.app / '.agents/skills/wiki-init/SKILL.md').exists())

    def test_memory_recall_frontmatter_and_validation(self):
        self.install()
        self.cli('new-memory', 'retry-window', '-t', 'fact', '-d', 'Retries: "two" attempts')
        self.cli('new-session', 'setup', '-s', 'Setup: complete', '--agent', 'test')
        self.cli('new-spec', 'first-task', '-s', 'Verify workflow')
        self.cli('new-adr', 'A draft decision')
        self.assertIn('retry-window', self.cli('recall', 'Retries').stdout)
        self.assertIn('proposed', next((self.wiki / 'projects/demo-app/decisions').glob('0001-*.md')).read_text())
        self.cli('reindex')
        self.cli('validate')
        entry = self.wiki / 'projects/demo-app/memory/retry-window.md'
        entry.write_text(entry.read_text(encoding='utf-8') + '\n[broken](missing.md)\n', encoding='utf-8')
        self.assertNotEqual(self.cli('validate', ok=False).returncode, 0)

    def test_context_secret_and_project_path_validation(self):
        self.install()
        context = self.wiki / 'context/product.md'
        context.write_text(context.read_text(encoding='utf-8') + '\napi_key = "' + 'x' * 32 + '"\n', encoding='utf-8')
        self.assertNotEqual(self.cli('validate', ok=False).returncode, 0)
        self.assertNotEqual(self.cli('--project', '../escape', 'new-memory', 'bad', '-d', 'no', ok=False).returncode, 0)
        self.assertFalse((self.wiki / 'escape').exists())

    def test_hooks_load_context_remind_once_and_survive_bad_input(self):
        self.install()
        payload = {'session_id': self.temp.name, 'cwd': str(self.app), 'source': 'startup'}
        self.assertIn('demo-app', self.hook('session_start', payload).stdout)
        self.assertEqual(self.hook('stop', payload).stdout, '')
        code = self.app / 'app.py'
        code.write_text('print(1)\n', encoding='utf-8')
        os.utime(code, (4102444800, 4102444800))
        reminder = json.loads(self.hook('stop', payload).stdout)
        self.assertEqual(reminder['decision'], 'block')
        self.hook('session_start', dict(payload, source='compact'))
        self.assertEqual(self.hook('stop', payload).stdout, '')
        for name in ('session_start', 'stop'):
            self.run_cmd([sys.executable, self.wiki / ('scripts/hooks/' + name + '.py')], data='invalid JSON')

    def test_sync_does_not_commit_other_staged_files(self):
        self.install()
        (self.wiki / 'unrelated.txt').write_text('keep staged', encoding='utf-8')
        self.git(self.wiki, 'add', 'unrelated.txt')
        head = self.git(self.wiki, 'rev-parse', 'HEAD').stdout
        self.assertNotEqual(self.cli('sync', ok=False).returncode, 0)
        self.assertEqual(head, self.git(self.wiki, 'rev-parse', 'HEAD').stdout)

    def test_submodule_detached_sync_and_local_push(self):
        remote = self.root / 'remote.git'
        self.git(self.root, 'init', '--bare', str(remote))
        self.git(self.wiki, 'remote', 'add', 'origin', str(remote))
        self.git(self.wiki, 'push', '-qu', 'origin', 'main')
        self.git(self.app, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-b', 'main', str(remote), '.agent-wiki')
        self.wiki = self.app / '.agent-wiki'
        self.git(self.wiki, 'config', 'user.name', 'Template Test')
        self.git(self.wiki, 'config', 'user.email', 'test@example.invalid')
        self.git(self.wiki, 'config', 'commit.gpgsign', 'false')
        self.git(self.wiki, 'checkout', '--detach')
        self.git(self.wiki, 'branch', '-D', 'main')
        self.install()
        self.cli('new-memory', 'shared', '-d', 'Shared knowledge')
        self.cli('sync', '-m', 'wiki(demo-app): setup', '--push')
        self.assertIn('wiki(demo-app): setup', self.git(remote, 'log', 'main', '-1', '--format=%s').stdout)
        self.assertEqual(self.git(remote, 'rev-parse', 'main').stdout,
                         self.git(self.wiki, 'rev-parse', 'HEAD').stdout)

    def test_failed_pull_is_reported_as_failure(self):
        self.install()
        self.git(self.wiki, 'remote', 'add', 'origin', str(self.root / 'missing.git'))
        self.assertNotEqual(self.cli('sync', ok=False).returncode, 0)


if __name__ == '__main__':
    unittest.main()
