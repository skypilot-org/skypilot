"""Stunt tasks, the episode loop, and reward functions.

A task is a plain JSON-able dict, so it can be stored in a dataset row as-is:

    {"kind": "kickflip", "speed": 18.0, "air_mass": 150.0, "wind_y": 0.0}
    {"kind": "hop", "speed": 22.0, "obstacle": {"x0": 30, "length": 5.7,
     "height": 1.9}, ...}
    {"kind": "ramp_flip", "speed": 12.0, "ramp": {"x0": 20, "radius": 6,
     "deck": 5}, ...}

``ramp_flip`` is the Oct 2026 reveal stunt: the car drives up a curved ramp
that curls into a half-loop, rides across the inverted top deck upside down
(held on by its suction fans), and leaves the end of the deck inverted. The
ramp ride is scripted (the car is on the track); the controller flies from the
lip: roll or pitch 180 degrees back upright and land on all four wheels.

A controller is any callable ``controller(obs: dict) -> [u0, u1, u2, u3]``
with throttles in [0, 1]. It is called at 50 Hz; physics runs at 500 Hz.
"""
import math
import random
from typing import Callable, Dict, List, Optional

from .physics import CarParams
from .physics import Obstacle
from .physics import RoadsterSim
from .physics import WindField

CONTROL_HZ = 50
T_MAX = 6.0
TRAJ_EVERY = 0.02  # s between recorded frames
TWO_PI = 2 * math.pi

TASK_KINDS = ('ramp_flip', 'hop_kickflip', 'hop', 'kickflip', 'backflip')
# The two reveal stunts; the others are simpler warm-up / curriculum tasks.
REVEAL_KINDS = ('ramp_flip', 'hop_kickflip')
WHEEL_DROP = 0.35  # body-frame z of the wheel contact points (CarParams)
# The road runs along x, centred on y = 0 (the viewer draws it 14 m wide with
# cones at +-7.6 m). A landing only counts with all four wheels on it.
ROAD_HALF_WIDTH = 7.0

# Real vehicles to jump over (length, height) in metres.
OBSTACLES = {
    'Model 3': (4.7, 1.44),
    'Model Y': (4.8, 1.62),
    'Cybercab': (4.3, 1.5),
    'Cybertruck': (5.7, 1.9),
    'Tesla Semi': (6.5, 4.0),
}

# Weather presets: (mean wind at 10 m range, gust sigma range), m/s.
WEATHER = {
    'normal': ((0.0, 14.0), (0.5, 4.0)),  # calm .. strong breeze (training)
    'gale': ((14.0, 24.0), (4.0, 8.0)),  # gale .. strong gale (held-out test)
    'storm': (
        (24.0, 33.0), (8.0, 12.0)),  # storm .. violent storm (held-out test)
}


def sample_task(kind: Optional[str] = None,
                rng: Optional[random.Random] = None,
                weather: str = 'normal') -> Dict:
    rng = rng or random.Random()
    kind = kind or rng.choice(TASK_KINDS)
    task = {
        'kind': kind,
        'speed': round(rng.uniform(14.0, 26.0), 1),
        'air_mass': round(rng.uniform(110.0, 170.0), 0),
    }
    # Weather: mean wind at 10 m (any direction) + turbulent gusts.
    (lo, hi), (glo, ghi) = WEATHER[weather]
    speed = rng.uniform(lo, hi)
    heading = rng.uniform(0.0, 2 * math.pi)  # direction the wind blows TOWARD
    task['weather'] = weather
    task['wind'] = {
        'x': round(speed * math.cos(heading), 1),
        'y': round(speed * math.sin(heading), 1),
        'gust': round(rng.uniform(glo, ghi), 1),
        'seed': rng.randrange(1 << 30)
    }
    if kind == 'ramp_flip':
        # Speed along the track at the lip; ramp sized like the reveal's.
        task['speed'] = round(rng.uniform(9.0, 14.0), 1)
        task['ramp'] = {
            'x0': 20.0,
            'radius': round(rng.uniform(5.0, 7.0), 1),
            'deck': round(rng.uniform(3.0, 6.0), 1)
        }
    if kind in ('hop', 'hop_kickflip'):
        # hop_kickflip is the reveal's "flip over a Cybertruck".
        names = (['Cybertruck'] if kind == 'hop_kickflip' else list(OBSTACLES))
        name = rng.choice(names)
        length, height = OBSTACLES[name]
        task['obstacle'] = {
            'name': name,
            'x0': round(rng.uniform(25, 45), 1),
            'length': length,
            'height': height
        }
    return task


def ramp_lip(task: Dict):
    """Car state where it leaves the ramp deck: inverted, nose pointing -x."""
    r = task['ramp']
    x = r['x0'] - r['deck']
    z = 2 * r['radius'] - WHEEL_DROP  # wheels on the deck's underside
    return (x, 0.0, z), (-task['speed'], 0.0, 0.0), (0.0, 0.0, 1.0, 0.0)


def ramp_ride_frames(task: Dict,
                     dt: float = TRAJ_EVERY,
                     approach_s: float = 1.5):
    """Scripted pre-launch trajectory (t < 0): approach, half-loop, deck.

    Rows match the recorded trajectory format with zero thrust and a full
    tank: [t, x, y, z, qw, qx, qy, qz, thr0..thr3, air_frac, wind_x, wind_y,
    wind_z] (wind = air velocity at the car, m/s).
    """
    r, v = task['ramp'], task['speed']
    rc = r['radius'] - WHEEL_DROP  # path of the car's centre inside the loop
    s_app, s_arc, s_deck = v * approach_s, math.pi * rc, r['deck']
    total = s_app + s_arc + s_deck
    rows, s = [], 0.0
    wind = make_wind(task)
    while s < total - 1e-9:
        if s < s_app:  # driving toward the ramp
            x, z, th = r['x0'] - s_app + s, WHEEL_DROP, 0.0
        elif s < s_app + s_arc:  # up and over the half-loop (nose-up pitch)
            phi = (s - s_app) / rc
            x, z, th = r['x0'] + rc * math.sin(
                phi), r['radius'] - rc * math.cos(phi), -phi
        else:  # upside down along the deck, heading -x
            x, z, th = r['x0'] - (s - s_app -
                                  s_arc), 2 * r['radius'] - WHEEL_DROP, -math.pi
        q = (math.cos(th / 2), 0.0, math.sin(th / 2), 0.0)
        rows.append([round((s - total) / v, 3),
                     round(x, 3), 0.0,
                     round(z, 3)] + [round(c, 4) for c in q] + [0.0] * 4 +
                    [1.0] + [round(c, 2) for c in wind.at(z)])
        s += v * dt
    return rows


def make_wind(task: Dict) -> WindField:
    w = task.get('wind')
    if w:
        return WindField((w['x'], w['y']), gust=w['gust'], seed=w['seed'])
    return WindField((0.0, float(task.get('wind_y', 0.0))))  # legacy tasks


def make_sim(task: Dict) -> RoadsterSim:
    params = CarParams(air_mass=float(task.get('air_mass', 150.0)))
    obs = task.get('obstacle')
    obstacle = Obstacle(obs['x0'], obs['length'],
                        obs['height']) if obs else None
    sim = RoadsterSim(params,
                      speed=float(task.get('speed', 20.0)),
                      obstacle=obstacle,
                      wind=make_wind(task))
    if task.get('ramp'):
        pos, vel, quat = ramp_lip(task)
        sim.s.pos, sim.s.vel, sim.s.quat = pos, vel, quat
        sim.wheels_down = 0
    return sim


def task_obs(task: Dict) -> Dict:
    """Static task info appended to every observation."""
    out = {'task': task['kind']}
    if task.get('ramp'):
        out.update(launch_height=2 * task['ramp']['radius'])
    if task.get('obstacle'):
        o = task['obstacle']
        out.update(obstacle_x0=o['x0'],
                   obstacle_length=o['length'],
                   obstacle_height=o['height'])
    return out


def episode(task: Dict, record: bool = True):
    """The episode loop as a generator: yields ``obs`` at every 50 Hz control
    tick and expects the four throttles back via ``send()``. Returns the result
    dict (``StopIteration.value``). ``run_episode`` drives it with a controller
    callable; ``pilot.PilotEpisode`` drives it from an LLM's decisions."""
    sim = make_sim(task)
    steps_per_ctrl = round(1 / (CONTROL_HZ * sim.DT))
    traj_every = round(TRAJ_EVERY / sim.DT)
    max_steps = round(T_MAX / sim.DT)
    static = task_obs(task)
    z0 = sim.s.pos[2]

    u = [0.0] * 4
    airborne_t = 0.0
    was_airborne = False
    settle_t = 0.0
    landed = False
    landing_vz = 0.0
    touchdown_seen = False
    touchdown_up = touchdown_spin = None
    max_z = z0
    max_up = -1.0
    lateral = 0.0  # max |y| of the centre from first touchdown on
    traj = ramp_ride_frames(task) if (record and task.get('ramp')) else []

    for k in range(max_steps):
        if k % steps_per_ctrl == 0:
            obs = sim.observe()
            obs.update(static)
            u = yield obs
        if record and k % traj_every == 0:
            s = sim.s
            traj.append([round(s.t, 3)] + [round(c, 3) for c in s.pos] +
                        [round(c, 4) for c in s.quat] +
                        [round(f / sim.p.thrust_max, 3) for f in s.thrust] +
                        [round(s.air / sim.p.air_mass, 3)] +
                        [round(c, 2) for c in sim.wind_now])
        sim.step(u)
        s = sim.s
        max_z = max(max_z, s.pos[2])
        R_up = 1 - 2 * (s.quat[1]**2 + s.quat[2]**2)  # body-z . world-z
        max_up = max(max_up, R_up)

        in_air = sim.wheels_down == 0 and not sim.hull_contact
        if in_air:
            airborne_t += sim.DT
            if airborne_t > 0.15:
                was_airborne = True
        else:
            airborne_t = 0.0
            if was_airborne and not touchdown_seen:
                touchdown_seen = True
                landing_vz = -s.vel[2]
                touchdown_up = 1 - 2 * (s.quat[1]**2 + s.quat[2]**2)
                touchdown_spin = math.sqrt(sum(w * w for w in s.omega))
        if touchdown_seen:
            lateral = max(lateral, abs(s.pos[1]))
        if sim.crashed:
            break
        if was_airborne:
            omega = math.sqrt(sum(w * w for w in s.omega))
            up_z = sim.observe()['up_z']
            if sim.wheels_down == 4 and up_z > 0.94 and omega < 2.0:
                settle_t += sim.DT
                if settle_t > 0.3:
                    landed = True
                    break
            else:
                settle_t = 0.0

    s = sim.s
    result = {
        'task': task,
        'landed': landed,
        'crashed': sim.crashed,
        'controller_error': None,
        'was_airborne': was_airborne,
        'duration': round(s.t, 3),
        'roll_total': s.roll_total,
        'pitch_total': s.pitch_total,
        'max_height_gain': max_z - z0,
        'max_up': max_up,
        'touchdown_up': touchdown_up,
        'touchdown_spin': touchdown_spin,
        'landing_vz': landing_vz,
        'air_left': s.air / sim.p.air_mass,
        'final_x': s.pos[0],
        'lateral_offset': round(lateral, 3),
        # Wheels are ~half the body width from the centre line.
        'on_road': touchdown_seen
                   and lateral + sim.p.width / 2 <= ROAD_HALF_WIDTH,
    }
    if task.get('obstacle'):
        o = task['obstacle']
        # Rear bumper is ~half a car length behind the centre.
        result['cleared'] = (
            sim.crashed != 'hit_obstacle' and
            s.pos[0] - sim.p.length / 2 > o['x0'] + o['length'])
    result['reward'], result['reward_terms'] = score(result)
    result['success'] = is_success(result)
    if record:
        result['traj'] = traj
    return result


def run_episode(task: Dict,
                controller: Callable[[Dict], List[float]],
                record: bool = True) -> Dict:
    """Fly ``controller(obs) -> 4 throttles`` (called at 50 Hz) through a task.
    A controller that raises or returns NaN is switched off for the rest of
    the episode (throttles 0) and the error is reported."""
    gen = episode(task, record)
    error = None
    try:
        obs = next(gen)
        while True:
            u = [0.0] * 4
            if error is None:
                try:
                    out = controller(obs)
                    u = [float(out[i]) for i in range(4)]
                    if any(math.isnan(x) for x in u):
                        raise ValueError('controller returned NaN')
                except Exception as e:  # pylint: disable=broad-except
                    error = f'{type(e).__name__}: {e}'[:300]
                    u = [0.0] * 4
            obs = gen.send(u)
    except StopIteration as stop:
        result = stop.value
    result['controller_error'] = error
    return result


def _rotations(kind: str, r: Dict) -> float:
    """Full rotations completed in the stunt's direction."""
    if kind in ('kickflip', 'hop_kickflip'):
        return abs(r['roll_total']) / TWO_PI
    if kind == 'backflip':  # nose-up rotation is negative body pitch
        return max(0.0, -r['pitch_total']) / TWO_PI
    return 0.0


def _rot_progress(kind: str, r: Dict) -> float:
    """Fraction of the required rotation done (capped at 1). Used for success."""
    return min(_rotations(kind, r), 1.0)


def _rot_credit(kind: str, r: Dict) -> float:
    """Reward credit for rotation: full for 1-1.25 turns, then decaying to 0
    at 2.25 turns. Without the decay, "spin 4-5 times and crash" earned full
    rotation credit and GRPO had no gradient toward stopping the spin
    (observed on the Cybertruck flip: groups all scoring ~0.40)."""
    n = _rotations(kind, r)
    if n <= 1.25:
        return min(n, 1.0)
    return max(0.0, 1.0 - (n - 1.25))


def touchdown_quality(r: Dict) -> float:
    """How close the first ground contact was to a clean landing, in [0, 1].

    Continuous credit even when the touchdown is a crash, so "almost landed"
    beats "flipped and slammed in". Without it the policy plateaus on the
    partial-credit terms (rotation / height / cleared) and never learns the
    landing (observed: held-out reward stuck at exactly 0.40 / 0.60).
    """
    if r.get('touchdown_up') is None or r['crashed'] == 'hit_obstacle':
        return 0.0
    up = max(0.0, min(1.0, r['touchdown_up']))  # 1 = wheels down
    calm = max(0.0, min(1.0, 1 - r['touchdown_spin'] / 6.0))
    soft = max(0.0, min(1.0, 1 - r['landing_vz'] / 9.0))
    return up * (0.5 + 0.25 * calm + 0.25 * soft)


def centred(r: Dict) -> float:
    """1 on the centre line, 0 with the wheels at the road edge."""
    room = ROAD_HALF_WIDTH - 0.95  # half the car's width
    return max(0.0, 1.0 - r.get('lateral_offset', 0.0) / room)


def is_success(r: Dict) -> bool:
    """Stuck the landing ON THE ROAD and did the stunt."""
    kind = r['task']['kind']
    if not r['landed'] or r['crashed'] or not r.get('on_road', True):
        return False
    if kind in ('kickflip', 'backflip'):
        return _rot_progress(kind, r) > 0.85
    if kind == 'hop':
        return bool(r.get('cleared'))
    if kind == 'hop_kickflip':
        return bool(r.get('cleared')) and _rot_progress(kind, r) > 0.85
    if kind == 'ramp_flip':
        return True
    raise ValueError(f'unknown task kind {kind!r}')


def score(r: Dict):
    """Reward in [0, 1]: stunt progress -> touchdown quality -> clean landing.

    Each stage only pays once the previous one is achieved, so the gradient
    always points at the next missing piece of the stunt.
    """
    kind = r['task']['kind']
    success = is_success(r)
    soft = max(0.0, min(1.0, 1 - r['landing_vz'] / 9.0))
    # Landing near the centre line is part of a clean finish (videos showed
    # policies landing upright but off the asphalt).
    finish = 0.4 * soft + 0.3 * r['air_left'] + 0.3 * centred(r)
    tq = touchdown_quality(r)
    if kind in ('kickflip', 'backflip'):
        prog = _rot_credit(kind, r)
        terms = {'rotation': 0.25 * prog, 'touchdown': 0.25 * tq * prog}
    elif kind == 'hop':
        h = r['task']['obstacle']['height']
        cleared = float(bool(r.get('cleared')))
        terms = {
            'height': 0.2 * min(r['max_height_gain'] / (h + 0.5), 1.0),
            'cleared': 0.2 * cleared,
            'touchdown': 0.1 * tq * cleared
        }
    elif kind == 'ramp_flip':
        upright = max(0.0, min(1.0, (r['max_up'] + 1) / 2))
        terms = {'uprighted': 0.25 * upright, 'touchdown': 0.25 * tq}
    elif kind == 'hop_kickflip':
        h = r['task']['obstacle']['height']
        cleared = float(bool(r.get('cleared')))
        prog = _rot_credit(kind, r)
        terms = {
            'height': 0.1 * min(r['max_height_gain'] / (h + 0.5), 1.0),
            'cleared': 0.1 * cleared,
            'rotation': 0.15 * prog,
            'touchdown': 0.15 * tq * prog * cleared
        }
    else:
        raise ValueError(f'unknown task kind {kind!r}')
    terms['landed'] = 0.4 * success
    terms['finish'] = 0.1 * success * finish
    terms = {k: round(float(v), 4) for k, v in terms.items()}
    return round(sum(terms.values()), 4), terms
