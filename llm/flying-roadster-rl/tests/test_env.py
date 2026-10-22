"""Environment tests: physics sanity, stunt feasibility, isolation.

    cd demo/flying-roadster-rl && python -m pytest tests -q
"""
import random
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompting import build_messages, extract_code  # noqa: E402
from roadster_env import run_episode, sample_task  # noqa: E402
from roadster_env.baselines import BASELINES, load_controller  # noqa: E402

KICKFLIP = {'kind': 'kickflip', 'speed': 20, 'air_mass': 150, 'wind_y': 0}


def test_idle_car_keeps_driving():
    r = run_episode(KICKFLIP, lambda obs: [0, 0, 0, 0], record=False)
    assert r['crashed'] is None and not r['was_airborne']
    assert r['final_x'] == pytest.approx(20 * 6.0, rel=0.02)
    assert r['reward'] == 0.0


def test_air_is_consumed_and_runs_out():
    r = run_episode(KICKFLIP, lambda obs: [1, 1, 1, 1], record=False)
    assert r['was_airborne'] and r['air_left'] < 0.1


def test_one_sided_thrust_rolls_the_car():
    r = run_episode(KICKFLIP, lambda obs: [1, 0, 1, 0], record=False)
    assert r['roll_total'] > 1.0 and r['crashed']


@pytest.mark.parametrize('kind', sorted(BASELINES))
def test_every_stunt_is_feasible(kind):
    """Hand-written controllers land each stunt on randomized conditions."""
    rng = random.Random(0)
    rs = [run_episode(sample_task(kind, rng), load_controller(BASELINES[kind]),
                      record=False) for _ in range(20)]
    success = sum(r['success'] for r in rs)
    # Backflip pitches the large top/bottom area through gusty wind; even the
    # tuned controller lands ~80% (a real physical effect, not a bug).
    assert success >= (14 if kind == 'backflip' else 18), [r['crashed'] for r in rs]
    assert min(r['reward'] for r in rs if r['success']) >= 0.85


def test_reward_prefers_near_misses():
    """Clean landing > upright hard touchdown > spinning into the roof."""
    from roadster_env.baselines import KICKFLIP
    tail = '    return stabilize(obs, landing_vz(obs))\n'
    hard = KICKFLIP[:-len(tail)] + '    return [0.0] * 4\n'
    spin = KICKFLIP[:-len(tail)] + '    return [1.0, 0.0, 1.0, 0.0]\n'
    task = {'kind': 'kickflip', 'speed': 20, 'air_mass': 150, 'wind_y': 0}
    r = [run_episode(task, load_controller(c), record=False)['reward']
         for c in (KICKFLIP, hard, spin)]
    assert r[0] > r[1] > r[2], r


@pytest.mark.parametrize('kind', ['ramp_flip', 'hop_kickflip'])
def test_sdk_controllers_land_in_storms(kind):
    """The flight SDK is enough to fly the reveal stunts, even in storms."""
    from roadster_env.baselines import HOP_KICKFLIP_SDK, RAMP_FLIP_SDK
    src = RAMP_FLIP_SDK if kind == 'ramp_flip' else HOP_KICKFLIP_SDK
    rng = random.Random(5)
    rs = [run_episode(sample_task(kind, rng, weather='storm'), load_controller(src),
                      record=False) for _ in range(15)]
    assert sum(r['success'] for r in rs) >= 14


def test_wind_is_physical():
    """Boundary-layer profile, gusts, reproducibility, and a sideways push."""
    from roadster_env.physics import WindField
    w = WindField((10.0, 0.0), gust=0.0)
    assert w.at(1.0)[0] < w.at(10.0)[0] == 10.0 < w.at(20.0)[0]
    a, b = WindField((0, 0), gust=3.0, seed=7), WindField((0, 0), gust=3.0, seed=7)
    for _ in range(100):
        a.step(0.01), b.step(0.01)
    assert a.at(5.0) == b.at(5.0)  # seeded -> re-simulation is exact
    calm = {'kind': 'kickflip', 'speed': 20, 'air_mass': 150,
            'wind': {'x': 0, 'y': 0, 'gust': 0, 'seed': 1}}
    windy = dict(calm, wind={'x': 0, 'y': 20, 'gust': 0, 'seed': 1})
    lift = lambda o: [0.7] * 4
    ya = run_episode(calm, lift)['traj'][-1][2]
    yb = run_episode(windy, lift)['traj'][-1][2]
    assert yb > ya + 0.5  # a +y wind blows the car toward +y


def test_reward_is_bounded():
    rng = random.Random(1)
    for _ in range(20):
        t = sample_task(None, rng)
        u = [rng.random() for _ in range(4)]
        r = run_episode(t, lambda obs: u, record=False)
        assert 0.0 <= r['reward'] <= 1.0


def test_prompt_and_extraction():
    msgs = build_messages(sample_task('hop', random.Random(0)))
    assert 'controller(obs, mem)' in msgs[0]['content']
    assert 'Jump the car over' in msgs[1]['content']
    text = 'plan\n```python\ndef helper(): pass\n```\n```python\ndef controller(obs, mem):\n    return [0]*4\n```'
    assert extract_code(text).startswith('def controller')
    assert extract_code('<think>```python\ndef controller(o, m): pass\n```</think> nothing') is None


def test_landing_off_the_road_is_not_a_success():
    from roadster_env.tasks import is_success, score
    base = {'task': {'kind': 'ramp_flip'}, 'landed': True, 'crashed': None,
            'max_up': 1.0, 'touchdown_up': 1.0, 'touchdown_spin': 0.0,
            'landing_vz': 1.5, 'air_left': 0.5}
    centre = dict(base, lateral_offset=0.0, on_road=True)
    edge = dict(base, lateral_offset=5.5, on_road=True)
    off = dict(base, lateral_offset=8.0, on_road=False)
    assert is_success(centre) and is_success(edge) and not is_success(off)
    assert score(centre)[0] > score(edge)[0] > score(off)[0]


@pytest.mark.parametrize('dz, crashed', [(-0.1, True), (0.1, False)])
def test_wheels_clipping_the_truck_is_a_crash(dz, crashed):
    """The wheels hang 0.2 m below the floor: a car whose wheels scrape the
    Cybertruck's roof hit it, even if the body cleared."""
    from roadster_env.physics import Obstacle, RoadsterSim
    truck = Obstacle(x0=10.0, length=5.7, height=1.9)
    sim = RoadsterSim(obstacle=truck)
    wheel_z = sim.p.wheel_pos[0][2]
    sim.s.pos = (12.85, 0.0, truck.height - wheel_z + dz)  # level, centred over the truck
    sim.s.vel = (15.0, 0.0, 0.0)
    sim.step([0.0] * 4)
    assert (sim.crashed == 'hit_obstacle') == crashed


def test_pilot_actions_tolerate_nulls():
    """A reply like {"mode": "land", "roll": null} means "default", and must
    never crash the flight (it killed an RL run at step 37)."""
    from roadster_env.pilot import PilotEpisode, parse_action
    a = parse_action('{"mode": "land", "roll": null}')
    assert a == {'mode': 'land', 'roll': 0.0}
    assert parse_action('{"mode": "hold", "roll": null, "pitch": null, "vz": null}')['vz'] is None
    assert parse_action('{"mode": "rotate", "axis": "roll", "to": null, "lift": null}')['to'] == 360.0
    ep = PilotEpisode(sample_task('hop_kickflip', random.Random(0)))
    ep.step(a)
    ep.step(parse_action('{"mode": "climb", "vz": null}'))
