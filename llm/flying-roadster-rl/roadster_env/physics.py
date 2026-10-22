"""Pure-Python 3D rigid-body sim of a thruster-equipped roadster.

No third-party deps on purpose: it runs anywhere with a stock Python 3.

Frames
------
World: x forward (initial heading), y left, z up. Ground is the plane z = 0.
Body:  same axes when the car sits level. Quaternion ``q`` maps body -> world.

Model (all numbers are *assumptions*, see CarParams; Tesla published no
thrust / pressure / mass figures for the 2026 Roadster):
  * 4 cold-gas thrusters at the corners, nozzles fixed, firing along body +z
    (i.e. they push the car "up" relative to its own floor). Differential
    left/right thrust -> roll (kickflip), front/rear -> pitch (backflip).
  * One compressed-air tank. Isothermal blowdown: max thrust scales with the
    remaining air mass, so thrust fades as the tank empties.
  * First-order valve lag on every thruster.
  * Ground contact via spring-damper wheels (suspension) and stiff hull
    points; hull/roof contact or a hard landing is a crash.
"""
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

G0 = 9.80665
RHO_AIR = 1.225

Vec = Tuple[float, float, float]


# --------------------------------------------------------------------------
# Small vector / quaternion helpers (tuples, no numpy)
# --------------------------------------------------------------------------
def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def norm(a):
    return math.sqrt(dot(a, a))


def quat_mul(p, q):
    pw, px, py, pz = p
    qw, qx, qy, qz = q
    return (pw * qw - px * qx - py * qy - pz * qz,
            pw * qx + px * qw + py * qz - pz * qy,
            pw * qy - px * qz + py * qw + pz * qx,
            pw * qz + px * qy - py * qx + pz * qw)


def quat_normalize(q):
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q)


def quat_to_mat(q):
    """Rotation matrix (body -> world) as 3 row tuples."""
    w, x, y, z = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))


def mat_vec(m, v):
    return (dot(m[0], v), dot(m[1], v), dot(m[2], v))


def mat_t_vec(m, v):
    return (m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
            m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
            m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2])


def quat_to_euler(q):
    """ZYX Euler angles (roll, pitch, yaw) in radians."""
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    sp = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    pitch = math.asin(sp)
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------
@dataclass
class CarParams:
    # Body: 4.5 m x 1.9 m x 1.1 m, ~4,400 lb dry ("4000+ pound car").
    length: float = 4.5
    width: float = 1.9
    height: float = 1.1
    dry_mass: float = 2000.0  # kg, includes COPVs
    # Air supply. 600 L of COPV volume was the figure quoted at the reveal;
    # pressure is not public. 150 kg usable air ~= 600 L at ~300 bar
    # (isothermal, ideal gas) minus the regulator floor.
    air_mass: float = 150.0  # kg usable
    isp: float = 65.0  # s, realistic for ambient-temperature air
    # Thrusters: (x, y, z) in body frame, firing along body +z.
    thruster_pos: Tuple[Vec, ...] = (
        (1.5, 0.85, -0.2),  # 0 front-left
        (1.5, -0.85, -0.2),  # 1 front-right
        (-1.5, 0.85, -0.2),  # 2 rear-left
        (-1.5, -0.85, -0.2),  # 3 rear-right
    )
    thrust_max: float = 12000.0  # N per thruster while regulated
    # Regulated supply: full thrust until the tank falls to this fraction,
    # then blowdown (thrust ~ remaining air) to zero.
    regulator_floor: float = 0.25
    valve_tau: float = 0.03  # s, first-order valve lag
    # Aero: drag area (Cd * A) per BODY axis, so a crosswind or a car falling
    # flat sees far more area than the head-on frontal 0.65 m^2.
    cda_body: Vec = (0.65, 3.2, 6.0)  # front/back, side, top/bottom (m^2)
    # Centre of pressure relative to the CoM: behind and above it, so side
    # wind weathervanes the nose and rolls the car slightly.
    cp_offset: Vec = (-0.25, 0.0, 0.15)
    ang_damping: float = 150.0  # N*m*s/rad, crude aero rotational damping
    # Wheels / suspension.
    wheel_pos: Tuple[Vec, ...] = (
        (1.4, 0.8, -0.35),
        (1.4, -0.8, -0.35),
        (-1.4, 0.8, -0.35),
        (-1.4, -0.8, -0.35),
    )
    susp_k: float = 80000.0
    susp_c: float = 9000.0
    tire_mu: float = 1.1
    # Hull contact.
    hull_k: float = 600000.0
    hull_c: float = 30000.0
    hull_mu: float = 0.5
    # Crash thresholds.
    max_landing_vz: float = 9.0  # m/s vertical speed at wheel touchdown
    max_hull_impact: float = 3.0  # m/s normal speed at hull contact

    @property
    def inertia(self) -> Vec:
        m, l, w, h = self.dry_mass, self.length, self.width, self.height
        return (m / 12 * (w * w + h * h), m / 12 * (l * l + h * h),
                m / 12 * (l * l + w * w))

    def hull_points(self) -> List[Tuple[Vec, bool]]:
        """(point, is_roof) pairs. Floor sits above the wheel contact."""
        hl, hw = self.length / 2, self.width / 2
        z_lo, z_hi = -0.15, -0.15 + self.height
        pts = []
        for sx in (1, -1):
            for sy in (1, -1):
                pts.append(((sx * hl, sy * hw, z_lo), False))
                pts.append(((sx * hl, sy * hw, z_hi), True))
        # Mid-roof / mid-sides so the car can't balance on its edges unseen.
        pts.append(((0.0, 0.0, z_hi), True))
        pts.append(((0.0, hw, z_hi * 0.5), True))
        pts.append(((0.0, -hw, z_hi * 0.5), True))
        return pts

    def obstacle_points(self) -> List[Vec]:
        """Body-frame points tested against obstacles: the wheel contacts plus
        a grid over the whole body surface (every 0.5 m or finer), so neither
        a wheel nor a mid-body edge can clip the truck unnoticed."""
        hl, hw = self.length / 2, self.width / 2
        z_lo, z_hi = -0.15, -0.15 + self.height
        xs = [-hl + i * self.length / 9 for i in range(10)]
        ys = [-hw + i * self.width / 4 for i in range(5)]
        zs = [z_lo + i * self.height / 3 for i in range(4)]
        pts = {(x, y, z) for x in xs for y in ys for z in zs
               if x in (xs[0], xs[-1]) or y in (ys[0], ys[-1]) or z in (zs[0], zs[-1])}
        return sorted(pts) + list(self.wheel_pos)


class WindField:
    """Near-ground atmospheric wind: mean wind + turbulent gusts.

    * Mean wind follows the power-law surface-layer profile
      U(z) = U_10 * (z / 10 m) ** 0.14 (open terrain), so it is weaker near
      the ground than at the 10 m reference height.
    * Gusts are an Ornstein-Uhlenbeck process per axis (exponentially
      correlated, i.e. a first-order Dryden-style turbulence model) with time
      constant ``tau`` and standard deviation ``gust`` (vertical: half, and
      suppressed close to the ground). Exact discretisation, seeded, so an
      episode is reproducible for evals / re-simulation.
    """
    Z_REF, ALPHA, Z_MIN = 10.0, 0.14, 0.5

    def __init__(self, mean_xy=(0.0, 0.0), gust: float = 0.0, tau: float = 1.5,
                 seed: int = 0):
        self.mean = (float(mean_xy[0]), float(mean_xy[1]))
        self.sigma = (gust, gust, 0.5 * gust)
        self.tau = tau
        self.rng = random.Random(seed)
        # Start in the stationary distribution (no spin-up transient).
        self.g = [self.rng.gauss(0.0, sg) for sg in self.sigma]

    def step(self, dt: float):
        if not any(self.sigma):
            return
        a = math.exp(-dt / self.tau)
        b = math.sqrt(1 - a * a)
        self.g = [a * g + b * sg * self.rng.gauss(0.0, 1.0)
                  for g, sg in zip(self.g, self.sigma)]

    def at(self, z: float) -> Vec:
        prof = (max(z, self.Z_MIN) / self.Z_REF) ** self.ALPHA
        near_ground = min(1.0, max(z, 0.0) / 3.0)
        return (self.mean[0] * prof + self.g[0], self.mean[1] * prof + self.g[1],
                self.g[2] * near_ground)


@dataclass
class Obstacle:
    """Axis-aligned box on the ground (e.g. a parked Cybertruck)."""
    x0: float
    length: float
    height: float
    half_width: float = 3.0

    def near(self, p: Vec, reach: float) -> bool:
        """Cheap gate: could any point within ``reach`` of ``p`` be inside?"""
        return (self.x0 - reach <= p[0] <= self.x0 + self.length + reach and
                abs(p[1]) <= self.half_width + reach and p[2] <= self.height + reach)

    def contains(self, p: Vec) -> bool:
        return (self.x0 <= p[0] <= self.x0 + self.length and
                abs(p[1]) <= self.half_width and p[2] <= self.height)


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------
@dataclass
class SimState:
    pos: Vec
    vel: Vec
    quat: Tuple[float, float, float, float]
    omega: Vec  # body frame
    air: float
    thrust: List[float]  # actual (lagged) thrust per thruster, N
    t: float = 0.0
    roll_total: float = 0.0  # integral of body roll rate
    pitch_total: float = 0.0  # integral of body pitch rate


class RoadsterSim:
    DT = 0.002  # physics step (500 Hz)

    def __init__(self, params: Optional[CarParams] = None,
                 speed: float = 20.0,
                 obstacle: Optional[Obstacle] = None,
                 wind=None):
        self.p = params or CarParams()
        self.cruise_speed = speed
        self.obstacle = obstacle
        # Accept a WindField, or a constant (x, y, z) vector for old tasks.
        if wind is None or isinstance(wind, tuple):
            wind = WindField(mean_xy=(wind or (0.0, 0.0, 0.0))[:2])
        self.wind = wind
        self.wind_now: Vec = self.wind.at(0.3)
        self._hull = self.p.hull_points()
        self._obstacle_pts = self.p.obstacle_points()
        self._inertia = self.p.inertia
        # Static sag so the car starts at rest on its suspension.
        sag = self.p.dry_mass * G0 / 4 / self.p.susp_k
        z0 = -self.p.wheel_pos[0][2] - sag
        self.s = SimState(pos=(0.0, 0.0, z0), vel=(speed, 0.0, 0.0),
                          quat=(1.0, 0.0, 0.0, 0.0), omega=(0.0, 0.0, 0.0),
                          air=self.p.air_mass, thrust=[0.0] * 4)
        self.crashed: Optional[str] = None
        self.wheels_down = 4
        self.hull_contact = False

    # ---- derived quantities ------------------------------------------------
    @property
    def mass(self) -> float:
        return self.p.dry_mass + self.s.air

    def thrust_limit(self) -> float:
        frac = max(0.0, self.s.air) / self.p.air_mass
        return self.p.thrust_max * min(1.0, frac / self.p.regulator_floor)

    # ---- one physics step --------------------------------------------------
    def step(self, throttle: Sequence[float]):
        p, s, dt = self.p, self.s, self.DT
        R = quat_to_mat(s.quat)
        m = self.mass
        force = (0.0, 0.0, -m * G0)
        torque_b = (0.0, 0.0, 0.0)

        # Thrusters (valve lag, blowdown, air consumption).
        fmax = self.thrust_limit()
        total_thrust = 0.0
        alpha = dt / (p.valve_tau + dt)
        for i in range(4):
            u = min(1.0, max(0.0, float(throttle[i])))
            s.thrust[i] += alpha * (u * fmax - s.thrust[i])
            f_b = (0.0, 0.0, s.thrust[i])
            force = add(force, mat_vec(R, f_b))
            torque_b = add(torque_b, cross(p.thruster_pos[i], f_b))
            total_thrust += s.thrust[i]
        s.air = max(0.0, s.air - total_thrust / (p.isp * G0) * dt)

        # Aero: quadratic drag per body axis on the air-relative velocity,
        # applied at the centre of pressure (-> weathervaning torques).
        self.wind.step(dt)
        self.wind_now = self.wind.at(s.pos[2])
        v_b = mat_t_vec(R, sub(s.vel, self.wind_now))
        f_aero_b = tuple(-0.5 * RHO_AIR * p.cda_body[k] * abs(v_b[k]) * v_b[k]
                         for k in range(3))
        force = add(force, mat_vec(R, f_aero_b))
        torque_b = add(torque_b, cross(p.cp_offset, f_aero_b))
        torque_b = add(torque_b, scale(s.omega, -p.ang_damping))

        omega_w = mat_vec(R, s.omega)
        fwd = (R[0][0], R[1][0], 0.0)
        lat = (R[0][1], R[1][1], 0.0)
        nf, nl = norm(fwd), norm(lat)
        fwd = scale(fwd, 1 / nf) if nf > 1e-6 else (1.0, 0.0, 0.0)
        lat = scale(lat, 1 / nl) if nl > 1e-6 else (0.0, 1.0, 0.0)

        # Wheels: suspension spring-damper + lateral grip + cruise traction.
        wheels_down = 0
        for wp in p.wheel_pos:
            r_w = mat_vec(R, wp)
            pw = add(s.pos, r_w)
            if pw[2] >= 0.0:
                continue
            v_pt = add(s.vel, cross(omega_w, r_w))
            if v_pt[2] < -p.max_landing_vz and not self.crashed:
                self.crashed = 'hard_landing'
            # Wheels only work when the car is roughly right side up.
            if R[2][2] < 0.3:
                continue
            wheels_down += 1
            n = max(0.0, -p.susp_k * pw[2] - p.susp_c * v_pt[2])
            v_lat = dot(v_pt, lat)
            f_lat = -max(-1.0, min(1.0, v_lat / 0.5)) * p.tire_mu * n
            v_long = dot(v_pt, fwd)
            f_long = max(-p.tire_mu * n, min(
                p.tire_mu * n, 2000.0 * (self.cruise_speed - v_long)))
            f = add(add(scale(lat, f_lat), scale(fwd, f_long)), (0.0, 0.0, n))
            force = add(force, f)
            torque_b = add(torque_b, mat_t_vec(R, cross(r_w, f)))
        self.wheels_down = wheels_down

        # Hull contact (and obstacle collision).
        hull_contact = False
        if self.obstacle and not self.crashed and self.obstacle.near(s.pos, 3.0) and any(
                self.obstacle.contains(add(s.pos, mat_vec(R, op))) for op in self._obstacle_pts):
            self.crashed = 'hit_obstacle'
        for hp, is_roof in self._hull:
            r_h = mat_vec(R, hp)
            ph = add(s.pos, r_h)
            if ph[2] >= 0.0:
                continue
            hull_contact = True
            v_pt = add(s.vel, cross(omega_w, r_h))
            if not self.crashed:
                if is_roof:
                    self.crashed = 'roof_contact'
                elif v_pt[2] < -p.max_hull_impact:
                    self.crashed = 'hull_impact'
            n = max(0.0, -p.hull_k * ph[2] - p.hull_c * v_pt[2])
            v_t = (v_pt[0], v_pt[1], 0.0)
            vt = norm(v_t)
            f_t = scale(v_t, -p.hull_mu * n / max(vt, 0.2))
            f = add(f_t, (0.0, 0.0, n))
            force = add(force, f)
            torque_b = add(torque_b, mat_t_vec(R, cross(r_h, f)))
        self.hull_contact = hull_contact

        # Integrate (semi-implicit Euler).
        acc = scale(force, 1 / m)
        s.vel = add(s.vel, scale(acc, dt))
        s.pos = add(s.pos, scale(s.vel, dt))
        I = self._inertia
        Iw = (I[0] * s.omega[0], I[1] * s.omega[1], I[2] * s.omega[2])
        gyro = cross(s.omega, Iw)
        s.omega = tuple(s.omega[k] + (torque_b[k] - gyro[k]) / I[k] * dt
                        for k in range(3))
        wx, wy, wz = s.omega
        s.quat = quat_normalize(add4(s.quat, scale4(
            quat_mul(s.quat, (0.0, wx, wy, wz)), 0.5 * dt)))
        s.roll_total += wx * dt
        s.pitch_total += wy * dt
        s.t += dt

    # ---- observation exposed to the controller ------------------------------
    def observe(self) -> dict:
        s = self.s
        R = quat_to_mat(s.quat)
        roll, pitch, yaw = quat_to_euler(s.quat)
        obs = {
            't': s.t,
            'x': s.pos[0], 'y': s.pos[1], 'z': s.pos[2],
            'vx': s.vel[0], 'vy': s.vel[1], 'vz': s.vel[2],
            'roll': roll, 'pitch': pitch, 'yaw': yaw,
            'roll_rate': s.omega[0], 'pitch_rate': s.omega[1],
            'yaw_rate': s.omega[2],
            'roll_total': s.roll_total, 'pitch_total': s.pitch_total,
            # World-z component of the body's up axis: +1 upright, -1 upside down.
            'up_z': R[2][2],
            # World-z component of body x (nose) and y (left side) axes.
            'nose_z': R[2][0], 'left_z': R[2][1],
            'wheels_on_ground': self.wheels_down,
            'air_frac': s.air / self.p.air_mass,
            'thrust_max_now': self.thrust_limit(),
            'mass': self.mass,
        }
        return obs


def add4(a, b):
    return tuple(a[i] + b[i] for i in range(4))


def scale4(a, s):
    return tuple(c * s for c in a)
