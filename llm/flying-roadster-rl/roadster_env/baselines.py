"""Hand-written reference controllers.

They are stored as *source strings* in exactly the format the LLM is asked to
produce (``def controller(obs, mem)``), so they double as:
  * a feasibility proof that every stunt is physically achievable, and
  * golden inputs for the tests.
"""

_MIXER = '''
import math

ARM_Y, ARM_X = 0.85, 1.5
SIGN_Y = (1, -1, 1, -1)   # FL, FR, RL, RR: +1 = left side
SIGN_X = (1, 1, -1, -1)   # +1 = front

def mix(total, tau_roll, tau_pitch, fmax):
    """Total thrust (N) + body torques (N*m) -> 4 throttles in [0, 1].

    Torque has priority over collective thrust so attitude control survives
    saturation (otherwise a hard climb pins every valve at 1.0)."""
    fmax = max(fmax, 1.0)
    d = [tau_roll * SIGN_Y[i] / (4 * ARM_Y) - tau_pitch * SIGN_X[i] / (4 * ARM_X)
         for i in range(4)]
    spread = max(d) - min(d)
    if spread > fmax:
        d = [x * fmax / spread for x in d]
    lo, hi = -min(d), fmax - max(d)
    c = min(hi, max(lo, total / 4))
    return [min(1.0, max(0.0, (c + x) / fmax)) for x in d]

def stabilize(obs, vz_target, kp=25000.0, kd=6000.0):
    """Hold level attitude and track a vertical speed."""
    tau_r = -kp * obs['roll'] - kd * obs['roll_rate']
    tau_p = -kp * 2 * obs['pitch'] - kd * 2 * obs['pitch_rate']
    up = max(obs['up_z'], 0.3)
    total = obs['mass'] * (9.81 + 4.0 * (vz_target - obs['vz'])) / up
    return mix(max(total, 0.0), tau_r, tau_p, obs['thrust_max_now'])

def landing_vz(obs):
    # Air is precious: free-fall-ish when high, braking late on a profile that
    # uses ~half the thrusters' decel authority, ~1.5 m/s at touchdown.
    return -(1.5 + math.sqrt(2 * 6.0 * max(obs['z'] - 0.8, 0.0)))
'''

HOP = _MIXER + '''
def controller(obs, mem):
    gap = obs['obstacle_x0'] - obs['x']
    h = obs['obstacle_height']
    # Time-to-obstacle: launch so we peak just as we reach it.
    if not mem.get('launched'):
        t_hit = gap / max(obs['vx'], 1.0)
        if t_hit < 1.2:
            mem['launched'] = True
        else:
            return [0.0] * 4
    clear_z = h + 1.6  # body centre height that clears the roof
    past = obs['x'] - 2.5 > obs['obstacle_x0'] + obs['obstacle_length']
    if not past:
        vz_t = 6.0 if obs['z'] < clear_z else 0.0
        return stabilize(obs, vz_t)
    return stabilize(obs, landing_vz(obs))
'''

KICKFLIP = _MIXER + '''
def controller(obs, mem):
    phase = mem.setdefault('phase', 'lift')
    fmax = obs['thrust_max_now']
    if phase == 'lift':
        if obs['z'] > 2.5:
            mem['phase'] = phase = 'spin'
        else:
            return stabilize(obs, 7.0)
    if phase == 'spin':
        if obs['roll_rate'] > 7.0:
            mem['phase'] = phase = 'coast'
        else:
            return mix(obs['mass'] * 9.81 * max(obs['up_z'], 0), 20000, 0, fmax)
    if phase == 'coast':
        # Brake so roll_rate hits ~0 exactly at 2*pi. alpha ~ 20 rad/s^2.
        remaining = 2 * math.pi - obs['roll_total']
        brake_angle = obs['roll_rate'] ** 2 / (2 * 20.0)
        if remaining <= brake_angle + 0.05:
            mem['phase'] = phase = 'land'
        else:
            return [0.0] * 4
    # Land: level out and descend gently (roll error from Euler, near 0 here).
    return stabilize(obs, landing_vz(obs))
'''

BACKFLIP = _MIXER + '''
def controller(obs, mem):
    phase = mem.setdefault('phase', 'lift')
    fmax = obs['thrust_max_now']
    if phase == 'lift':
        if obs['z'] > 2.5:
            mem['phase'] = phase = 'spin'
        else:
            return stabilize(obs, 7.0)
    if phase == 'spin':
        # Nose up = negative pitch rate: fire the FRONT pair harder.
        if obs['pitch_rate'] < -4.5:
            mem['phase'] = phase = 'coast'
        else:
            return mix(obs['mass'] * 9.81 * max(obs['up_z'], 0), 0, -36000, fmax)
    if phase == 'coast':
        remaining = 2 * math.pi + obs['pitch_total']
        brake_angle = obs['pitch_rate'] ** 2 / (2 * 10.0)
        if remaining <= brake_angle + 0.05:
            mem['phase'] = phase = 'land'
        else:
            return [0.0] * 4
    return stabilize(obs, landing_vz(obs))
'''

RAMP_FLIP = _MIXER + '''
def controller(obs, mem):
    # Leaves the ramp deck upside down. Roll 180 degrees back upright (roll
    # inertia is ~4.5x lower than pitch), stop the spin, then land.
    phase = mem.setdefault('phase', 'spin')
    fmax = obs['thrust_max_now']
    if phase == 'spin':
        if abs(obs['roll_rate']) > 6.0:
            mem['phase'] = phase = 'coast'
        else:
            # Pure torque: inverted, any collective thrust pushes us DOWN.
            return mix(0.0, 20000, 0, fmax)
    if phase == 'coast':
        remaining = math.pi - abs(obs['roll_total'])
        brake_angle = obs['roll_rate'] ** 2 / (2 * 22.0)
        if remaining <= brake_angle + 0.05:
            mem['phase'] = phase = 'land'
        else:
            return [0.0] * 4
    return stabilize(obs, landing_vz(obs))
'''

HOP_KICKFLIP = _MIXER + '''
def controller(obs, mem):
    # Launch early enough to be above the Cybertruck, kickflip over it, land.
    phase = mem.setdefault('phase', 'wait')
    fmax = obs['thrust_max_now']
    gap = obs['obstacle_x0'] - obs['x']
    if phase == 'wait':
        if gap / max(obs['vx'], 1.0) < 1.0:
            mem['phase'] = phase = 'lift'
        else:
            return [0.0] * 4
    if phase == 'lift':
        if obs['z'] > obs['obstacle_height'] + 2.3:
            mem['phase'] = phase = 'spin'
        else:
            return stabilize(obs, 7.0)
    if phase == 'spin':
        if obs['roll_rate'] > 7.0:
            mem['phase'] = phase = 'coast'
        else:
            return mix(obs['mass'] * 9.81 * max(obs['up_z'], 0), 20000, 0, fmax)
    if phase == 'coast':
        remaining = 2 * math.pi - obs['roll_total']
        brake_angle = obs['roll_rate'] ** 2 / (2 * 20.0)
        if remaining <= brake_angle + 0.05:
            mem['phase'] = phase = 'land'
        else:
            return [0.0] * 4
    past = obs['x'] - 2.5 > obs['obstacle_x0'] + obs['obstacle_length']
    return stabilize(obs, landing_vz(obs) if past else max(landing_vz(obs), 0.0))
'''

# Same controller, but energy-aware: a lower apex over the truck and a gentler
# spin leave enough air for the landing burn in strong crosswinds. Lands
# ~100% in gales/storms where HOP_KICKFLIP lands ~87% / ~78%: evidence that the
# wind failures are avoidable (a learned policy can beat the hand-written one).
HOP_KICKFLIP_WIND_AWARE = (HOP_KICKFLIP.replace(
    "obs['obstacle_height'] + 2.3:",
    "obs['obstacle_height'] + 1.9:").replace("obs['roll_rate'] > 7.0:",
                                             "obs['roll_rate'] > 5.0:"))
assert HOP_KICKFLIP_WIND_AWARE != HOP_KICKFLIP

# The same two reveal stunts written ONLY with the flight-control SDK (`fc`),
# i.e. the level of abstraction the model is given. Proof the SDK suffices:
# both land ~100% in normal, gale and storm winds.
RAMP_FLIP_SDK = '''import math
def controller(obs, mem):
    p = mem.setdefault('p', 'spin')
    if p == 'spin':
        if abs(obs['roll_rate']) > 6.0:
            mem['p'] = p = 'coast'
        else:
            return fc.spin(obs, 'roll', +1)
    if p == 'coast':
        if math.pi - abs(obs['roll_total']) <= fc.brake_angle(obs, 'roll'):
            mem['p'] = p = 'land'
        else:
            return [0.0] * 4
    tr, tp = fc.hold_attitude(obs)
    return fc.mix(fc.vz_thrust(obs, fc.landing_vz(obs)), tr, tp, obs)
'''

HOP_KICKFLIP_SDK = '''import math
def controller(obs, mem):
    p = mem.setdefault('p', 'wait')
    if p == 'wait':
        if (obs['obstacle_x0'] - obs['x']) / max(obs['vx'], 1) < 1.0:
            mem['p'] = p = 'lift'
        else:
            return [0.0] * 4
    if p == 'lift':
        if obs['z'] > obs['obstacle_height'] + 1.9:
            mem['p'] = p = 'spin'
        else:
            tr, tp = fc.hold_attitude(obs)
            return fc.mix(fc.vz_thrust(obs, 7.0), tr, tp, obs)
    if p == 'spin':
        if obs['roll_rate'] > 5.0:
            mem['p'] = p = 'coast'
        else:
            return fc.spin(obs, 'roll', +1, collective=obs['mass'] * 9.81 * max(obs['up_z'], 0))
    if p == 'coast':
        if 2 * math.pi - obs['roll_total'] <= fc.brake_angle(obs, 'roll') + 0.05:
            mem['p'] = p = 'land'
        else:
            return [0.0] * 4
    tr, tp = fc.hold_attitude(obs)
    past = obs['x'] - 2.5 > obs['obstacle_x0'] + obs['obstacle_length']
    vz = fc.landing_vz(obs) if past else max(fc.landing_vz(obs), 0.0)
    return fc.mix(fc.vz_thrust(obs, vz), tr, tp, obs)
'''

BASELINES = {
    'ramp_flip': RAMP_FLIP,
    'hop_kickflip': HOP_KICKFLIP,
    'hop': HOP,
    'kickflip': KICKFLIP,
    'backflip': BACKFLIP
}


def load_controller(source: str):
    """Exec controller source in-process (trusted code only!)."""
    from . import flight_sdk  # pylint: disable=import-outside-toplevel
    ns: dict = {'fc': flight_sdk}
    exec(compile(source, '<controller>', 'exec'), ns)  # pylint: disable=exec-used
    fn = ns['controller']
    mem: dict = {}
    return lambda obs: fn(obs, mem)
