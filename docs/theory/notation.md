# Notation

Units: metres, dBm/dB, seconds. Defaults are the values in the code; each theory page
names the function or parameter next to its equation.

| symbol | meaning | default | in code |
|---|---|---|---|
| $c \in \mathcal{C}$ | grid cell (centre $\mathbf{x}_c$), $C = \lvert\mathcal{C}\rvert$ | 0.2 m grid | `plan.make_grid(res)` |
| $r$ | receiver (proxy) with position $\mathbf{x}_r$, height $z_r$ | | `plan.Receiver` |
| $P_{0,r}$ | RSSI at 1 m from receiver $r$ | prior $\mathcal{N}(-59, 6^2)$ or `p0` | `ChannelParams.p0` |
| $n$ | path-loss exponent | prior $\mathcal{N}(2, 0.4^2)$ | `ChannelParams.n` |
| $L_m$ | loss per wall of material $m$ | see {doc}`channel` | `ChannelParams.loss` |
| $W_m(r, c)$ | number of walls of material $m$ crossed from $r$ to $c$ | | `channel.wall_counts` |
| $d_r(c)$ | 3-D distance receiver → cell | min 0.3 m | `channel.geometry`, `MIN_DIST` |
| $\mu_r(c)$ | expected RSSI | | `channel.predict`, `Calibration.predict` |
| $y_r$ | observed RSSI this second (median of adverts), or ∅ if not heard | | `Locator.tick` |
| $\sigma, \nu$ | Student-t scale and degrees of freedom | 6 dB, 4 | `Likelihood.sigma`, `.nu` |
| $s$ | receiver sensitivity | −100 dBm | `Likelihood.sensitivity` |
| $p_\text{drop}$ | probability an advert is lost regardless of signal | 0.1 | `Likelihood.p_drop` |
| $b_t(c)$ | belief: probability the device is in cell $c$ at second $t$ | | `Tracker.belief` |
| $K$ | one-second motion kernel | $s_K$ = 0.7 m | `tracker.motion_kernel` |
| $p_\text{move}$ | per-second probability of taking a step | 0.15, 0.01 when silent | `Tracker.p_move`, `.p_move_silent` |
| $p_\text{jump}$ | per-second leak to anywhere | 3·10⁻³ | `Tracker.p_jump` |
| $\lambda$ | evidence forgetting per heard step | 0.1 | `Tracker.evidence_forget` |
| $o$ | a device's transmit offset relative to the calibration phone (one quantity, see below) | | |
| $o_k, w_k$ | the tracker's offset grid ($k = 1 \dots N_o$) and its weights | −12…12 dB step 2 | `Tracker.offsets`, `.offset_w` |
| $q, \pi$ | mmWave miss rate; P(occupied ∣ device elsewhere) | 0.15, 0.5 | `Tracker.mmwave_fnr`, `.mmwave_p_other` |
| $\tau$ | seconds of a sensor state worth one observation | 5 | `Tracker.mmwave_tau` |
| $h$ | room hysteresis | 0.3 | `Tracker.hysteresis` |
| $\sigma_\text{pos}$ | position spread within the reported room | | `Estimate.sigma` |
| $k_\text{GP}, \mathbf{G}$ | GP kernel and Gram matrix (length scale $\eta$, scale $s_f$, noise $\sigma_n$) | 1.5 m, 3 dB, 2 dB | `ResidualGP` |
| $\delta_g$ | transmit offset of survey group $g$ (device × phone position) | prior $\mathcal{N}(0, 10^2)$ | `Calibration.offsets` |

**One offset, two estimates.** A device's transmit offset $o$ (its power relative to the
phone the survey was done with) never enters `channel.predict`. The tracker marginalises
it online over the grid $o_k$ ({doc}`tracking`). Calibration fits it per survey group as
$\delta_g$, where a group is a device × phone position, so body shadowing is part of it
({doc}`calibration`).
