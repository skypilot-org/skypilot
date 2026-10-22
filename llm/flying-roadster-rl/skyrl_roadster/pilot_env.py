"""SkyRL-Gym environment: the LLM IS the pilot. One turn = one decision.

Every turn the model replies with one JSON action (see prompts/pilot_system.md),
the flight computer flies it for 0.2 s of sim time, and the next turn's user
message is the new observation line. The episode ends when the car lands or
crashes; the reward is the episode reward (same scoring as code mode).

The pilot only emits JSON, never code, so the simulator runs in-process (no
separate simulator service). Per-row extras (see ``make_dataset.py``):
  task_json    the stunt + conditions (JSON string)

``handover_steps`` (optional extra): the scripted pilot flies that many
decisions first and the model takes over mid-flight (reverse curriculum; the
dataset builds the matching first observation).

An unparseable reply keeps the previous action running, as a late or garbled
command would on a real vehicle. If the turn budget runs out mid-flight the
last action keeps flying until the episode ends.
"""
import json
import os
import sys
from typing import Any, Dict

from skyrl_gym.envs.base_text_env import BaseTextEnv, BaseTextEnvStepOutput

# The demo root (roadster_env/) is put on PYTHONPATH by the launcher; fall back
# to the sky_workdir layout.
sys.path.insert(0, os.path.expanduser('~/sky_workdir'))
from roadster_env.pilot import ActionError, handover, obs_text, parse_action  # noqa: E402


class PilotEnv(BaseTextEnv):

    def __init__(self, env_config: Dict[str, Any] = None,
                 extras: Dict[str, Any] = None):
        super().__init__()
        extras = extras or {}
        self.task = json.loads(extras['task_json'])
        self.max_turns = int(extras.get('max_turns', 40))
        self.ep = handover(self.task, int(extras.get('handover_steps') or 0))
        self.invalid = 0

    def step(self, action: str) -> BaseTextEnvStepOutput:
        self.turns += 1
        try:
            act = parse_action(action)
        except ActionError:
            act, self.invalid = None, self.invalid + 1
        self.ep.invalid = self.invalid
        self.ep.step(act)
        if not self.ep.done and self.turns >= self.max_turns:
            while not self.ep.done:  # out of turns: the last action keeps flying
                self.ep.step(None)
        if self.ep.done:
            reward = float(self.ep.result.get('reward', 0.0))
            return BaseTextEnvStepOutput(observations=[], reward=reward, done=True,
                                         metadata={'reward': reward, 'crashed': self.ep.result.get('crashed')})
        obs = obs_text(self.ep.obs, self.task, self.ep.action)
        return BaseTextEnvStepOutput(observations=[{'role': 'user', 'content': obs}],
                                     reward=0.0, done=False, metadata={})

    def get_metrics(self) -> Dict[str, Any]:
        r = self.ep.result or {}
        return {
            'success': float(bool(r.get('success'))),
            'landed': float(bool(r.get('landed'))),
            'crashed': float(bool(r.get('crashed'))),
            'on_road': float(bool(r.get('on_road'))),
            'invalid_actions': float(self.invalid),
            'turns': float(self.turns),
        }
