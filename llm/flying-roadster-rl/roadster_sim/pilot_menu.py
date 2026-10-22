"""A discrete action menu over the pilot modes, for decision models that pick
from named options (e.g. Jev's typed `choice` questions) instead of writing
free-form JSON. Each decision is two picks: a manoeuvre and a drift correction.

    action = menu_action('kickflip', 'none')   # -> a pilot.py action dict
"""
from typing import Dict

DRIFT_ROLL = 8.0  # deg of roll trim; positive roll pushes the car toward -y

MANOEUVRES = {
    'coast': ('All thrusters off; the car flies ballistically.',
              {'mode': 'coast'}),
    'climb': ('Stay level and climb at 7 m/s.',
              {'mode': 'climb', 'vz': 7.0}),
    'hover': ('Stay level and hold the current height (vertical speed 0).',
              {'mode': 'hold', 'roll': 0.0, 'pitch': 0.0, 'vz': 0.0}),
    'roll_upright': ('Roll until the total roll (`rolled`) reaches +180 deg, braking to stop there, '
                     'no extra lift. Turns a car that started inverted back upright. Keep choosing it '
                     'until the roll has finished.',
                     {'mode': 'rotate', 'axis': 'roll', 'to': 180.0, 'lift': 0.0}),
    'kickflip': ('Full roll until the total roll (`rolled`) reaches +360 deg, braking to stop there, '
                 'with extra lift while upright. Keep choosing it until the roll has finished.',
                 {'mode': 'rotate', 'axis': 'roll', 'to': 360.0, 'lift': 1.1}),
    'land': ('Level the car and fly a late-braking descent to a soft touchdown.',
             {'mode': 'land', 'roll': 0.0}),
}
DRIFT = {
    'none': 'No sideways correction.',
    'toward_plus_y': 'Lean so the car drifts toward +y (use when y or vy is negative and growing).',
    'toward_minus_y': 'Lean so the car drifts toward -y (use when y or vy is positive and growing).',
}
_TRIM = {'none': 0.0, 'toward_plus_y': -DRIFT_ROLL, 'toward_minus_y': DRIFT_ROLL}


def menu_action(manoeuvre: str, drift: str = 'none') -> Dict:
    """Menu picks -> action dict. Drift only trims `hover` and `land`."""
    a = dict(MANOEUVRES[manoeuvre][1])
    if a['mode'] in ('hold', 'land'):
        a['roll'] = _TRIM.get(drift, 0.0)
    return a


def menu_text() -> str:
    """The menu as prompt text, for chat models flying the same interface."""
    lines = ['Manoeuvres:'] + [f'- `{k}`: {d}' for k, (d, _) in MANOEUVRES.items()]
    lines += ['Drift correction (applies to `hover` and `land`):'] + [f'- `{k}`: {d}' for k, d in DRIFT.items()]
    return '\n'.join(lines)
