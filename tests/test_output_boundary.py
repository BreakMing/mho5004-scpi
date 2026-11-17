"""Exercise real local entrypoints with an externally selected task directory."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4
from hashlib import sha256

PACKAGE = Path(__file__).resolve().parents[1]


class OutputBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(os.environ.get('MHO5004_TEST_OUTPUT', tempfile.gettempdir())) / ('output-' + uuid4().hex[:8])
        cls.work.mkdir(parents=True)
        cls.moved = cls.work / 'skill with spaces'
        shutil.copytree(PACKAGE, cls.moved, ignore=shutil.ignore_patterns('.git', '__pycache__', 'output', '.venv'))
        print('OUTPUT_BOUNDARY_VALIDATION: ' + str(cls.work), flush=True)

    def setUp(self):
        self.project = self.work / ('experiment ' + uuid4().hex[:8])
        self.project.mkdir()

    def snapshot(self):
        return {file.relative_to(self.moved).as_posix(): sha256(file.read_bytes()).hexdigest()
                for file in self.moved.rglob('*') if file.is_file()}

    def invoke(self, script, *args, body=None, script_prefix=''):
        # Deliberately omit -B and PYTHONDONTWRITEBYTECODE: the entrypoints must
        # avoid adding imported-module bytecode to the installed Skill themselves.
        code = (
            'import runpy,sys\nfrom pathlib import Path\n'
            'def guard(event,args):\n'
            '    if event.startswith("socket."):\n'
            '        raise RuntimeError("Network forbidden in output boundary validation")\n'
            'sys.addaudithook(guard)\n'
            'script=Path(sys.argv[1]);sys.path.insert(0,str(script.parent))\n'
            'sys.argv=sys.argv[1:]\n'
        )
        code += body or 'runpy.run_path(str(script),run_name="__main__")\n'
        return subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', code,
                               script_prefix + str(self.moved / 'scripts' / script), *map(str, args)],
                              cwd=self.project, text=True, capture_output=True, encoding='utf-8', timeout=60)

    def test_missing_output_is_rejected_before_files_or_network(self):
        for script, args in [('scope_csv.py', ['analyze', self.moved / 'assets/samples/ch1-compensation.csv']),
                             ('scope_csv.py', ['save']), ('capture_scope.py', []),
                             ('capture_scope.py', ['--reindex'])]:
            with self.subTest(script=script, args=args):
                before = self.snapshot()
                result = self.invoke(script, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('required', result.stderr)
                self.assertNotIn('Network forbidden', result.stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(list(self.project.iterdir()), [])

    def test_inside_skill_and_relative_outputs_are_rejected(self):
        for target in [self.moved, self.moved / 'output/new-run', self.moved.parent, Path('relative-output')]:
            for script, args in [('scope_csv.py', ['analyze', self.moved / 'assets/samples/ch1-compensation.csv', '--output', target]),
                                 ('scope_csv.py', ['save', '--output', target]),
                                 ('capture_scope.py', ['--reindex', '--output-root', target]),
                                 ('capture_scope.py', ['--output-root', target])]:
                with self.subTest(target=str(target), script=script, args=args):
                    before = self.snapshot()
                    result = self.invoke(script, *args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('outside the Skill', result.stderr)
                    self.assertNotIn('Network forbidden', result.stderr)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(list(self.project.iterdir()), [])

    def test_external_analysis_and_gallery_leave_skill_unchanged(self):
        before = self.snapshot()
        output = self.project / 'output/csv/run-01'
        result = self.invoke('scope_csv.py', 'analyze', self.moved / 'assets/samples/ch1-compensation.csv',
                             '--start-s=-0.000001', '--end-s=0.000001', '--output', output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((output / 'summary.json').read_text('utf-8'))['selection']['selected_rows'], 3001)
        self.assertTrue((output / 'summary.md').is_file())
        archive = self.project / 'output/screenshots'
        historical = self.moved / 'assets/previews/screenshot-gallery/historical-20261109'
        shutil.copytree(historical, archive / historical.name)
        result = self.invoke('capture_scope.py', '--reindex', '--output-root', archive)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('历史截图', (archive / 'index.html').read_text('utf-8'))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.moved.rglob('__pycache__')))

    def test_direct_runtime_and_analysis_reject_skill_outputs(self):
        body = (
            'sys.dont_write_bytecode=True\n'
            'from scope_runtime import Runtime\n'
            'from scope_csv_data import read_csv,write_analysis\n'
            'from capture_scope import rebuild_index,capture_screen\n'
            'root=script.parent.parent\n'
            'record=read_csv(root/"assets/samples/ch1-compensation.csv")\n'
            'for call in (lambda:Runtime(root),lambda:write_analysis(record,root),\n'
            '             lambda:rebuild_index(root),lambda:capture_screen(root)):\n'
            '    try:call()\n'
            '    except ValueError as error:\n'
            '        assert "outside the Skill" in str(error),repr(error)\n'
            '    else:raise AssertionError("Internal output accepted")\n'
        )
        before = self.snapshot()
        result = self.invoke('scope_csv.py', body=body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_existing_temporary_file_cannot_overwrite_skill_resource(self):
        before = self.snapshot()
        resource = self.moved / 'assets/data-summary.md.tpl'
        archive = self.project / 'screenshots'
        archive.mkdir()
        os.link(resource, archive / 'index.csv.tmp')
        result = self.invoke('capture_scope.py', '--reindex', '--output-root', archive)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        runtime = self.project / 'runtime'
        runtime.mkdir()
        os.link(resource, runtime / 'runtime.json.tmp')
        body = (
            'sys.dont_write_bytecode=True\n'
            'from scope_runtime import Runtime\n'
            'try:Runtime(Path(sys.argv[1]),host="192.0.2.10")\n'
            'except FileExistsError:pass\n'
            'else:raise AssertionError("Existing temporary file overwritten")\n'
        )
        result = self.invoke('scope_csv.py', runtime, body=body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows device paths only')
    def test_windows_device_paths_cannot_alias_skill(self):
        for prefix in ('\\\\?\\', '\\\\.\\'):
            for target in (prefix + str(self.moved), prefix + str(self.moved / 'output/new-run')):
                for script, args in [('scope_csv.py', ['analyze', self.moved / 'assets/samples/ch1-compensation.csv', '--output', target]),
                                     ('scope_csv.py', ['save', '--output', target]),
                                     ('capture_scope.py', ['--reindex', '--output-root', target]),
                                     ('capture_scope.py', ['--output-root', target])]:
                    with self.subTest(target=target, script=script, args=args):
                        before = self.snapshot()
                        result = self.invoke(script, *args)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('outside the Skill', result.stderr)
                        self.assertNotIn('Network forbidden', result.stderr)
                        self.assertEqual(self.snapshot(), before)
        body = (
            'sys.dont_write_bytecode=True\n'
            'from scope_paths import external_output\n'
            'root=script.parent.parent\n'
            'for prefix in ("\\\\\\\\?\\\\","\\\\\\\\.\\\\"):\n'
            '    try:external_output(prefix+str(root))\n'
            '    except ValueError:pass\n'
            '    else:raise AssertionError("Device alias accepted")\n'
        )
        result = self.invoke('scope_csv.py', body=body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # The same alias can originate in __file__, with a normal output path.
        for prefix in ('\\\\?\\', '\\\\.\\'):
            for script, args in [('scope_csv.py', ['analyze', self.moved / 'assets/samples/ch1-compensation.csv', '--output', self.moved / 'aliased-run']),
                                 ('scope_csv.py', ['save', '--output', self.moved / 'aliased-run']),
                                 ('capture_scope.py', ['--reindex', '--output-root', self.moved]),
                                 ('capture_scope.py', ['--output-root', self.moved])]:
                with self.subTest(script_prefix=prefix, script=script):
                    before = self.snapshot()
                    result = self.invoke(script, *args, script_prefix=prefix)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('outside the Skill', result.stderr)
                    self.assertNotIn('Network forbidden', result.stderr)
                    self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
