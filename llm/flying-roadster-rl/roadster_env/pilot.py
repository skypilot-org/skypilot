"""The LLM as the pilot: high-level decisions at a fixed rate, flown by the
flight library at 50 Hz in between.

Every ``DECIDE_EVERY`` control ticks (0.2 s of sim time) the pilot sees a short
text observation and returns one JSON action, e.g.

    {"mode": "rotate", "axis": "roll", "to": 180, "lift": 0.3}

The mode runs closed-loop at 50 Hz until the next decision, like an autopilot
mode on a real vehicle. Modes:

    coast                                    all thrusters off
    climb   vz (m/s)                         level, track a vertical speed
    hold    roll, pitch (deg), vz (m/s, opt) hold an attitude (+ vertical speed)
    rotate  axis roll|pitch, to (deg), lift  spin until the CUMULATIVE rotation on
                                             that axis reaches `to` (e.g. 180 / 360 /
                                             -360), braking to stop on it, then
                                             hold; lift = collective as a fraction
                                             of weight (0-1.5)
    land    roll (deg, opt)                  level (or trimmed) + late-braking
                                             descent profile to touch down softly

`to` is absolute (cumulative since t=0), so repeating the same rotate action
on the next decision continues the same manoeuvre instead of restarting it.
"""
import json
import os
import math
import re
from typing import Dict, Optional, Tuple

from . import flight_sdk as fc
from .tasks import CONTROL_HZ, episode

DECIDE_EVERY = 10                     # control ticks per decision
DECISION_DT = DECIDE_EVERY / CONTROL_HZ  # 0.2 s of sim time
MODES = ('coast', 'climb', 'hold', 'rotate', 'land')
_JSON_RE = re.compile(r'\{.*?\}', re.DOTALL)


class ActionError(ValueError):
    pass


def _num(a, key, default, lo, hi):
    v = a.get(key)
    if v is None:  # missing or JSON null -> the default (which may itself be None)
        v = default
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError) as e:
        raise ActionError(f'{key} must be a number') from e
    if not math.isfinite(v):
        raise ActionError(f'{key} must be finite')
    return max(lo, min(hi, v))


def parse_action(text: str) -> Dict:
    """Last JSON object in the reply -> a validated action dict."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    blobs = _JSON_RE.findall(text)
    if not blobs:
        raise ActionError('no JSON action found')
    try:
        a = json.loads(blobs[-1])
    except json.JSONDecodeError as e:
        raise ActionError(f'invalid JSON: {e.msg}') from e
    return normalize_action(a)


def normalize_action(a) -> Dict:
    """Validate an action dict, clamp its numbers and fill defaults."""
    if not isinstance(a, dict) or a.get('mode') not in MODES:
        raise ActionError(f'mode must be one of {MODES}')
    m = a['mode']
    out = {'mode': m}
    if m == 'climb':
        out['vz'] = _num(a, 'vz', 3.0, -12.0, 12.0)
    elif m == 'hold':
        out['roll'] = _num(a, 'roll', 0.0, -180.0, 180.0)
        out['pitch'] = _num(a, 'pitch', 0.0, -90.0, 90.0)
        out['vz'] = _num(a, 'vz', None, -12.0, 12.0)
    elif m == 'rotate':
        axis = a.get('axis', 'roll')
        if axis not in ('roll', 'pitch'):
            raise ActionError('axis must be roll or pitch')
        out['axis'] = axis
        out['to'] = _num(a, 'to', 360.0, -1080.0, 1080.0)
        out['lift'] = _num(a, 'lift', 0.0, 0.0, 1.5)
    elif m == 'land':
        out['roll'] = _num(a, 'roll', 0.0, -20.0, 20.0)
    return out


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def throttles(action: Dict, obs: Dict) -> list:
    """Execute one mode for one 50 Hz tick."""
    m = action['mode']
    weight = obs['mass'] * fc.G
    if m == 'coast':
        return [0.0] * 4
    if m == 'climb':
        tr, tp = fc.hold_attitude(obs)
        return fc.mix(fc.vz_thrust(obs, action['vz']), tr, tp, obs)
    if m == 'hold':
        tr, tp = fc.hold_attitude(obs, math.radians(action['roll']), math.radians(action['pitch']))
        total = fc.vz_thrust(obs, action['vz']) if action['vz'] is not None else 0.0
        return fc.mix(total, tr, tp, obs)
    if m == 'land':
        tr, tp = fc.hold_attitude(obs, math.radians(action['roll']), 0.0)
        return fc.mix(fc.vz_thrust(obs, fc.landing_vz(obs)), tr, tp, obs)
    # rotate: spin toward the cumulative target, coast at the rate cap, brake to
    # stop on it, then hold the final attitude.
    axis = action['axis']
    total = obs['roll_total'] if axis == 'roll' else obs['pitch_total']
    rate = obs['roll_rate'] if axis == 'roll' else obs['pitch_rate']
    rem = math.radians(action['to']) - total
    lift = action['lift'] * weight * max(obs['up_z'], 0.0)  # thrust only helps when upright-ish
    if abs(rem) < math.radians(4) and abs(rate) < 0.6:
        cur = obs['roll'] if axis == 'roll' else obs['pitch']
        tgt = _wrap(cur + rem)
        tr, tp = fc.hold_attitude(obs, tgt if axis == 'roll' else 0.0, tgt if axis == 'pitch' else 0.0)
        return fc.mix(lift, tr, tp, obs)
    direction = 1 if rem > 0 else -1
    moving_toward = rate * direction > 0
    if moving_toward and abs(rem) <= fc.brake_angle(obs, axis) + abs(rate) / CONTROL_HZ:
        return fc.spin(obs, axis, -direction, collective=lift)  # brake
    cap = 7.0 if axis == 'roll' else 3.5  # rad/s; faster just wastes air
    if moving_toward and abs(rate) >= cap:
        return fc.mix(lift, 0.0, 0.0, obs)  # coast at the cap
    return fc.spin(obs, axis, direction, collective=lift)


# ROADSTER_OBS_TIMING=0 drops the seconds-to-truck hints from the telemetry line.
OBS_TIMING = os.environ.get('ROADSTER_OBS_TIMING', '1') != '0'


def obs_text(obs: Dict, task: Dict, action: Optional[Dict] = None) -> str:
    """Compact, readable state for the pilot (degrees, %, metres)."""
    parts = [f't={obs["t"]:.1f}s', f'height={obs["z"]:.1f}m', f'vz={obs["vz"]:+.1f}',
             f'speed={obs["vx"]:.1f}', f'lateral y={obs["y"]:+.1f}m vy={obs["vy"]:+.1f}',
             f'roll={math.degrees(obs["roll"]):+.0f}deg', f'pitch={math.degrees(obs["pitch"]):+.0f}deg',
             f'roll_rate={math.degrees(obs["roll_rate"]):+.0f}deg/s',
             f'rolled={math.degrees(obs["roll_total"]):+.0f}deg',
             f'pitched={math.degrees(obs["pitch_total"]):+.0f}deg',
             f'air={100 * obs["air_frac"]:.0f}%', f'wheels_down={obs["wheels_on_ground"]}']
    if task.get('obstacle'):
        o = task['obstacle']
        d = o['x0'] - obs['x']
        timing = ''
        if OBS_TIMING and obs['vx'] > 0.5 and d + o['length'] > 0:
            # Seconds at the current speed: the division every pilot otherwise has to do in its head.
            timing = (f' (reach in {d / obs["vx"]:.1f}s)' if d > 0 else '') + \
                f', past it in {(d + o["length"]) / obs["vx"]:.1f}s'
        parts.append(f'{o["name"]}: front {d:+.1f}m ahead{timing}, {o["length"]}m long, {o["height"]}m tall')
    if action is not None:
        parts.append('current=' + json.dumps(action, separators=(',', ':')))
    return ' | '.join(parts)


class PilotEpisode:
    """One flight, advanced one decision at a time.

        ep = PilotEpisode(task)
        while not ep.done:
            ep.step(choose(ep.obs))      # action dict
        ep.result                        # same dict as tasks.run_episode
    """

    def __init__(self, task: Dict, record: bool = False):
        self.task = task
        self._gen = episode(task, record)
        self.obs = next(self._gen)
        self.action = {'mode': 'coast'}
        self.done = False
        self.result: Optional[Dict] = None
        self.decisions = 0
        self.invalid = 0

    def step(self, action: Optional[Dict]) -> Tuple[Dict, bool]:
        """Fly ``action`` for one decision interval. ``None`` keeps the
        previous action (an invalid or late reply)."""
        if action is not None:
            self.action = normalize_action(action)
        self.decisions += 1
        for _ in range(DECIDE_EVERY):
            try:
                self.obs = self._gen.send(throttles(self.action, self.obs))
            except StopIteration as stop:
                self.result = stop.value
                self.result['decisions'] = self.decisions
                self.result['invalid_actions'] = self.invalid
                self.done = True
                break
        return self.obs, self.done


def handover(task: Dict, steps: int, record: bool = False) -> PilotEpisode:
    """An episode where the scripted pilot (``pilot_baselines.TUNED``) has
    already flown the first ``steps`` decisions; the caller flies the rest.
    Deterministic for a given task, so a dataset row and its env agree on the
    hand-over state. Used as a reverse curriculum: training flights that start
    mid-manoeuvre (nothing is imitated; the reward is the whole flight's)."""
    from .pilot_baselines import TUNED  # pylint: disable=import-outside-toplevel
    ep, mem, pilot = PilotEpisode(task, record), {}, TUNED[task['kind']]
    for _ in range(steps):
        if ep.done:
            break
        ep.step(pilot(ep.obs, mem))
    return ep


def fly(task: Dict, pilot, record: bool = False) -> Dict:
    """Fly a scripted ``pilot(obs, mem) -> action dict`` (for baselines/tests)."""
    ep, mem = PilotEpisode(task, record), {}
    while not ep.done:
        ep.step(pilot(ep.obs, mem))
    return ep.result
