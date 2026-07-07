"""Pure math for the remote-driven Mopeka tank calibration wizard.

Two modes (spec'd by the operator 2026-07-07):

FULL ("from scratch", tank starts EMPTY):
  * step = total_capacity / points; cumulative fill targets step, 2*step, ...
    stopping ONE STEP EARLY (the brim-full point is skipped so the tank can't
    run over). 300 gal / 10 points -> fills at 30, 60 ... 270.
  * The empty (0 gal) reading is recorded separately as the first curve point
    before any pumping (the state machine's confirm_empty phase).

OFFSET (correct an existing curve):
  * User picks how many points and the most water to pump (max_gallons).
    step = max_gallons / points; targets step ... max_gallons (max is safe by
    definition — the user chose it).
  * At each point (including the empty start) the measured, offset-compensated
    level (inches) is compared to what the EXISTING curve says the level
    should be at that many gallons; the differences average into a new
    per-sensor Height Offset (inches). The curve itself is never edited.

All functions are pure so they can be unit-tested headless; the dashboard's
calibration state machine calls them.
"""


def compute_point_targets(mode, total_capacity=0.0, points=0, max_gallons=0.0):
    """Cumulative pump-shutoff targets (gallons) for a calibration run.

    Returns a list of floats. Raises ValueError on unusable parameters. The
    empty (0 gal) reading is NOT in the list — the state machine records it
    at confirm_empty before the first fill.
    """
    if mode == 'full':
        total = float(total_capacity)
        n = int(points)
        if total <= 0:
            raise ValueError('tank size must be positive')
        if n < 2:
            raise ValueError('need at least 2 calibration points')
        step = total / n
        # Stop one step early: never command a fill to the brim.
        return [round(step * i, 3) for i in range(1, n)]

    if mode == 'offset':
        top = float(max_gallons)
        n = int(points)
        if top <= 0:
            raise ValueError('max gallons must be positive')
        if n < 1:
            raise ValueError('need at least 1 offset point')
        step = top / n
        return [round(step * i, 3) for i in range(1, n + 1)]

    raise ValueError(f'unknown calibration mode {mode!r}')


def expected_level_in(table_rows, gallons):
    """Invert a calibration table: the level (inches from sensor, i.e. from
    the top — larger = emptier) the curve expects at `gallons`.

    table_rows: [(tank_level_in, gallons), ...] in any order. Piecewise-linear
    between the two rows bracketing `gallons`; clamps to the table's ends.
    Raises ValueError on a table with fewer than 2 rows.
    """
    rows = sorted(((float(g), float(lvl)) for lvl, g in table_rows),
                  key=lambda r: r[0])
    if len(rows) < 2:
        raise ValueError('calibration table needs at least 2 points')

    g = float(gallons)
    if g <= rows[0][0]:
        return rows[0][1]
    if g >= rows[-1][0]:
        return rows[-1][1]
    for (g0, l0), (g1, l1) in zip(rows, rows[1:]):
        if g0 <= g <= g1:
            if g1 == g0:
                return l0
            frac = (g - g0) / (g1 - g0)
            return l0 + frac * (l1 - l0)
    return rows[-1][1]  # unreachable, defensive


def offset_adjustment_inches(diffs):
    """Average the per-point (expected − measured) inch differences into the
    Height Offset ADJUSTMENT to add to the sensor's current offset.

    Each diff is expected_level_in − measured_compensated_level_in at the same
    gallons. The measured level already includes the current offset, so the
    average is the correction to ADD to it. Empty list raises ValueError.
    """
    cleaned = [float(d) for d in diffs]
    if not cleaned:
        raise ValueError('no offset points recorded')
    return round(sum(cleaned) / len(cleaned), 3)
