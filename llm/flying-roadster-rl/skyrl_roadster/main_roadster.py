"""SkyRL entrypoint: GRPO on the flying-roadster pilot environment.

Copied into a SkyRL checkout as ``examples/train/roadster/`` and run from the
SkyRL root (same layout as SkyRL's own ``examples/train/multiply``):

    uv run --isolated --extra fsdp -m examples.train.roadster.main_roadster <overrides>
"""
import sys

import ray
from skyrl.train.config import SkyRLTrainConfig
from skyrl.train.entrypoints.main_base import BasePPOExp, validate_cfg
from skyrl.train.utils import initialize_ray
from skyrl_gym.envs import register


@ray.remote(num_cpus=1)
def skyrl_entrypoint(cfg: SkyRLTrainConfig):
    # Register inside the Ray task so no change to the skyrl-gym package is needed.
    register(id='roadster_pilot', entry_point='examples.train.roadster.pilot_env:PilotEnv')
    BasePPOExp(cfg).run()


def main() -> None:
    cfg = SkyRLTrainConfig.from_cli_overrides(sys.argv[1:])
    validate_cfg(cfg)
    initialize_ray(cfg)
    ray.get(skyrl_entrypoint.remote(cfg))


if __name__ == '__main__':
    main()
