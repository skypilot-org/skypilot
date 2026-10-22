"""Prompt construction + code extraction shared by the trainer and evals."""
import pathlib
import random
import re
from typing import Dict, List, Optional

from roadster_env import sample_task
from roadster_env import TASK_KINDS
from roadster_env.physics import CarParams
from roadster_env.physics import G0

PROMPT_DIR = pathlib.Path(__file__).resolve().parent / 'prompts'
_SYSTEM = (PROMPT_DIR / 'system.md').read_text()

TASK_TEXT = {
    'ramp_flip':
        ('Fly the reveal stunt. The car has just driven up a curved '
         'ramp that curls into a half-loop ({radius} m radius) and '
         'across its inverted top deck, so at t = 0 it leaves the end '
         'of the deck UPSIDE DOWN at about {height:.1f} m, nose '
         'pointing -x, moving -x at {speed:.1f} m/s, not rotating. '
         'Rotate 180 degrees back upright (roll or pitch) and land on '
         'all four wheels. Careful: while inverted, the thrusters push '
         'the car toward the ground.'),
    'hop': ('Jump the car over a parked {name} ({length} m long, {height} m '
            'tall) that sits across the road starting at x = {x0} m, then land '
            'on all four wheels.'),
    'kickflip': ('Do a kickflip: get airborne, complete one full 360° roll '
                 'about the car\'s long axis (either direction), and land on '
                 'all four wheels.'),
    'backflip': ('Do a backflip: get airborne, complete one full 360° '
                 'nose-UP pitch rotation, and land on all four wheels.'),
    'hop_kickflip': ('Kickflip over a parked {name} ({length} m long, '
                     '{height} m tall) that starts at x = {x0} m: clear it, '
                     'complete a full 360° roll, and land on all four wheels.'),
}


def system_prompt(task: Dict, interface: str = 'sdk') -> str:
    """``interface='raw'`` drops the flight-control SDK section (the model
    must map thrust to the four throttles itself); used for the interface
    ablation."""
    p = CarParams(air_mass=float(task.get('air_mass', 150.0)))
    mass = p.dry_mass + p.air_mass
    total = 4 * p.thrust_max
    text = _SYSTEM
    if interface == 'raw':
        text = re.sub(r'## Flight-control SDK\n.*?\n\n',
                      '',
                      text,
                      flags=re.DOTALL)
    else:
        assert interface == 'sdk', interface
    return text.format(mass=mass,
                       thrust_max=p.thrust_max,
                       thrust_total=total,
                       tw=total / (mass * G0),
                       air_mass=p.air_mass,
                       burn_time=p.air_mass * p.isp * G0 / total)


def user_prompt(task: Dict) -> str:
    fmt = dict(task.get('obstacle', {}))
    if task.get('ramp'):
        fmt.update(radius=task['ramp']['radius'],
                   height=2 * task['ramp']['radius'],
                   speed=task['speed'])
    text = TASK_TEXT[task['kind']].format(**fmt)
    w = task.get('wind')
    if w:
        spd = (w['x']**2 + w['y']**2)**0.5
        wind_txt = (
            f'a wind forecast of {spd:.1f} m/s at 10 m height, blowing '
            f'toward (x {w["x"]:+.1f}, y {w["y"]:+.1f}) m/s (weaker near '
            f'the ground), gusty: random gusts of about ±{w["gust"]:.1f} m/s '
            f'that change over 1-2 s. The gusts are not known in advance')
    else:
        wind = task.get('wind_y', 0.0)
        wind_txt = (
            f'a {abs(wind):.1f} m/s crosswind blowing toward the car\'s '
            f'{"left" if wind > 0 else "right"}'
            if abs(wind) >= 0.5 else 'no wind')
    if task.get('ramp'):
        start = 'the car is airborne at the ramp lip (see above)'
    else:
        start = (f'the car starts at x = 0 driving straight down the road at '
                 f'{task["speed"]:.1f} m/s ({task["speed"] * 3.6:.0f} km/h)')
    return (f'Task: {text}\n\nConditions: {start}, with {task["air_mass"]:.0f} '
            f'kg of air in the tank and {wind_txt}.\n\nWrite the controller.')


_PILOT_SYSTEM = (PROMPT_DIR / 'pilot_system.md').read_text()


def pilot_system_prompt(task: Dict) -> str:
    p = CarParams(air_mass=float(task.get('air_mass', 150.0)))
    mass = p.dry_mass + p.air_mass
    total = 4 * p.thrust_max
    # Which way a roll pushes the car depends on its heading: the ramp's loop turns it around (nose -x).
    roll_drift = (
        'positive roll moves it toward positive y, because the nose points -x'
        if task['kind'] == 'ramp_flip' else
        'positive roll moves it toward negative y')
    return _PILOT_SYSTEM.format(mass=mass,
                                tw=total / (mass * G0),
                                air_mass=p.air_mass,
                                burn_time=p.air_mass * p.isp * G0 / total,
                                roll_drift=roll_drift)


def pilot_user_prompt(task: Dict,
                      first_obs: str,
                      handover: bool = False) -> str:
    """Task briefing + the first observation (the pilot flies; it writes no code).
    ``handover``: the flight computer flew the opening and hands over mid-flight."""
    briefing = user_prompt(task).replace('\n\nWrite the controller.', '')
    now = ('The flight computer flew the opening; you take over now.'
           if handover else 'You are flying now.')
    return f'{briefing}\n\n{now} Reply with one JSON action per turn.\n{first_obs}'


def build_pilot_messages(task: Dict,
                         first_obs: str,
                         handover: bool = False) -> List[Dict]:
    return [{
        'role': 'system',
        'content': pilot_system_prompt(task)
    }, {
        'role': 'user',
        'content': pilot_user_prompt(task, first_obs, handover)
    }]


def build_messages(task: Dict, interface: str = 'sdk') -> List[Dict]:
    return [{
        'role': 'system',
        'content': system_prompt(task, interface)
    }, {
        'role': 'user',
        'content': user_prompt(task)
    }]


_CODE_RE = re.compile(r'```(?:python|py)?\s*\n(.*?)```', re.DOTALL)


def extract_code(completion: str) -> Optional[str]:
    """Last fenced block that defines controller(); None if missing."""
    # Drop any <think>...</think> reasoning a thinking model emits.
    completion = re.sub(r'<think>.*?</think>', '', completion, flags=re.DOTALL)
    blocks = [b for b in _CODE_RE.findall(completion) if 'def controller' in b]
    return blocks[-1] if blocks else None


def make_dataset_rows(n: int, seed: int, kinds=TASK_KINDS) -> List[Dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        task = sample_task(kinds[i % len(kinds)], rng)
        rows.append({'prompt': build_messages(task), 'task': task})
    return rows
