"""Evidence/runtime adapter around the bundled VXI-11 transport."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from ftplib import FTP
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from scope_paths import external_output

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = ROOT / 'scripts/scope_transport.py'
STORAGE_FIELDS = ('SOURce', 'LOCAtion', 'ALLSegments', 'TYPE', 'FILename')


def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def dump(path, value):
    path = Path(path)
    external_output(path.parent)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def safe_value(value):
    value = str(value)
    if not value or any(c in value for c in '\r\n;') or not value.isascii():
        raise ValueError('Storage parameter is not a safe single ASCII SCPI value')
    return value


class Runtime:
    def __init__(self, output, host=None, ftp_port=None):
        self.output = external_output(output)
        spec = importlib.util.spec_from_file_location('_scope_csv_transport', TRANSPORT)
        self.transport = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.transport)
        self.transport.RUN = self.output
        self.transport.save_event = self.event  # No historical campaign/wiring labels in new logs.
        self.host = self.transport.require_host(host)
        self.transport.CONFIG['connection']['host'] = self.host
        self.ftp_port = ftp_port or self.transport.CONFIG['file_access']['ftp_port']
        if not 1 <= self.ftp_port <= 65535:
            raise ValueError('FTP port outside 1..65535')
        self.serial = 0
        self.sessions = 0
        self.cleanup_errors = []
        dump(self.output / 'runtime.json', {
            'host': self.host, 'ftp_port': self.ftp_port, 'transport': 'VXI-11',
            'device_name': self.transport.CONFIG['vxi11']['device_name'],
            'transport_source': str(TRANSPORT), 'transport_sha256': sha256(TRANSPORT.read_bytes()).hexdigest(),
            'note': 'Connection defaults only; historical wiring confirmations are not current observations.',
        })

    def event(self, kind, **fields):
        row = {'timestamp': timestamp(), 'event': kind, **fields}
        with (self.output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        if kind in ('vxi11_cleanup_problem', 'ftp_cleanup_problem'):
            self.cleanup_errors.append(row)

    @contextmanager
    def session(self):
        self.sessions += 1
        number = self.sessions
        session = self.transport.Session()
        entered = False
        error_info = (None, None, None)
        try:
            session.__enter__()
            entered = True
            yield session
        except BaseException as error:
            error_info = (type(error), error, error.__traceback__)
            raise
        finally:
            if entered:
                session.__exit__(*error_info)
            elif session.sock:
                session.sock.close()
            dump(self.output / f'session-{number:02d}-packets.json', session.packets)

    def q(self, session, label, command):
        self.serial += 1
        result = session.query(f'Q{self.serial:04d}.{label}', command)
        value = result.get('normalized', '').strip()
        if result['status'] != 'response_complete' or not value or value.lower().startswith('error:'):
            raise RuntimeError(f'{label}: response not usable; inspect evidence')
        return value

    def identity(self, session):
        value = self.q(session, 'identity', '*IDN?')
        parts = value.split(',')
        if len(parts) != 4 or parts[:2] != ['Micsig', 'MHO5004']:
            raise RuntimeError('Unexpected instrument identity: ' + value)
        if parts[3] != '1.611.171':
            raise RuntimeError('Firmware differs from the verified version; no save: ' + value)
        return value

    def state(self, session, label):
        result = {'trigger_status': self.q(session, label + '.state', ':TRIGger:STATus?'),
                  'segmented': self.q(session, label + '.segmented', ':ACQuire:SEGMented?'),
                  'sample_rate': self.q(session, label + '.rate', ':ACQuire:SRATe?'),
                  'timebase_extent': self.q(session, label + '.timebase', ':TIMebase:EXTent?'),
                  'timebase_position': self.q(session, label + '.position', ':TIMebase:POSition?'),
                  'channels': {}}
        for ch in (1, 2):
            result['channels'][f'CH{ch}'] = {
                key: self.q(session, f'{label}.CH{ch}.{key}', f':CHANnel{ch}:{command}?')
                for key, command in [('display', 'DISPlay'), ('probe', 'PROBe'), ('input', 'INPutres'),
                                     ('scale', 'SCALe'), ('position', 'POSition'), ('coupling', 'COUPle')]}
        return result

    def storage(self, session, label):
        return {field: safe_value(self.q(session, label + '.' + field, ':STORage:SAVE:' + field + '?'))
                for field in STORAGE_FIELDS}

    def set_storage(self, session, field, value, label):
        if field not in STORAGE_FIELDS:
            raise ValueError('Storage field outside CSV workflow')
        value = safe_value(value)
        self._write(session, label, ':STORage:SAVE:' + field + ' ' + value)
        got = self.q(session, label + '.check', ':STORage:SAVE:' + field + '?')
        equal = got == value if field == 'FILename' else got.upper() == value.upper()
        if not equal:
            raise RuntimeError(label + ': storage readback mismatch')

    def _write(self, session, label, command):
        self.serial += 1
        result = session.write_command(f'W{self.serial:04d}.{label}', command)
        if result['status'] != 'write_complete_unconfirmed':
            raise RuntimeError(label + ': write result uncertain; do not resend save')

    def start_save_once(self, session):
        marker = self.output / 'save-intent.json'
        with marker.open('x', encoding='utf-8') as stream:
            json.dump({'at': timestamp(), 'maximum_save_actions': 1,
                       'warning': 'This records intent, not proof the device received/completed SAVE.'}, stream)
        self._write(session, 'save_once', ':STORage:SAVE:START')


class ReadOnlyFTP(FTP):
    def __init__(self, runtime):
        super().__init__(timeout=10)
        self.runtime = runtime

    def putcmd(self, line):
        verb = line.split(' ', 1)[0].upper()
        if '\r' in line or '\n' in line or verb not in {'USER', 'PASS', 'TYPE', 'PASV', 'EPSV', 'NLST', 'SIZE', 'RETR', 'QUIT'}:
            raise RuntimeError('FTP command outside read-only workflow')
        self.runtime.event('ftp_command', command='PASS <redacted>' if verb == 'PASS' else line)
        return super().putcmd(line)

    def getresp(self):
        try:
            response = super().getresp()
        except Exception as error:
            self.runtime.event('ftp_response_error', error_type=type(error).__name__, message=str(error))
            raise
        self.runtime.event('ftp_response', response=response)
        return response

    def __exit__(self, *args):
        # FTP.__exit__ silently discards QUIT EOF/timeout errors. Preserve them
        # without masking an earlier save/download failure; final status uses them.
        if self.sock is not None:
            try:
                self.quit()
            except Exception as error:
                self.runtime.event('ftp_cleanup_problem', stage='QUIT',
                                   error_type=type(error).__name__, message=str(error))
            finally:
                try:
                    self.close()
                except Exception as error:
                    self.runtime.event('ftp_cleanup_problem', stage='close',
                                       error_type=type(error).__name__, message=str(error))
