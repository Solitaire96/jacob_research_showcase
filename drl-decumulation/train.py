"""Train one agent on TRAIN-window bootstrap paths and save it to checkpoints/.

    python3 train.py --algo ddpg --seed 0 --steps 200000

Every random source is seeded (torch, numpy, the environment, action noise), so a
given (algo, seed, steps) reproduces up to floating-point nondeterminism in torch.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import DDPG, TD3
from stable_baselines3.common.noise import NormalActionNoise
from stable_baselines3.common.utils import set_random_seed

from decumulation.env import DecumulationEnv
from decumulation.sim import Config

ROOT = Path(__file__).resolve().parent
ALGOS = {"ddpg": DDPG, "td3": TD3}


def train(algo: str, seed: int, steps: int, gamma_crra: float = 4.0, out_dir: Path = ROOT / "checkpoints",
          use_macro: bool = False, leaky: bool = False):
    set_random_seed(seed)
    torch.set_num_threads(1)
    cfg = Config(gamma=gamma_crra)
    env = DecumulationEnv(cfg, use_macro=use_macro or leaky, macro_lag="source" if leaky else "path")
    env.reset(seed=seed)
    env.action_space.seed(seed)
    noise = NormalActionNoise(mean=np.zeros(2), sigma=0.15 * np.ones(2))
    model = ALGOS[algo](
        "MlpPolicy", env,
        action_noise=noise,
        gamma=env.learner_gamma,        # beta^12: same time preference the evaluator uses
        learning_rate=3e-4,
        buffer_size=200_000,
        learning_starts=2_000,
        batch_size=256,
        tau=0.005,
        policy_kwargs={"net_arch": [64, 64]},
        seed=seed,
        verbose=0,
    )
    t0 = time.time()
    model.learn(total_timesteps=steps)
    out_dir.mkdir(exist_ok=True)
    tag = "_leaky" if leaky else ("_macro" if use_macro else "")
    name = f"{algo}{tag}_g{gamma_crra:g}_seed{seed}"
    model.save(out_dir / name)
    meta = {"algo": algo, "seed": seed, "steps": steps, "use_macro": use_macro or leaky, "leaky_macro_lag": leaky, "config": cfg.as_dict(),
            "train_window": list(__import__("decumulation.data", fromlist=["TRAIN"]).TRAIN),
            "wall_seconds": round(time.time() - t0, 1)}
    (out_dir / f"{name}.json").write_text(json.dumps(meta, indent=2))
    print(f"saved {name} in {meta['wall_seconds']}s")
    return out_dir / f"{name}.zip"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--algo", choices=sorted(ALGOS), default="ddpg")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--steps", type=int, default=200_000)
    ap.add_argument("--gamma-crra", type=float, default=4.0)
    ap.add_argument("--macro", action="store_true", help="ablation: add path-lagged macro features")
    ap.add_argument("--leaky", action="store_true",
                    help="reproduce the leak: macro features lagged along source history")
    a = ap.parse_args()
    train(a.algo, a.seed, a.steps, a.gamma_crra, use_macro=a.macro, leaky=a.leaky)
