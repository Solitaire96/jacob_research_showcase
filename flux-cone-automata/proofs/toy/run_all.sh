#!/bin/sh
# Re-run every KeYmaera X proof. Needs Java 17+ and keymaerax.jar (5.1.2) at $KYX.
KYX=${KYX:-keymaerax.jar}
for f in P1_mono1_no_Y P1x_mono1_no_Y_exact_exp P2_co_no_overdraw P2x_co_no_overdraw_exact_exp P3L_exp_interval_lemma P4_step_sound P5_step_complete P5r_successor_admissible P4a_org1_step_sound P5a_org1_step_complete P4b_org2_step_sound P5b_org2_step_complete P6a_org1_forced_chain_optimal P6b_org2_controlled_optimal P7_trajectory_sound P8_trajectory_complete P8g_trajectory_complete_game P2c_thesis_bound_counterexample P3_co_emergence P2b_thesis_bound_overdraws; do
  java -Xss20M -jar "$KYX" -launch -prove "$f.kyx" -timeout 300 2>&1 | grep -E "^(PROVED|FAILED|UNFINISHED)"
done
