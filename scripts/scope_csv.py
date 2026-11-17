"""Save/verify one scope CSV, or analyze an existing CSV without connecting to a scope."""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sys
import time

sys.dont_write_bytecode = True
from scope_paths import external_output
from scope_csv_data import MAX_BYTES, read_csv, write_analysis

DIRECTORIES = ('/smart/csvwave', '/smart/binwave', '/smart/refwave')


def new_output(path):
    path = external_output(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def analyze(path, output, source=None, start_s=None, end_s=None):
    output = external_output(output)
    record = read_csv(path, source)
    with Path(record.info['path']).open('rb') as original, (output / 'original.csv').open('xb') as archive:
        shutil.copyfileobj(original, archive)
    archived = read_csv(output / 'original.csv', record.info['source'])
    if archived.info['sha256'] != record.info['sha256']:
        raise RuntimeError('CSV changed while creating the local archive')
    record.info['archived_file'] = 'original.csv'
    return write_analysis(record, output, start_s, end_s)


def inventory(ftp):
    return {directory: sorted(ftp.nlst(directory)) for directory in DIRECTORIES}


def new_files(ftp, before, unique):
    current = inventory(ftp)
    result = {}
    for directory, names in current.items():
        old = {PurePosixPath(name).name for name in before[directory]}
        for item in names:
            name = PurePosixPath(item).name
            if name not in old and (name.startswith(unique + '_') or name.startswith(unique + '.')):
                if any(c in name for c in '\r\n'):
                    raise ValueError('Unsafe remote filename')
                remote = directory + '/' + name
                ftp.voidcmd('TYPE I')
                result[remote] = ftp.size(remote)
    return result


def download(ftp, remote, destination):
    ftp.voidcmd('TYPE I')
    before = ftp.size(remote)
    if before is None or not 0 < before <= MAX_BYTES:
        raise ValueError('Remote CSV size is invalid or exceeds 64 MiB')
    received = 0
    digest = sha256()
    with destination.open('xb') as stream:
        def receive(part):
            nonlocal received
            received += len(part)
            if received > MAX_BYTES:
                raise ValueError('Download exceeded 64 MiB; partial file retained')
            stream.write(part)
            digest.update(part)
        response = ftp.retrbinary('RETR ' + remote, receive, blocksize=65536)
    after = ftp.size(remote)
    if not response.startswith('226') or received != before or before != after:
        raise RuntimeError('FTP transfer/size did not prove a complete file')
    return {'remote_path': remote, 'local_file': destination.name, 'bytes': received,
            'sha256': digest.hexdigest(), 'size_before': before, 'size_after': after,
            'ftp_completion': response}


def export(args, output):
    # Lazy import: the analyze command has no transport imports or network path.
    from scope_runtime import Runtime, ReadOnlyFTP, STORAGE_FIELDS, dump, timestamp
    runtime = Runtime(output, args.host, args.ftp_port)
    result = {'started_at': timestamp(), 'source': args.source, 'errors': [], 'polls': [],
              'storage_changed': False, 'file_verified': False, 'storage_restored': False,
              'source_evidence': 'CH1 complete exports exist; CH2 full export is not yet hardware-verified.',
              'scope': 'One CSV export, not a new acquisition test or total acquisition-memory verification.'}
    original = None

    def persist():
        dump(output / 'export.json', result)

    persist()
    try:
        # Check file access before making changes to storage parameters.
        with ReadOnlyFTP(runtime) as ftp:
            ftp.connect(runtime.host, runtime.ftp_port)
            ftp.login()
            before = inventory(ftp)
            result['inventory_before'] = before
            persist()
            with runtime.session() as session:
                result['identity'] = runtime.identity(session)
                entry = result['entry'] = runtime.state(session, 'entry')
                persist()
                if entry['trigger_status'] != 'STOP' or entry['segmented'] != '0':
                    raise RuntimeError('CSV export requires current STOP and segmented acquisition off; no save attempted')
                channel = entry['channels'][args.source]
                if channel['display'] != '1' or channel['input'].upper() != 'MEGA':
                    raise RuntimeError('Requested CH1/CH2 must be visible with 1 MOhm input; no save attempted')
                original = result['storage_before'] = runtime.storage(session, 'storage_before')
                unique = 'C' + datetime.now().strftime('%m%d') + secrets.token_hex(3).upper()
                if any(unique in item for names in before.values() for item in names):
                    raise RuntimeError('Unique-name collision; no save attempted')
                result['unique_name'] = unique
                result['storage_changed'] = True
                persist()
                for field, value in [('SOURce', args.source), ('LOCAtion', 'Local'),
                                     ('ALLSegments', '0'), ('TYPE', 'CSV'), ('FILename', unique)]:
                    runtime.set_storage(session, field, value, 'prepare.' + field)
                result['storage_before_save'] = runtime.storage(session, 'save_settings')
                if runtime.q(session, 'before_save.state', ':TRIGger:STATus?') != 'STOP':
                    raise RuntimeError('Scope no longer STOP; no save attempted')
                persist()
                started = time.monotonic()
                result['save_intent_at'] = timestamp()
                persist()
                try:
                    runtime.start_save_once(session)
                    result['save_write_completed_at'] = timestamp()
                except Exception as error:
                    # The SAVE may have reached the device. Only observe files; never resend.
                    result['errors'].append('SAVE acknowledgement uncertain: ' + repr(error))
                persist()
            previous, stable = None, 0
            candidate = None
            while time.monotonic() - started < args.timeout:
                current = new_files(ftp, before, unique)
                elapsed = time.monotonic() - started
                stable = stable + 1 if current and current == previous else 0
                previous = current
                result['polls'].append({'at': timestamp(), 'elapsed_s': elapsed, 'files': current})
                persist()
                candidates = [p for p, size in current.items() if p.lower().endswith('.csv') and size and size > 400]
                if elapsed >= 18 and stable >= 2 and len(candidates) == 1:
                    candidate = candidates[0]
                    break
                time.sleep(1.5)
            result['new_files'] = new_files(ftp, before, unique)
            persist()
            if candidate is None:
                raise RuntimeError('No unique stable CSV in the bounded observation; do not repeat SAVE automatically')
            local = output / 'original.csv'
            result['download_intent'] = {'at': timestamp(), 'remote_path': candidate, 'local_file': local.name}
            persist()
            result['download'] = download(ftp, candidate, local)
            persist()
            record = read_csv(local, args.source)
            if record.info['sha256'] != result['download']['sha256']:
                raise RuntimeError('Local file differs from downloaded bytes')
            if record.info['metadata']['Model'] != 'MHO5004/' + result['identity'].split(',')[2]:
                raise RuntimeError('CSV model/serial do not match the connected instrument')
            result['csv'] = record.info
            result['file_verified'] = True
            result['inventory_after'] = inventory(ftp)
            persist()
            write_analysis(record, output)
    except Exception as error:
        result['errors'].append(repr(error))
        persist()
    finally:
        if original is not None and result['storage_changed']:
            # New identified session; independently attempt every storage restoration.
            try:
                with runtime.session() as session:
                    if runtime.identity(session) != result['identity']:
                        raise RuntimeError('Restore identity differs; no restoration writes')
                    for field in STORAGE_FIELDS:
                        try:
                            runtime.set_storage(session, field, original[field], 'restore.' + field)
                        except Exception as error:
                            result['errors'].append('restore.' + field + ': ' + repr(error))
                    final_storage = result['storage_final'] = runtime.storage(session, 'storage_final')
                    result['storage_restored'] = final_storage == original
                    final = result['final'] = runtime.state(session, 'final')
                    result['observed_state_unchanged'] = final == result['entry']
                    if not result['observed_state_unchanged'] or not result['storage_restored']:
                        result['errors'].append('Final state/storage differ from entry; inspect export.json')
            except Exception as error:
                result['errors'].append('restoration/final check: ' + repr(error))
        result['cleanup_errors'] = runtime.cleanup_errors
        if runtime.cleanup_errors:
            result['errors'].append('Transport cleanup reported errors; inspect events.jsonl')
        result['finished_at'] = timestamp()
        result['success'] = result['file_verified'] and result['storage_restored'] and not result['errors']
        persist()
        if not result['success']:
            dump(output / 'failure.json', {'at': timestamp(), 'errors': result['errors'],
                 'details': 'export.json', 'warning': 'Inspect SAVE intent, remote files and evidence before any new save.'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    local = subs.add_parser('analyze', help='Validate/plot a saved CSV locally; no instrument connection')
    local.add_argument('csv', type=Path)
    local.add_argument('--source', choices=('CH1', 'CH2'))
    local.add_argument('--start-s', type=float)
    local.add_argument('--end-s', type=float)
    live = subs.add_parser('save', help='One real SAVE + read-only FTP; current STOP required; leaves STOP')
    live.add_argument('--source', choices=('CH1', 'CH2'), default='CH1')
    live.add_argument('--host', help='Confirmed instrument address; required for live save')
    live.add_argument('--ftp-port', type=int)
    live.add_argument('--timeout', type=float, default=60, help='Bounded file observation, 20..300 seconds')
    for sub in (local, live):
        sub.add_argument('--output', type=Path, required=True,
                         help='Absolute new task directory outside the Skill; existing directories are refused')
    args = parser.parse_args()
    if args.action == 'save':
        if not 20 <= args.timeout <= 300:
            parser.error('--timeout must be 20..300 seconds')
        if args.ftp_port is not None and not 1 <= args.ftp_port <= 65535:
            parser.error('--ftp-port must be 1..65535')
    output = None
    try:
        output = new_output(args.output)
        if args.action == 'analyze':
            analyze(args.csv, output, args.source, args.start_s, args.end_s)
        elif not export(args, output)['success']:
            print(f'INCOMPLETE: {output}; inspect failure.json and export.json; no automatic retry', file=sys.stderr)
            return 1
        print(f'OUTPUT: {output}')
        return 0
    except Exception as error:
        if output:
            # No instrument runtime needed to preserve an early/local failure.
            with (output / 'failure.json').open('x', encoding='utf-8') as stream:
                json.dump({'error': repr(error), 'action': args.action}, stream, ensure_ascii=False, indent=2)
        print(f'ERROR: {error}; output: {output}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
