"""Validate actual MHO5004 CSV exports and plot their native time/voltage columns."""
from array import array
from bisect import bisect_left, bisect_right
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import json
import math
from pathlib import Path
from string import Template
from scope_paths import external_output

MAX_BYTES = 64 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'


@dataclass
class CSVRecord:
    info: dict
    times: array
    volts: array


def _resolution(text):
    """Half a last printed decimal place, not an empirical spacing correction."""
    try:
        return 0.5 * 10.0 ** Decimal(text).as_tuple().exponent
    except (InvalidOperation, OverflowError, TypeError) as error:
        raise ValueError('Invalid timestamp precision') from error


def read_csv(path, expected_source=None):
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('CSV exceeds the 64 MiB input bound')
    raw = path.read_bytes()
    if not raw.endswith(b'\n'):
        raise ValueError('CSV has no final newline; completeness not established')
    reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
    metadata = {}
    for row in reader:
        if row and row[0] == 'TIME':
            if len(row) < 2 or any(row[2:]):
                raise ValueError('Expected exactly one CSV source column')
            source = row[1]
            break
        if row and row[0]:
            if len(row) < 2 or row[0] in metadata:
                raise ValueError('Malformed or duplicated CSV metadata')
            metadata[row[0]] = row[1]
    else:
        raise ValueError('No TIME/source header')
    if source not in ('CH1', 'CH2') or metadata.get('Source') != source:
        raise ValueError('CSV source metadata/header mismatch or unsupported source')
    if expected_source is not None and source != expected_source:
        raise ValueError('CSV source does not match requested source')
    if not metadata.get('Model', '').startswith('MHO5004/'):
        raise ValueError('Unsupported CSV model')
    if metadata.get('Horizontal Units') != 's' or metadata.get('Vertical Units') != 'V':
        raise ValueError('Expected CSV time in seconds and voltage in volts')
    try:
        declared_rows = int(metadata['Record Length'])
        interval = float(metadata['Sample Interval'])
    except (KeyError, ValueError) as error:
        raise ValueError('Missing/invalid Record Length or Sample Interval') from error
    if declared_rows < 2 or not math.isfinite(interval) or interval <= 0:
        raise ValueError('Invalid Record Length or positive sample interval')
    times, volts = array('d'), array('d')
    previous, previous_rounding = None, None
    min_dt, max_dt, max_grid_error = math.inf, 0.0, 0.0
    first_rounding = None
    for row in reader:
        if not row or not any(row):
            continue
        if len(row) < 2 or any(row[2:]):
            raise ValueError('Unexpected CSV sample columns')
        try:
            t, v = float(row[0]), float(row[1])
        except ValueError as error:
            raise ValueError('Invalid CSV sample number') from error
        if not math.isfinite(t) or not math.isfinite(v):
            raise ValueError('CSV samples must be finite')
        rounding = _resolution(row[0])
        if previous is None:
            first_rounding = rounding
        else:
            delta = t - previous
            if delta <= 0:
                raise ValueError('CSV time is not strictly increasing')
            min_dt, max_dt = min(min_dt, delta), max(max_dt, delta)
            arithmetic_error = 8 * max(math.ulp(t), math.ulp(previous), math.ulp(interval))
            if abs(delta - interval) > rounding + previous_rounding + arithmetic_error:
                raise ValueError('CSV sample interval differs from header beyond printed precision')
            predicted = times[0] + len(times) * interval
            grid_error = abs(t - predicted)
            max_grid_error = max(max_grid_error, grid_error)
            if grid_error > rounding + first_rounding + 8 * max(math.ulp(t), math.ulp(predicted)):
                raise ValueError('CSV time grid differs from header interval')
        times.append(t)
        volts.append(v)
        previous, previous_rounding = t, rounding
    if len(times) != declared_rows:
        raise ValueError(f'Record Length {declared_rows} differs from actual rows {len(times)}')
    info = {
        'path': str(path), 'bytes': len(raw), 'sha256': sha256(raw).hexdigest(),
        'source': source, 'metadata': metadata, 'rows': len(times),
        'sample_interval_s': interval, 'observed_interval_min_s': min_dt,
        'observed_interval_max_s': max_dt, 'max_time_grid_error_s': max_grid_error,
        'interval_check': 'Header spacing within decimal timestamp rounding and floating arithmetic bounds',
        'first_time_s': times[0], 'last_time_s': times[-1],
        'voltage_min_V': min(volts), 'voltage_max_V': max(volts),
        'complete_export_file': True,
        'scope': 'Validated CSV export file; total acquisition memory and absence of decimation are not established.',
        'voltage_policy': 'Use CSV voltage directly; do not reapply probe attenuation or vertical offset.',
    }
    return CSVRecord(info, times, volts)


def select_window(record, start_s=None, end_s=None):
    lo = record.times[0] if start_s is None else start_s
    hi = record.times[-1] if end_s is None else end_s
    if not all(math.isfinite(x) for x in (lo, hi)) or lo > hi:
        raise ValueError('Window bounds must be finite and ordered')
    first = bisect_left(record.times, lo)
    end = bisect_right(record.times, hi)
    if first == end:
        raise ValueError('No CSV samples inside the requested time window')
    info = {'source': record.info['source'], 'parent_sha256': record.info['sha256'],
            'requested_start_s': start_s, 'requested_end_s': end_s,
            'selected_rows': end - first, 'first_sample_index_1based': first + 1,
            'last_sample_index_1based': end, 'first_time_s': record.times[first],
            'last_time_s': record.times[end - 1], 'time_zero_shift_s': 0,
            'bounds': 'Inclusive comparison on the original CSV time column'}
    return CSVRecord(info, record.times[first:end], record.volts[first:end])


def plot_png(record, path):
    external_output(Path(path).parent)
    from PIL import Image, ImageDraw, ImageFont
    style_path = ASSETS / 'waveform-style.json'
    style_bytes = style_path.read_bytes()
    style = json.loads(style_bytes)
    canvas, colors, fonts, lines = (style[name] for name in ('canvas', 'colors', 'fonts', 'lines'))
    image = Image.new('RGB', (canvas['width'], canvas['height']), colors['background'])
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(fonts['family'], fonts['regular_size'])
        title_font = ImageFont.truetype(fonts['family'], fonts['title_size'])
    except OSError:
        font = ImageFont.load_default(size=fonts['regular_size'])
        title_font = ImageFont.load_default(size=fonts['title_size'])
    left, top, right, bottom = canvas['plot_box']
    t0, t1 = record.times[0], record.times[-1]
    v0, v1 = min(record.volts), max(record.volts)
    pad = (v1 - v0) * 0.08 if v1 > v0 else max(abs(v0) * 0.05, 0.05)
    v0, v1 = v0 - pad, v1 + pad
    axis0, axis1 = (t0, t1) if t1 > t0 else (t0 - 0.5, t1 + 0.5)
    extent = max(abs(axis0), abs(axis1), axis1 - axis0)
    factor, unit = (1e9, 'ns') if extent < 1e-6 else ((1e6, 'us') if extent < 1e-3 else ((1e3, 'ms') if extent < 1 else (1, 's')))
    divisions = style['ticks']['divisions']
    if not isinstance(divisions, int) or not 1 <= divisions <= 50:
        raise ValueError('Plot grid divisions must be an integer in 1..50')
    digits = style['ticks']['significant_digits']
    for i in range(divisions + 1):
        x = left + (right - left) * i / divisions
        y = bottom - (bottom - top) * i / divisions
        draw.line([(x, top), (x, bottom)], fill=colors['grid'], width=lines['grid_width'])
        draw.line([(left, y), (right, y)], fill=colors['grid'], width=lines['grid_width'])
        draw.text((x, bottom + 15), f'{(axis0 + (axis1-axis0)*i/divisions)*factor:.{digits}g}', font=font, fill=colors['text'], anchor='mt')
        draw.text((left - 12, y), f'{v0+(v1-v0)*i/divisions:.{digits}g}', font=font, fill=colors['text'], anchor='rm')
    points = [(left + (t-axis0)/(axis1-axis0)*(right-left), bottom - (v-v0)/(v1-v0)*(bottom-top))
              for t, v in zip(record.times, record.volts)]
    if len(points) > 1:
        draw.line(points, fill=colors['waveform'], width=lines['waveform_width'])
    else:
        x, y = points[0]
        draw.ellipse((x-3, y-3, x+3, y+3), fill=colors['waveform'])
    labels = style['labels']
    draw.rectangle((left, top, right, bottom), outline=colors['axes'], width=lines['axes_width'])
    draw.text(tuple(canvas['title_xy']), labels['title'].format(source=record.info['source']), font=title_font, fill=colors['title'])
    draw.text(tuple(canvas['voltage_label_xy']), labels['voltage'], font=font, fill=colors['text'])
    draw.text(((left+right)/2, canvas['time_label_y']), labels['time'].format(unit=unit), font=font, fill=colors['text'], anchor='mt')
    draw.text(tuple(canvas['footer_xy']), labels['footer'].format(count=len(points)), font=font, fill=colors['footer'])
    with Path(path).open('xb') as stream:
        image.save(stream, format='PNG')
    return {'path': 'assets/waveform-style.json', 'sha256': sha256(style_bytes).hexdigest()}


def write_summary_table(record, selection, output):
    output = external_output(output)
    template_path = ASSETS / 'data-summary.md.tpl'
    template_bytes = template_path.read_bytes()
    original, chosen = record.info, selection.info

    def number(value):
        return format(value, '.12g')

    model = original['metadata']['Model'].replace('\\', '\\\\').replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')
    text = Template(template_bytes.decode('utf-8')).substitute(
        model=model, source=original['source'], original_rows=f"{original['rows']:,}",
        sample_interval=number(original['sample_interval_s']),
        original_start=number(original['first_time_s']), original_end=number(original['last_time_s']),
        original_vmin=number(original['voltage_min_V']), original_vmax=number(original['voltage_max_V']),
        original_sha256=original['sha256'],
        requested_start='未指定（按文件起点）' if chosen['requested_start_s'] is None else number(chosen['requested_start_s']),
        requested_end='未指定（按文件终点）' if chosen['requested_end_s'] is None else number(chosen['requested_end_s']),
        selected_start=number(chosen['first_time_s']), selected_end=number(chosen['last_time_s']),
        selected_rows=f"{chosen['selected_rows']:,}", first_index=f"{chosen['first_sample_index_1based']:,}",
        last_index=f"{chosen['last_sample_index_1based']:,}",
        selected_vmin=number(min(selection.volts)), selected_vmax=number(max(selection.volts)))
    with (Path(output) / 'summary.md').open('x', encoding='utf-8') as stream:
        stream.write(text)
    return {'path': 'assets/data-summary.md.tpl', 'sha256': sha256(template_bytes).hexdigest()}


def write_analysis(record, output, start_s=None, end_s=None):
    output = external_output(output)
    selection = select_window(record, start_s, end_s)
    with (output / 'window.csv').open('x', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['TIME', record.info['source']])
        writer.writerows(zip(selection.times, selection.volts))
    plot_template = plot_png(selection, output / 'waveform.png')
    table_template = write_summary_table(record, selection, output)
    info = {'original': record.info, 'selection': selection.info,
            'derived_csv': 'window.csv (data columns only; original metadata remains in the source and summary)',
            'plot': {'file': 'waveform.png', 'samples_plotted': len(selection.times), 'time_zero_shift_s': 0},
            'templates': {'waveform_style': plot_template, 'data_summary': table_template},
            'artifacts': {name: {'bytes': (output/name).stat().st_size,
                                 'sha256': sha256((output/name).read_bytes()).hexdigest()}
                          for name in ('window.csv', 'waveform.png', 'summary.md')}}
    with (output / 'summary.json').open('x', encoding='utf-8') as stream:
        json.dump(info, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return info
