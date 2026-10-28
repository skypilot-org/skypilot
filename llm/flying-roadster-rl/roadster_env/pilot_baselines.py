"""Scripted pilots that fly through the same decision interface the LLM uses
(one action every 0.2 s). They prove the stunts are flyable with these modes and
set the bar for the RL-trained pilot."""
import math


def ramp_flip(obs, mem):
    # Leave the deck inverted: roll back upright (cumulative +180 deg), then land.
    if not mem.get('upright'):
        if abs(obs['roll_total']) > math.radians(170) and abs(
                obs['roll_rate']) < 0.6:
            mem['upright'] = True
        else:
            return {'mode': 'rotate', 'axis': 'roll', 'to': 180, 'lift': 0.0}
    return {'mode': 'land'}


def hop_kickflip(obs, mem):
    o_x0, o_len, o_h = obs['obstacle_x0'], obs['obstacle_length'], obs[
        'obstacle_height']
    phase = mem.setdefault('phase', 'approach')
    if phase == 'approach':
        if (o_x0 - obs['x']) / max(obs['vx'], 1.0) < 1.1:
            phase = mem['phase'] = 'climb'
        else:
            return {'mode': 'coast'}
    if phase == 'climb':
        if obs['z'] > o_h + 1.9:
            phase = mem['phase'] = 'flip'
        else:
            return {'mode': 'climb', 'vz': 7.0}
    if phase == 'flip':
        if obs['roll_total'] > math.radians(350) and abs(
                obs['roll_rate']) < 0.6:
            phase = mem['phase'] = 'land'
        else:
            return {'mode': 'rotate', 'axis': 'roll', 'to': 360, 'lift': 1.0}
    past = obs['x'] - 2.5 > o_x0 + o_len
    return {'mode': 'land'} if past else {'mode': 'climb', 'vz': 0.0}


def hop_kickflip_tuned(obs, mem):
    """Same plan with three decisions tuned by hand in storm tests: a lower apex
    (truck + 1.4 m), a little extra lift during the roll, and roll trim against
    sideways drift while holding and landing. Storm success 6/24 -> 16/24."""
    o_x0, o_len, o_h = obs['obstacle_x0'], obs['obstacle_length'], obs[
        'obstacle_height']
    phase = mem.setdefault('phase', 'approach')
    if phase == 'approach':
        if (o_x0 - obs['x']) / max(obs['vx'], 1.0) < 1.1:
            phase = mem['phase'] = 'climb'
        else:
            return {'mode': 'coast'}
    if phase == 'climb':
        if obs['z'] > o_h + 1.4:
            phase = mem['phase'] = 'flip'
        else:
            return {'mode': 'climb', 'vz': 7.0}
    if phase == 'flip':
        if obs['roll_total'] > math.radians(350) and abs(
                obs['roll_rate']) < 0.6:
            phase = mem['phase'] = 'land'
        else:
            return {'mode': 'rotate', 'axis': 'roll', 'to': 360, 'lift': 1.1}
    trim = max(-15.0, min(15.0, 4.0 * (obs['vy'] + 0.3 * obs['y'])))
    past = obs['x'] - 2.5 > o_x0 + o_len
    return {
        'mode': 'land',
        'roll': trim
    } if past else {
        'mode': 'hold',
        'roll': trim,
        'pitch': 0,
        'vz': 0.0
    }


PILOTS = {'ramp_flip': ramp_flip, 'hop_kickflip': hop_kickflip}
TUNED = {'ramp_flip': ramp_flip, 'hop_kickflip': hop_kickflip_tuned}
