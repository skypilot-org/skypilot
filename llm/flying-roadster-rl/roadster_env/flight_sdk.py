"""Flight-control SDK available to controllers as the global ``fc``.

Like a real flight-software stack, it handles the low-level control so the
controller only has to write the flight logic (phases, switching conditions,
thresholds, air budget). Everything here only uses the observation; it has no
access to the simulator. Stdlib-only. The pilot interface (``pilot.py``) uses
it to fly each high-level command at 50 Hz.

    u = fc.mix(total_N, tau_roll, tau_pitch, obs)        # -> [u0, u1, u2, u3]
    tau_roll, tau_pitch = fc.hold_attitude(obs)           # PD to level (wrap-safe)
    total = fc.vz_thrust(obs, vz_target)                  # thrust for a climb/sink rate
    vz = fc.landing_vz(obs)                               # late-braking descent profile
    u = fc.spin(obs, 'roll', +1)                          # max-rate spin, least collective
    a = fc.brake_angle(obs, 'roll')                       # angle needed to stop the spin
"""
import math

G = 9.81
ARM_Y, ARM_X = 0.85, 1.5
SIGN_Y = (1, -1, 1, -1)   # thrusters FL, FR, RL, RR: +1 = left side
SIGN_X = (1, 1, -1, -1)   # +1 = front
ROLL_ACCEL_MAX = 25.0     # rad/s^2 with one side at full thrust
PITCH_ACCEL_MAX = 10.0    # rad/s^2 with one end at full thrust


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def mix(total, tau_roll, tau_pitch, obs):
    """Total thrust (N, along the car's up axis) + body torques (N*m) -> throttles.

    Torque has priority: if the request saturates, collective thrust is cut
    before attitude authority is lost.
    """
    fmax = max(obs['thrust_max_now'], 1.0)
    d = [tau_roll * SIGN_Y[i] / (4 * ARM_Y) - tau_pitch * SIGN_X[i] / (4 * ARM_X)
         for i in range(4)]
    spread = max(d) - min(d)
    if spread > fmax:
        d = [x * fmax / spread for x in d]
    c = min(fmax - max(d), max(-min(d), total / 4))
    return [min(1.0, max(0.0, (c + x) / fmax)) for x in d]


def hold_attitude(obs, roll_target=0.0, pitch_target=0.0, kp=25000.0, kd=6000.0):
    """PD torques toward a target roll/pitch (radians). Angle errors wrap, so
    it also works from upside down (it takes the short way round)."""
    e_r = _wrap(obs['roll'] - roll_target)
    e_p = _wrap(obs['pitch'] - pitch_target)
    return (-kp * e_r - kd * obs['roll_rate'],
            -2 * kp * e_p - 2 * kd * obs['pitch_rate'])


def vz_thrust(obs, vz_target, gain=4.0):
    """Total thrust (N) to track a vertical speed. 0 when the car is upside
    down or on its side (thrust would push it down or sideways)."""
    if obs['up_z'] < 0.3:
        return 0.0
    return max(0.0, obs['mass'] * (G + gain * (vz_target - obs['vz'])) / obs['up_z'])


def landing_vz(obs, decel=6.0, touchdown=1.5):
    """Recommended (negative) vertical speed for a late-braking descent: fall
    fast while high, brake late, ~touchdown m/s at the wheels. Saves air."""
    return -(touchdown + math.sqrt(2 * decel * max(obs['z'] - 0.8, 0.0)))


def spin(obs, axis='roll', direction=1, collective=0.0):
    """Throttles for a maximum-rate spin about 'roll' or 'pitch' (direction
    +1/-1 in the body-rate sign convention) with the least possible collective
    thrust (add some via ``collective`` in N if you also want lift)."""
    big = 1e6 * direction
    return mix(collective, big if axis == 'roll' else 0.0,
               big if axis == 'pitch' else 0.0, obs)


def brake_angle(obs, axis='roll', margin=0.85):
    """Rotation (rad) needed to stop the current spin at full counter-torque,
    padded by ``margin`` of the max angular deceleration."""
    rate = obs['roll_rate'] if axis == 'roll' else obs['pitch_rate']
    amax = ROLL_ACCEL_MAX if axis == 'roll' else PITCH_ACCEL_MAX
    return rate * rate / (2 * amax * margin)
