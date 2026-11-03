"""Regenerates fig_Y_envelope.png from the .npy files written by replicate.py."""
import os, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
H = os.path.dirname(os.path.abspath(__file__))
Y = np.load(os.path.join(H, "co_traj_Y.npy")); E = np.load(os.path.join(H, "env_Y.npy")); t = np.arange(len(Y)) * 0.1
fig, ax = plt.subplots(figsize=(7, 4), dpi=160)
ax.fill_between(t, E.min(0), E.max(0), color="#b7d3f6", lw=0, label="spread of 200 random feasible strategies (not the reachable set)")
ax.plot(t, Y, color="#2a78d6", lw=2, label="dFBA, growth-optimal (Chiu rules)")
ax.plot(t, np.zeros_like(t), color="#eb6834", lw=2, ls="--", label="Organism 1 alone: Y = 0 in every run")
ax.set_xlabel("time (h)"); ax.set_ylabel("Y in medium (mM)")
ax.set_title("Co-culture Y: optimal dFBA is one strategy; others reach more", fontsize=10, loc="left")
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.grid(axis="y", color="#e6e6e6", lw=0.6); ax.legend(frameon=False, fontsize=8, loc="upper left")
fig.tight_layout(); fig.savefig(os.path.join(H, "fig_Y_envelope.png"))
