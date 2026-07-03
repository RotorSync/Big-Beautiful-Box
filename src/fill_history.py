"""Fill-history log line parsing, shared by the BLE server (rotorsync_bumble)
and the WiFi server (rotorlink.config_handler).

One parser for /home/pi/fill_history.log so the two transports can never drift
apart again — the WiFi copy once lost the Pilot/FlowStart/FlowEnd fields and
loads fetched over WiFi reached the backend without attribution.

Compact keys are the wire contract with the iOS app:
  t/rq/ag/df/st/tf/s2t/fs/fe/pl and (new) lat/lon/lac for the pilot's location
  at fill time, written by the dashboard as `| Loc: <lat>,<lon>[,<acc>]`.
"""
import time


def history_timestamp_epoch(value):
    try:
        return int(time.mktime(time.strptime(value, '%Y-%m-%d %H:%M:%S')))
    except (TypeError, ValueError):
        return None


def named_field(parts, name):
    prefix = f'{name}:'
    for part in parts:
        text = part.strip()
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return ''


def parse_float_token(value):
    try:
        cleaned = (
            str(value)
            .replace('gal', '')
            .replace('GPM', '')
            .replace('F', '')
            .replace('s', '')
            .strip()
        )
        if not cleaned:
            return None
        return round(float(cleaned), 3)
    except (TypeError, ValueError):
        return None


def _loc_fields(parts):
    """`Loc: 41.123456,-95.654321[,12.5]` -> (lat, lon, acc) or (None,)*3."""
    raw = named_field(parts, 'Loc')
    if not raw:
        return None, None, None
    tokens = [t.strip() for t in raw.split(',')]
    if len(tokens) < 2:
        return None, None, None
    try:
        lat = round(float(tokens[0]), 6)
        lon = round(float(tokens[1]), 6)
    except (TypeError, ValueError):
        return None, None, None
    acc = None
    if len(tokens) >= 3:
        try:
            acc = round(float(tokens[2]), 1)
        except (TypeError, ValueError):
            acc = None
    return lat, lon, acc


def item_from_line(line):
    parts = line.strip().split('|')
    if len(parts) < 3:
        return None

    timestamp = history_timestamp_epoch(parts[0].strip())
    if timestamp is None:
        return None

    requested = parse_float_token(named_field(parts, 'Requested'))
    actual = parse_float_token(named_field(parts, 'Actual'))
    if requested is None or actual is None:
        return None

    shutoff_type = ''
    for part in parts[3:]:
        text = part.strip()
        if text.lower().startswith(('auto', 'manual')):
            shutoff_type = text
            break

    lat, lon, acc = _loc_fields(parts)
    return {
        't': timestamp,
        'rq': requested,
        'ag': actual,
        'df': round(actual - requested, 3),
        'st': shutoff_type,
        'tf': parse_float_token(named_field(parts, 'Temp')),
        's2t': parse_float_token(named_field(parts, 'StopToThumb')),
        # Flow window epochs (None when the box didn't record them — the app
        # flags such records loudly). history_timestamp_epoch('') returns None.
        'fs': history_timestamp_epoch(named_field(parts, 'FlowStart')),
        'fe': history_timestamp_epoch(named_field(parts, 'FlowEnd')),
        'pl': named_field(parts, 'Pilot').strip() or None,
        'lat': lat,
        'lon': lon,
        'lac': acc,
    }
