#!/bin/sh
# Reproduce the shipped checkpoints, two at a time (about 75 minutes on a 2-core CPU):
#   main:     DDPG and TD3, seeds 0-4, observation = wealth and age
#   ablation: DDPG and TD3, seeds 0-1, plus path-lagged macro features and last return
#   leak:     DDPG, seeds 0-1, macro lagged along SOURCE history (reproduces the bug)
# Then: python3 evaluate.py
set -e
cd "$(dirname "$0")"
mkdir -p logs
{
  for algo in ddpg td3; do for s in 0 1 2 3 4; do echo "$algo $s ''"; done; done
  for algo in ddpg td3; do for s in 0 1; do echo "$algo $s --macro"; done; done
  for s in 0 1; do echo "ddpg $s --leaky"; done
} | xargs -P 2 -n 3 sh -c 'python3 train.py --algo "$0" --seed "$1" --steps 150000 $2 > "logs/$0$2_seed$1.log" 2>&1'
touch logs/ALL_DONE
