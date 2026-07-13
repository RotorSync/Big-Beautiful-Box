"""Shared identity rules for Mopeka history written by the BLE service.

Mopeka history lives on a box whose trailer assignment can change.  Identity
therefore belongs to every new row; a timestamp and tank values alone are not
enough to decide which trailer owns a reading.
"""

import csv
import json
import os
from pathlib import Path
import re
import tempfile


MOPEKA_HISTORY_LEGACY_FIELDNAMES = (
    "timestamp",
    "reason",
    "front_gal",
    "back_gal",
    "front_mm",
    "back_mm",
    "front_in",
    "back_in",
    "front_quality",
    "back_quality",
)

MOPEKA_HISTORY_IDENTITY_FIELDNAMES = (
    "trailer_id",
    "front_sensor_id",
    "back_sensor_id",
)

MOPEKA_HISTORY_FIELDNAMES = (
    *MOPEKA_HISTORY_LEGACY_FIELDNAMES,
    *MOPEKA_HISTORY_IDENTITY_FIELDNAMES,
)

_EMPTY_IDENTIFIERS = {
    "",
    "-",
    "---------------",
    "NONE",
    "NULL",
    "UNASSIGNED",
    "UNCONFIGURED",
}


def normalize_trailer_id(value):
    """Return a stable trailer key without interpreting display names."""
    text = str(value or "").strip()
    if text.upper() in _EMPTY_IDENTIFIERS:
        return ""

    numeric = re.fullmatch(r"(?:TR)?0*(\d+)", text, flags=re.IGNORECASE)
    if numeric:
        number = int(numeric.group(1))
        return str(number) if number > 0 else ""
    return text.upper()


def normalize_sensor_id(value):
    """Normalize a Mopeka suffix/MAC while retaining non-MAC stable IDs."""
    text = str(value or "").strip()
    if text.upper() in _EMPTY_IDENTIFIERS:
        return ""

    compact = re.sub(r"[:.\-\s]", "", text)
    if len(compact) in (6, 12) and re.fullmatch(r"[0-9A-Fa-f]+", compact):
        compact = compact.upper()
        return ":".join(
            compact[index:index + 2]
            for index in range(0, len(compact), 2)
        )
    return text.upper()


def normalize_history_identity(trailer_id, front_sensor_id, back_sensor_id):
    """Return ``(trailer, front, back)`` or ``None`` if nothing is known."""
    identity = (
        normalize_trailer_id(trailer_id),
        normalize_sensor_id(front_sensor_id),
        normalize_sensor_id(back_sensor_id),
    )
    return identity if any(identity) else None


def history_identity_from_config(cfg):
    """Build identity only from the durable, confirmed box configuration."""
    cfg = cfg if isinstance(cfg, dict) else {}
    trailer_id = cfg.get("assigned_trailer")
    if trailer_id is None:
        trailer_id = cfg.get("trailer")
    return normalize_history_identity(
        trailer_id,
        cfg.get("front_id"),
        cfg.get("back_id"),
    )


def history_identity_from_row(row):
    """Decode a scoped row; legacy rows with blank identity stay unscoped."""
    row = row if isinstance(row, dict) else {}
    return normalize_history_identity(
        row.get("trailer_id"),
        row.get("front_sensor_id"),
        row.get("back_sensor_id"),
    )


def history_identity_values(identity):
    """Return CSV identity values for an already-normalized identity."""
    if identity is None:
        return {name: "" for name in MOPEKA_HISTORY_IDENTITY_FIELDNAMES}
    normalized = normalize_history_identity(*identity)
    if normalized is None:
        return {name: "" for name in MOPEKA_HISTORY_IDENTITY_FIELDNAMES}
    return dict(zip(MOPEKA_HISTORY_IDENTITY_FIELDNAMES, normalized))


def history_identity_token(identity):
    """Return a stable wire token for comparing cache ownership across processes."""
    if identity is None:
        return None
    normalized = normalize_history_identity(*identity)
    if normalized is None:
        return None
    return json.dumps(normalized, separators=(",", ":"))


def normalize_history_identity_token(value):
    """Validate and canonicalize a trailer identity token from the wire."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, (list, tuple)) or len(decoded) != 3:
        return None
    return history_identity_token(tuple(decoded))


def filter_rows_for_current_identity(rows, current_identity):
    """Return history safe for the current assignment.

    Before the first identity-aware row is written, legacy-only history remains
    readable for backward compatibility.  As soon as any scoped row exists,
    legacy rows are quarantined and rows for other identities are excluded.
    """
    rows = list(rows)
    scoped_rows_exist = any(history_identity_from_row(row) for row in rows)
    if not scoped_rows_exist:
        return rows

    if current_identity is None:
        return []
    normalized_current = normalize_history_identity(*current_identity)
    if normalized_current is None:
        return []
    return [
        row
        for row in rows
        if history_identity_from_row(row) == normalized_current
    ]


def ensure_active_history_schema(path):
    """Add blank identity columns to a legacy active CSV atomically.

    Existing rows are preserved and deliberately receive empty identity cells:
    assigning them to the trailer that happens to be selected during upgrade
    would fabricate ownership.  Rotated files are intentionally not migrated.
    The returned field list can be used by ``csv.DictWriter`` for the append.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return list(MOPEKA_HISTORY_FIELDNAMES)

    with path.open(newline="") as source:
        reader = csv.reader(source)
        try:
            header = list(next(reader))
        except StopIteration:
            return list(MOPEKA_HISTORY_FIELDNAMES)
        missing = [
            name for name in MOPEKA_HISTORY_IDENTITY_FIELDNAMES
            if name not in header
        ]
        # Normal steady-state append: inspect one short header and leave the
        # potentially large history body unread. Full materialization is only
        # needed for the one-time legacy migration below.
        if not missing:
            return header
        rows = list(reader)

    original_width = len(header)
    extra_width = max(
        (max(0, len(existing_row) - original_width) for existing_row in rows),
        default=0,
    )
    extra_names = []
    for index in range(extra_width):
        candidate = f"legacy_extra_{index + 1}"
        while candidate in header or candidate in extra_names:
            candidate = f"_{candidate}"
        extra_names.append(candidate)

    migrated_rows = [header + extra_names + missing]
    for existing_row in rows:
        row = list(existing_row)
        migrated_width = original_width + extra_width
        if len(row) < migrated_width:
            row.extend([""] * (migrated_width - len(row)))
        row.extend([""] * len(missing))
        migrated_rows.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    original_stat = path.stat()
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary_path = destination.name
            writer = csv.writer(destination)
            writer.writerows(migrated_rows)
            destination.flush()
            os.fsync(destination.fileno())

        os.chmod(temporary_path, original_stat.st_mode)
        try:
            os.chown(temporary_path, original_stat.st_uid, original_stat.st_gid)
        except PermissionError:
            pass
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    return migrated_rows[0]
