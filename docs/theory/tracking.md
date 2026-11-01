# Tracking

Code: {py:class}`reiatsu.tracker.Tracker`, {py:class}`reiatsu.locator.Locator`.

Each device has its own grid HMM: a belief $b_t(c)$ over the walkable cells, advanced
once per second (`Locator.tick` → `Tracker.step`). The state is the cell, with a
discrete transmit offset marginalised alongside.

## Prediction: door-constrained motion

The one-second motion kernel is a Gaussian in **walking** distance
(`motion_kernel`, $s_K = 0.7$ m, truncated at $3 s_K$):

$$
K(c' \mid c) = \frac{\exp\!\big(-d_G(c, c')^2 / 2 s_K^2\big)}{\sum_{c''} \exp\!\big(-d_G(c, c'')^2 / 2 s_K^2\big)},
\qquad d_G(c, c') \le 3 s_K .
$$

Because $d_G$ is measured on the walkable graph (see {doc}`geometry`), probability flows
between rooms only through doors. Each second

$$
\tilde b(c') = (1 - p)\, b_{t}(c') + p \sum_c K(c' \mid c)\, b_{t}(c) + \frac{j}{C},
\qquad
b_{t+1} \propto \tilde b \odot f_\text{occ}
$$ (eq-predict)

where $f_\text{occ}$ is the mmWave factor from {doc}`fusion`, and:

- $p = p_\text{move} = 0.15$ while the device has been heard within the last 30 s (`silent_after`), else $p_\text{move,silent} = 0.01$. A locked iPhone can be silent for minutes, and its belief should hold rather than smear.
- $j = p_\text{jump} = 3 \cdot 10^{-3}$ leaks a little mass everywhere, so the device can still be found in a far room after an unseen walk.
- For a device nobody carries (`Tracker.carried = False`, set for the plan's reference devices) $p = 0$ and $j = p_\text{jump}/100$.

## Update: RSSI evidence with an unknown transmit offset

Phones differ in transmit power, so the tracker keeps a weight $w_k$ for each offset
$o_k \in \{-12, -10, \dots, 12\}$ dB (`Tracker.offsets`), with prior
$w_k \propto \exp(-o_k^2 / 2 \cdot 6^2)$ (`offset_sd`). When at least one receiver heard
the device:

$$
\ell_k(c) = \sum_{r \in \mathcal{R}_t} \log p\big(y_r \mid \mu_r(c) + o_k\big),
\qquad
J_{kc} = e^{\ell_k(c)}\; b_t(c)^{\,1-\lambda}\; w_k
$$ (eq-update)

$$
b_{t+1}(c) = \frac{\sum_k J_{kc}}{\sum_{k,c} J_{kc}},
\qquad
w_k \leftarrow (1-\phi)\, \frac{\sum_c J_{kc}}{\sum_{k,c} J_{kc}} + \frac{\phi}{K_o}
$$

$\mathcal{R}_t$ is the set of receivers that heard the device, plus the online ones that
didn't. $p(y_r \mid \cdot)$ is {eq}`eq-likelihood`. The offset weights forget slowly
($\phi$ = `offset_forget` = $10^{-3}$, $K_o$ offsets), so they can follow a change.

### Evidence forgetting

The exponent $1 - \lambda$ ($\lambda$ = `evidence_forget` = 0.1) discounts old evidence.
Without it, a device that doesn't move multiplies in the same reading every second. But
its error is mostly **persistent** (shadowing, map error), not independent noise, so
600 identical readings would claim 600 times the certainty they carry. With forgetting,
a constant likelihood $L$ converges to the fixed point

$$
b^\* \propto L^{1/\lambda},
$$

which is worth at most $1/\lambda = 10$ independent observations. In practice, one
receiver at −87 dBm keeps the top room around 0.6–0.7 instead of collapsing to 1.0
within a minute (`test_single_weak_receiver_stays_uncertain`).

## Output

Room probabilities are the belief summed per room:
$P_t(A) = \sum_{c:\, \rho(c) = A} b_t(c)$. The reported room switches from the current
$A_t$ to $A^\* = \arg\max_A P_t(A)$ only if $P_t(A^\*) > P_t(A_t) + h$ ($h$ =
`hysteresis` = 0.3). The position and its spread come from the belief inside the
reported room, renormalised to $\tilde b$:

$$
\hat{\mathbf{x}} = \sum_{c \in A_t} \tilde b(c)\, \mathbf{x}_c,
\qquad
\sigma = \Big(\sum_{c \in A_t} \tilde b(c)\, \lVert \mathbf{x}_c - \hat{\mathbf{x}} \rVert^2\Big)^{1/2}.
$$

These are the `room`, `room_probs`, `x`, `y` and `sigma` of
{py:class}`reiatsu.tracker.Estimate`, published to `reiatsu/<device>/state`.
