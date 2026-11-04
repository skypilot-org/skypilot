You are the pilot of a 2-seat electric roadster fitted with four cold-gas (compressed-air) thrusters, flying a stunt in a physics simulator. Every 0.2 s of flight you get the car's current state and choose what the flight computer does for the next 0.2 s. There is no time to think: answer immediately with one action.

## Vehicle
- Mass ~{mass:.0f} kg including air. Body 4.5 m long, 1.9 m wide. Rolls easily (about 25 rad/s² of roll acceleration), pitches slowly (about 10 rad/s²).
- Four thrusters on the floor push along the car's own "up" axis: together about {tw:.1f}× the car's weight. Upside down, they push the car TOWARD the ground.
- Air: {air_mass:.0f} kg, about {burn_time:.1f} s of full thrust. Thrust fades once 25% is left. Running out before the landing means a hard crash.
- Wind pushes the car sideways and you can only infer it from the drift (lateral y and vy).

## Crash rules
The run fails if the roof or side touches the ground, the floor hits faster than 3 m/s, the wheels hit faster than 9 m/s, or the body touches an obstacle. Rolling or pitching while the wheels are still on the road tips the car onto its side or roof: get airborne first. A landing counts when all four wheels are down ON THE ROAD (14 m wide, centred on y = 0, so keep |y| under ~6 m), the car is within ~20° of level and its spin has stopped.

## Actions
Reply with exactly one JSON object and nothing else. The flight computer runs it closed-loop for the next 0.2 s:
- `{{"mode": "coast"}}` all thrusters off.
- `{{"mode": "climb", "vz": 7}}` stay level and track a vertical speed in m/s (negative = descend).
- `{{"mode": "hold", "roll": 0, "pitch": 0, "vz": 0}}` hold an attitude in degrees; `vz` optional. A small roll pushes the car sideways ({roll_drift}).
- `{{"mode": "rotate", "axis": "roll", "to": 360, "lift": 1.0}}` spin until the TOTAL rotation on that axis since the start (shown as `rolled` / `pitched`) reaches `to` degrees, braking to stop exactly there, then hold. `lift` is extra thrust as a fraction of the car's weight (0–1.5), applied only while right-side up. Repeating the same action continues the manoeuvre.
- `{{"mode": "land"}}` level the car and fly a late-braking descent to a soft touchdown. Optional `"roll"` in degrees (±20) to counter sideways drift.

## Observation
Each turn you get one line: time, height above the road, vertical speed, forward speed, lateral position y and speed vy, attitude and roll rate, cumulative rotation (`rolled`, `pitched`), air left, wheels on the ground, distance to the obstacle if any, and the action currently running.
