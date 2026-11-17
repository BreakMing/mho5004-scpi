"""Offline portability checks using one preserved real scope CSV; no fake device."""
from decimal import Decimal
from hashlib import sha256
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

PACKAGE = Path(__file__).resolve().parents[1]
SAMPLE_HASH = '082f52697316cabb1d19c2494304085109380ce688edfa1315ff7fe80848ce87'


class PortableSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PACKAGE / 'SKILL.md').is_file():
            raise AssertionError('Missing self-contained Skill project: SKILL.md')
        cls.work = Path(os.environ.get('MHO5004_TEST_OUTPUT', tempfile.gettempdir())) / ('mho5004-portable-' + uuid4().hex)
        cls.work.mkdir(parents=True, exist_ok=False)
        cls.moved = cls.work / 'relocated skill with spaces'
        shutil.copytree(PACKAGE, cls.moved, ignore=shutil.ignore_patterns('.git', '__pycache__', 'output', '.venv'))
        cls.cwd = cls.work / 'unrelated working directory'
        cls.cwd.mkdir()
        print('LOCAL_VALIDATION_OUTPUT: ' + str(cls.work), flush=True)

    def run_local(self, script, *args):
        # A network attempt fails rather than reaching an instrument. There is
        # no emulated instrument, synthetic response, or generated test waveform.
        bootstrap = (
            'import runpy,sys\n'
            'from pathlib import Path\n'
            'def guard(event,args):\n'
            '    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):\n'
            '        raise RuntimeError("Network forbidden in local validation")\n'
            'sys.addaudithook(guard)\n'
            'script=Path(sys.argv[1]);sys.path.insert(0,str(script.parent))\n'
            'sys.argv=sys.argv[1:];runpy.run_path(str(script),run_name="__main__")\n'
        )
        result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', bootstrap,
                                 str(self.moved / 'scripts' / script), *map(str, args)],
                                cwd=self.cwd, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_cli_help_is_local_after_move(self):
        for script, args in [('capture_scope.py', ['--help']), ('scope_csv.py', ['--help']),
                             ('scope_csv.py', ['save', '--help'])]:
            with self.subTest(script=script, args=args):
                self.assertIn('usage:', self.run_local(script, *args).stdout)

    def test_runtime_loads_only_bundled_transport_and_config(self):
        bootstrap = (
            'import sys,json\nfrom pathlib import Path\n'
            'def guard(event,args):\n'
            '    if event in ("socket.connect","socket.getaddrinfo","socket.sendto"):\n'
            '        raise RuntimeError("Network forbidden")\n'
            'sys.addaudithook(guard)\n'
            'root=Path(sys.argv[1]);sys.path.insert(0,str(root/"scripts"))\n'
            'from scope_runtime import Runtime\n'
            'out=Path(sys.argv[2]);out.mkdir()\n'
            'runtime=Runtime(out,host="192.0.2.10")\n'
            'assert Path(runtime.transport.__file__).resolve().is_relative_to(root.resolve())\n'
            'assert "campaign_id" not in runtime.transport.CONFIG\n'
            'print(json.dumps({"host":runtime.host,"ftp_port":runtime.ftp_port}))\n'
        )
        result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', bootstrap,
                                 str(self.moved), str(self.work / 'runtime-only')],
                                cwd=self.cwd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['ftp_port'], 3721)

    def test_real_csv_analysis_after_move(self):
        sample = self.moved / 'assets/samples/ch1-compensation.csv'
        self.assertEqual(sha256(sample.read_bytes()).hexdigest(), SAMPLE_HASH)
        out = self.work / 'csv-window'
        self.run_local('scope_csv.py', 'analyze', sample, '--start-s=-0.000001',
                       '--end-s=0.000001', '--output', out)
        summary = json.loads((out / 'summary.json').read_text('utf-8'))
        self.assertEqual(summary['original']['rows'], 180000)
        self.assertEqual(summary['selection']['selected_rows'], 3001)
        self.assertEqual((out / 'original.csv').read_bytes(), sample.read_bytes())
        with sample.open(encoding='utf-8-sig', newline='') as stream:
            rows = csv.reader(stream)
            for row in rows:
                if row and row[0] == 'TIME':
                    break
            expected = [(float(row[0]), float(row[1])) for row in rows
                        if row and Decimal('-0.000001') <= Decimal(row[0]) <= Decimal('0.000001')]
        with (out / 'window.csv').open(newline='') as stream:
            actual = [(float(row['TIME']), float(row['CH1'])) for row in csv.DictReader(stream)]
        self.assertEqual(actual, expected)
        from PIL import Image
        with Image.open(out / 'waveform.png') as image:
            image.verify()
        self.assertEqual(summary['plot']['time_zero_shift_s'], 0)

    def test_references_are_local_and_resolve(self):
        for file in self.moved.rglob('*.md'):
            text = file.read_text('utf-8')
            for target in re.findall(r'\[[^\]\n]+\]\(([^)\n]+)\)', text):
                if target.startswith('https://'):
                    continue  # External documentation is never fetched by offline tests.
                self.assertNotRegex(target, r'^[A-Za-z]+:|^/')
                resolved = (file.parent / target.split('#', 1)[0]).resolve()
                self.assertTrue(resolved.is_relative_to(self.moved.resolve()), (file, target))
                self.assertTrue(resolved.exists(), (file, target))
        for file in list((self.moved / 'scripts').glob('*.py')) + list(self.moved.rglob('*.md')):
            text = file.read_text('utf-8')
            self.assertNotIn('MHO5004-Explore', text, file)
            self.assertNotIn('scpi_validation', text, file)

    def test_packaged_file_manifest(self):
        manifest = json.loads((self.moved / 'manifest.json').read_text('utf-8'))
        actual = {p.relative_to(self.moved).as_posix() for p in self.moved.rglob('*')
                  if p.is_file() and p.name != 'manifest.json'}
        self.assertEqual(actual, set(manifest['files']))
        for relative, expected in manifest['files'].items():
            file = (self.moved / relative).resolve()
            self.assertTrue(file.is_relative_to(self.moved.resolve()), relative)
            self.assertEqual(sha256(file.read_bytes()).hexdigest(), expected['sha256'], relative)


if __name__ == '__main__':
    unittest.main()
