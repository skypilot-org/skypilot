You are the flight-control engineer for a 2-seat electric roadster fitted with four cold-gas (compressed-air) thrusters. You write the controller code that flies the car through a stunt. Your code is executed in a physics simulator.

## Vehicle
- Mass ~{mass:.0f} kg including air. Body 4.5 m long, 1.9 m wide, 1.1 m tall. Roll inertia ~800 kg·m², pitch inertia ~3600 kg·m².
- Four thrusters fixed to the floor corners, all firing along the car's own "up" axis (they push the car away from its floor):
  - index 0 = front-left  at (x=+1.5, y=+0.85)
  - index 1 = front-right at (x=+1.5, y=-0.85)
  - index 2 = rear-left   at (x=-1.5, y=+0.85)
  - index 3 = rear-right  at (x=-1.5, y=-0.85)
  (x forward, y to the driver's left, metres from the centre of mass)
  Pairs: LEFT side = 0 and 2, RIGHT side = 1 and 3, FRONT = 0 and 1, REAR = 2 and 3.
- Each thruster gives up to {thrust_max:.0f} N at throttle 1.0 (all four ≈ {thrust_total:.0f} N, about {tw:.1f}× the car's weight). Valves lag ~30 ms.
- Air supply: {air_mass:.0f} kg of air, Isp ≈ 65 s, so full throttle on all four empties the tank in about {burn_time:.1f} s. Thrust stays full until 25 % of the air is left, then fades to zero. Use air sparingly.
- Firing the left pair harder than the right rolls the car to the right (positive roll rate); firing the front pair harder pitches the nose UP (negative pitch rate). When the car is upside down the thrusters push it TOWARD the ground.
- Wheels hold the car on the road and keep it at its cruise speed while all four touch the ground.
- Air: quadratic drag per body axis (small from the front, ~5x larger from the side, ~9x from above or below), acting slightly behind and above the centre of mass, so a side wind pushes the car sideways and turns its nose into the wind. Gusts are random; `obs` does not include the wind, but you can infer it from how the car drifts.

## Crash rules
The run fails if the roof or side of the body touches the ground, the floor hits the ground faster than 3 m/s, the wheels touch down faster than 9 m/s, or the body touches an obstacle. A landing counts when all four wheels are down ON THE ROAD, the car is within ~20° of level and its spin has stopped. The road runs along x, centred on y = 0 and 14 m wide, so land with |y| under ~6 m; closer to the centre line is better.

## Controller API
Write a Python function

```python
def controller(obs, mem):
    ...
    return [u0, u1, u2, u3]   # throttles, each clipped to [0, 1]
```

It is called 50 times per second for up to 6 s. `mem` is a dict that persists between calls (use it for state machines). Only the Python standard library is available (`math` is fine). There is no numpy, network or file access. Each call must finish within a few milliseconds.

## Flight-control SDK
A global `fc` is available in your code (no import needed). It does the low-level control so you can write the flight logic:
- `fc.mix(total_N, tau_roll, tau_pitch, obs)` -> throttles. Turns a total thrust (N, along the car's up axis) and body torques (N·m) into the 4 throttles; torque has priority when saturated.
- `fc.hold_attitude(obs, roll_target=0.0, pitch_target=0.0)` -> `(tau_roll, tau_pitch)`. PD torques toward a target attitude (radians); angle errors wrap, so it takes the short way round, even from upside down.
- `fc.vz_thrust(obs, vz_target)` -> total thrust (N) to track a vertical speed; returns 0 while the car is upside down or on its side.
- `fc.landing_vz(obs)` -> a late-braking descent speed (negative m/s) for the current height: fall fast while high, brake late, ~1.5 m/s at touchdown.
- `fc.spin(obs, 'roll' | 'pitch', direction=+1, collective=0.0)` -> throttles for a maximum-rate spin with the least collective thrust (add N of `collective` for lift).
- `fc.brake_angle(obs, 'roll' | 'pitch')` -> how much more rotation (rad) it takes to stop the current spin at full counter-torque.
- Constants: `fc.G`, `fc.ROLL_ACCEL_MAX` (~25 rad/s²), `fc.PITCH_ACCEL_MAX` (~10 rad/s²).
You decide the strategy: the phases, when each ends (based on `obs`, not just on time), heights, spin rates and how to save air. Using `obs` feedback matters: speeds, heights and wind vary between runs.

`obs` keys (SI units, radians):
- `t` time since start; `x`, `y`, `z` position of the centre of mass (`z` ≈ 0.29 when parked on the road)
- `vx`, `vy`, `vz` world velocity
- `roll`, `pitch`, `yaw` Euler angles (ZYX) of the body
- `roll_rate`, `pitch_rate`, `yaw_rate` body angular velocity
- `roll_total`, `pitch_total` accumulated rotation since t=0 (one full roll ≈ ±6.283)
- `up_z` vertical component of the car's up axis (+1 upright, −1 upside down); `nose_z`, `left_z` vertical components of its forward and left axes
- `wheels_on_ground` (0–4), `air_frac` air remaining (0–1), `thrust_max_now` per-thruster thrust available right now (N), `mass` (kg)
- `task` the task name; for obstacle tasks also `obstacle_x0` (x where the obstacle starts), `obstacle_length`, `obstacle_height`; for the ramp stunt also `launch_height`

## Output format
Be concise. First a short plan: at most 5 bullet points (under 120 words) covering the phases and when each one ends. Then exactly one ```python code block with the full controller, under about 80 lines. No other text after the code block.
