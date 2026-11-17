"""Missing live targets must fail before DNS/socket use; no instrument emulation."""
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


class ConnectionTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(os.environ.get('MHO5004_TEST_OUTPUT', tempfile.gettempdir())) / ('target-' + uuid4().hex)
        cls.work.mkdir(parents=True)
        cls.skill = cls.work / 'skill with spaces'
        shutil.copytree(PACKAGE, cls.skill, ignore=shutil.ignore_patterns('.git'))
        config = cls.skill / 'config/instrument.toml'
        config.write_text(re.sub(r'^host\s*=.*$', 'host = ""', config.read_text('utf-8'), flags=re.M), 'utf-8')
        print('TARGET_VALIDATION_OUTPUT: ' + str(cls.work), flush=True)

    def run_isolated(self, code, *args):
        guard = (
            'import sys\n'
            'def guard(event,args):\n'
            ' if event in ("socket.connect","socket.getaddrinfo","socket.sendto"):\n'
            '  print("NETWORK_ATTEMPT",file=sys.stderr)\n'
            '  raise AssertionError("NETWORK_ATTEMPT")\n'
            'sys.addaudithook(guard)\n'
        )
        return subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', guard + code,
                               str(self.skill), *map(str, args)], cwd=self.work,
                              capture_output=True, text=True, encoding='utf-8', timeout=30)

    def test_live_cli_missing_or_blank_host_never_reaches_network(self):
        code = (
            'import runpy\nfrom pathlib import Path\n'
            'root=Path(sys.argv[1]);script=root/"scripts"/sys.argv[2]\n'
            'sys.path.insert(0,str(script.parent))\n'
            'sys.argv=[str(script)]+sys.argv[3:]\n'
            'runpy.run_path(str(script),run_name="__main__")\n'
        )
        for script, action, output_flag in [('capture_scope.py', [], '--output-root'),
                                             ('scope_csv.py', ['save'], '--output')]:
            for host_args in ([], ['--host', '   ']):
                with self.subTest(script=script, host_args=host_args):
                    out = self.work / uuid4().hex
                    result = self.run_isolated(code, script, *action, output_flag, out, *host_args)
                    self.assertNotEqual(result.returncode, 0)
                    text = result.stdout + result.stderr
                    self.assertNotIn('NETWORK_ATTEMPT', text, text)
                    self.assertIn('host', text.lower(), text)
                    self.assertFalse(list(out.rglob('save-intent.json')))

    def test_runtime_and_direct_live_apis_require_target(self):
        code = (
            'from pathlib import Path\n'
            'root=Path(sys.argv[1]);sys.path.insert(0,str(root/"scripts"))\n'
            'from scope_runtime import Runtime\n'
            'from capture_scope import capture_screen\n'
            'import scope_transport as transport\n'
            'out=Path(sys.argv[2]);out.mkdir()\n'
            'errors=[]\n'
            'for action in (lambda:Runtime(out),lambda:capture_screen(out/"screens"),lambda:transport.Session()):\n'
            ' try:action()\n'
            ' except ValueError as error:\n'
            '  assert "host" in str(error).lower(),str(error)\n'
            '  errors.append(str(error))\n'
            ' else:raise AssertionError("Missing target accepted")\n'
            'transport.CONFIG["connection"]["host"]="192.0.2.10"\n'
            'try:capture_screen(out/"must-not-reuse-previous-host")\n'
            'except ValueError:pass\n'
            'else:raise AssertionError("Implicit previous host accepted")\n'
            'runtime=Runtime(out,host="192.0.2.10")\n'
            'assert runtime.host=="192.0.2.10"\n'
            'print(__import__("json").dumps(errors))\n'
        )
        result = self.run_isolated(code, self.work / 'api')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 3)


if __name__ == '__main__':
    unittest.main()
