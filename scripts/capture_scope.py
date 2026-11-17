"""Capture the current MHO5004 screen and maintain a local screenshot archive."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
from ftplib import FTP
from hashlib import sha256
from html import escape
import io
import json
from pathlib import Path
from string import Template
import sys
from urllib.parse import quote

sys.dont_write_bytecode = True
from scope_paths import external_output
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TIME = timezone(timedelta(hours=8))


def now():
    return datetime.now(LOCAL_TIME)


def write_atomic(path, text, encoding='utf-8'):
    external_output(path.parent)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('x', encoding=encoding) as stream:
        stream.write(text)
    temporary.replace(path)


def rebuild_index(archive):
    """Index verified captures, including the earlier ad hoc captures."""
    archive = external_output(archive)
    archive.mkdir(parents=True, exist_ok=True)
    rows = []
    historical_files = set()
    for metadata_path in archive.glob('*/capture_info.json'):
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            picture = (metadata_path.parent / metadata['file_evidence']).resolve()
            picture.relative_to(metadata_path.parent.resolve())
            data = picture.read_bytes()
            digest = sha256(data).hexdigest()
            if digest != metadata['file_sha256']:
                raise ValueError('SHA-256 mismatch')
            with Image.open(picture) as image:
                image.verify()
            with Image.open(picture) as image:
                if image.format != 'PNG':
                    raise ValueError('Expected PNG')
                width, height = image.size
            captured_at = metadata['captured_at']
            parsed = datetime.fromisoformat(captured_at)
            if parsed.tzinfo is None:
                raise ValueError('Capture time must include timezone')
            rows.append({
                'captured_at': captured_at,
                'file': picture.relative_to(archive.resolve()).as_posix(),
                'width': width, 'height': height, 'bytes': len(data),
                'sha256': digest, 'identity': metadata['identity'],
            })
            if metadata.get('historical_example') is True:
                historical_files.add(rows[-1]['file'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f'WARNING: skipped {metadata_path}: {error}', file=sys.stderr)
    rows.sort(key=lambda row: datetime.fromisoformat(row['captured_at']), reverse=True)
    buffer = io.StringIO(newline='')
    fields = ['captured_at', 'file', 'width', 'height', 'bytes', 'sha256', 'identity']
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    write_atomic(archive / 'index.csv', buffer.getvalue(), 'utf-8-sig')
    cards = []
    for row in rows:
        href = quote(row['file'], safe='/')
        info = quote(str(Path(row['file']).parent / 'capture_info.json').replace('\\', '/'), safe='/')
        label = escape(datetime.fromisoformat(row['captured_at']).astimezone(LOCAL_TIME).strftime('%Y-%m-%d %H:%M:%S'))
        history_notice = '<p>历史截图示例，不是当前现场采集。</p>' if row['file'] in historical_files else ''
        cards.append(
            f'<article>{history_notice}<a href="{href}"><img loading="lazy" src="{href}" alt="{label} 波形截图"></a>'
            f'<h2><a href="{href}">{label}</a></h2>'
            f'<p>{row["width"]} × {row["height"]} · {row["bytes"] / 1024:.1f} KB</p>'
            f'<p>{escape(row["identity"])}</p><a href="{info}">采集记录</a></article>'
        )
    template = Template((ROOT / 'assets/screenshot-gallery.html.tpl').read_text(encoding='utf-8'))
    html = template.substitute(total=len(rows), cards=''.join(cards),
                               empty_notice='' if rows else '<p>暂无通过文件校验的截图。</p>')
    write_atomic(archive / 'index.html', html)
    return len(rows)


def capture_screen(archive, host=None, ftp_port=None):
    archive = external_output(archive)
    import scope_transport as transport
    import scope_capture as capture

    target_host = transport.require_host(host)
    target_port = ftp_port or transport.CONFIG['file_access']['ftp_port']
    transport.CONFIG['connection']['host'] = target_host
    archive.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime('%Y%m%d_%H%M%S_%f')
    out = archive / stamp
    out.mkdir(exist_ok=False)
    (out / 'device-files').mkdir()
    transport.RUN = capture.RUN = out
    stage = 'FTP connection'
    result = None
    try:
        with FTP(timeout=10) as ftp:
            ftp.connect(target_host, target_port)
            ftp.login()
            stage = 'instrument identity'
            with transport.Session() as session:
                identity = session.query('screenshot_identity', '*IDN?')
                if (identity['status'] != 'response_complete'
                        or not identity.get('normalized', '').startswith('Micsig,MHO5004,')):
                    raise RuntimeError('Unexpected instrument: ' + repr(identity))
                stage = 'screen capture and FTP download'
                result = capture.capture_one(ftp, session, 'current_waveform')
                captured_at = now().isoformat()
            stage = 'local archive'
            picture = out / ('MHO5004_waveform_' + stamp + '.png')
            # A unique directory plus exclusive creation protects previous screenshots.
            source = out / result['file_evidence']
            with picture.open('xb') as stream:
                stream.write(source.read_bytes())
            source.unlink()
            result.update(file_evidence=picture.name, identity=identity['normalized'],
                          captured_at=captured_at, host=target_host, ftp_port=target_port)
            write_atomic(out / 'capture_info.json', json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    except Exception as error:
        failure = {'status': 'failed', 'failed_at': now().isoformat(),
                   'stage': stage, 'host': target_host, 'ftp_port': target_port,
                   'error': f'{type(error).__name__}: {error}',
                   'capture_result': result}
        write_atomic(out / 'failure.json', json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
        raise RuntimeError(f'{stage}: {error}. Evidence: {out}. No automatic capture retry.') from error
    return picture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True,
                        help='Absolute task screenshot archive outside the Skill')
    parser.add_argument('--host', help='Confirmed instrument address; required for live capture, unused by --reindex')
    parser.add_argument('--ftp-port', type=int, help='Override FTP port (normally 3721)')
    parser.add_argument('--reindex', action='store_true', help='Rebuild archive indexes offline; no capture')
    args = parser.parse_args()
    if args.ftp_port is not None and not 1 <= args.ftp_port <= 65535:
        parser.error('--ftp-port must be between 1 and 65535')
    try:
        archive = external_output(args.output_root)
    except ValueError as error:
        parser.error(str(error))
    picture = None
    try:
        if not args.reindex:
            picture = capture_screen(archive, args.host, args.ftp_port)
            print(f'SAVED: {picture}', flush=True)
        count = rebuild_index(archive)
        print(f'INDEX: {archive / "index.html"} ({count} screenshots)', flush=True)
        return 0
    except Exception as error:
        if picture:
            print(f'ERROR: screenshot saved at {picture}, but index update failed: {error}', file=sys.stderr)
        else:
            print(f'ERROR: {error}', file=sys.stderr)
            print('Check instrument LAN and FTP file service (normally port 3721).', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
