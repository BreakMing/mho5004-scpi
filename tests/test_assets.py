"""Verify reusable presentation assets with preserved real CSV/screenshot files."""
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

PACKAGE = Path(__file__).resolve().parents[1]


class AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(os.environ.get('MHO5004_TEST_OUTPUT', tempfile.gettempdir())) / ('mho5004-assets-' + uuid4().hex)
        cls.work.mkdir(parents=True, exist_ok=False)
        cls.moved = cls.work / 'skill with spaces'
        shutil.copytree(PACKAGE, cls.moved, ignore=shutil.ignore_patterns('.git', '__pycache__', 'output', '.venv'))
        cls.cwd = cls.work / 'unrelated cwd'
        cls.cwd.mkdir()
        print('ASSET_VALIDATION_OUTPUT: ' + str(cls.work), flush=True)

    def run_local(self, script, *args):
        bootstrap = (
            'import runpy,sys\nfrom pathlib import Path\n'
            'def guard(event,args):\n'
            '    if event.startswith("socket."):\n'
            '        raise RuntimeError("Network forbidden in asset validation")\n'
            'sys.addaudithook(guard)\n'
            'script=Path(sys.argv[1]);sys.path.insert(0,str(script.parent))\n'
            'sys.argv=sys.argv[1:];runpy.run_path(str(script),run_name="__main__")\n'
        )
        result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', bootstrap,
                                 str(self.moved / 'scripts' / script), *map(str, args)],
                                cwd=self.cwd, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def analyze(self, name):
        output = self.work / name
        self.run_local('scope_csv.py', 'analyze', self.moved / 'assets/samples/ch1-compensation.csv',
                       '--start-s=-0.000001', '--end-s=0.000001', '--output', output)
        return output

    def test_style_file_changes_real_plot_after_move(self):
        style_file = self.moved / 'assets/waveform-style.json'
        self.assertTrue(style_file.is_file(), 'Missing reusable waveform style')
        original = style_file.read_bytes()
        try:
            style = json.loads(original)
            style['colors']['background'] = '#fff7e6'
            style_file.write_text(json.dumps(style), encoding='utf-8')
            output = self.analyze('style-change')
            from PIL import Image
            with Image.open(output / 'waveform.png') as picture:
                self.assertEqual(picture.getpixel((0, 0)), (255, 247, 230))
                self.assertEqual(picture.size, (style['canvas']['width'], style['canvas']['height']))
            summary = json.loads((output / 'summary.json').read_text('utf-8'))
            self.assertEqual(summary['selection']['selected_rows'], 3001)
            self.assertEqual(summary['selection']['time_zero_shift_s'], 0)
            self.assertEqual(summary['templates']['waveform_style']['sha256'], sha256(style_file.read_bytes()).hexdigest())
        finally:
            style_file.write_bytes(original)

    def test_summary_template_uses_actual_data_and_preserves_original(self):
        template = self.moved / 'assets/data-summary.md.tpl'
        self.assertTrue(template.is_file(), 'Missing reusable data summary template')
        original = template.read_bytes()
        try:
            template.write_text(original.decode('utf-8') + '\n<!-- asset-template-check -->\n', encoding='utf-8')
            output = self.analyze('summary-table')
            summary = json.loads((output / 'summary.json').read_text('utf-8'))
            markdown = (output / 'summary.md').read_text('utf-8')
            self.assertIn('asset-template-check', markdown)
            for value in ('CH1', '180,000', '3,001', '-1e-06', '1e-06', summary['original']['sha256']):
                self.assertIn(value, markdown)
            self.assertNotIn('${', markdown)
            self.assertEqual((output / 'original.csv').read_bytes(), (self.moved / 'assets/samples/ch1-compensation.csv').read_bytes())
            self.assertEqual(summary['artifacts']['summary.md']['sha256'], sha256((output / 'summary.md').read_bytes()).hexdigest())
            self.assertEqual(summary['templates']['data_summary']['sha256'], sha256(template.read_bytes()).hexdigest())
        finally:
            template.write_bytes(original)

    def test_gallery_template_keeps_only_verified_historical_image(self):
        template = self.moved / 'assets/screenshot-gallery.html.tpl'
        self.assertTrue(template.is_file(), 'Missing reusable screenshot gallery template')
        records = list((self.moved / 'assets/previews/screenshot-gallery').glob('*/capture_info.json'))
        self.assertEqual(len(records), 1, 'A real historical screenshot is required for local gallery validation')
        archive = self.work / 'gallery'
        valid = archive / records[0].parent.name
        shutil.copytree(records[0].parent, valid)
        damaged = archive / 'damaged-local-copy'
        shutil.copytree(records[0].parent, damaged)
        bad = json.loads((damaged / 'capture_info.json').read_text('utf-8'))
        bad['file_sha256'] = '0' * 64
        (damaged / 'capture_info.json').write_text(json.dumps(bad), encoding='utf-8')
        original = template.read_bytes()
        try:
            template.write_text(original.decode('utf-8').replace('<body>', '<body data-template-check="yes">'), encoding='utf-8')
            result = self.run_local('capture_scope.py', '--reindex', '--output-root', archive)
            html = (archive / 'index.html').read_text('utf-8')
            self.assertIn('data-template-check="yes"', html)
            self.assertNotIn('${', html)
            self.assertIn('历史截图', html, 'Reindexing historical examples must preserve their visible history notice')
            self.assertIn('SHA-256 mismatch', result.stderr)
            with (archive / 'index.csv').open(encoding='utf-8-sig', newline='') as stream:
                indexed = list(csv.DictReader(stream))
            self.assertEqual(len(indexed), 1)
            metadata = json.loads((valid / 'capture_info.json').read_text('utf-8'))
            self.assertEqual(indexed[0]['sha256'], metadata['file_sha256'])
            self.assertEqual(indexed[0]['identity'], metadata['identity'])
            self.assertEqual(sha256((valid / metadata['file_evidence']).read_bytes()).hexdigest(), metadata['file_sha256'])
        finally:
            template.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
