# Channel model

Code: {py:mod}`reiatsu.channel`.

## Expected RSSI

The expected RSSI of the device in cell $c$, as heard by receiver $r$, is the
log-distance path-loss model {cite:p}`rappaport2002` with an attenuation term per
crossed wall, the multi-wall model of {cite:t}`motley1988` that was adopted in COST 231
{cite:p}`cost231`:

$$
\mu_r(c) = P_{0,r} - 10\, n \log_{10} \max\big(d_r(c),\, d_\text{min}\big) - \sum_m W_m(r, c)\, L_m
$$ (eq-channel)

(`channel.predict`). Here:

- $d_r(c) = \sqrt{\lVert \mathbf{x}_c - \tilde{\mathbf{x}}_r \rVert^2 + (z - z_r)^2}$, with the device at $z = 1$ m and $d_\text{min} = 0.3$ m (`MIN_DIST`).
- $\tilde{\mathbf{x}}_r$ is the RF position (`rf_position`): a receiver listed within 0.1 m of its room's boundary (on a wall or in a corner) is moved 0.15 m towards the room's vertex mean, so it sits inside the room.
- $W_m(r, c)$ counts the walls of material $m$ that the segment $\tilde{\mathbf{x}}_r \to \mathbf{x}_c$ *properly* crosses (`wall_counts`, `plan.crosses`). Segments $\mathbf{p}\mathbf{q}$ and $\mathbf{a}\mathbf{b}$ cross when both pairs of orientation tests have strictly opposite signs:
  $$(\mathbf{d} \times (\mathbf{a}-\mathbf{p}))\,(\mathbf{d} \times (\mathbf{b}-\mathbf{p})) < 0 \;\wedge\; (\mathbf{e} \times (\mathbf{p}-\mathbf{a}))\,(\mathbf{e} \times (\mathbf{q}-\mathbf{a})) < 0, \quad \mathbf{d} = \mathbf{q}-\mathbf{p},\ \mathbf{e} = \mathbf{b}-\mathbf{a}.$$
  Doors are gaps, so a path through a doorway crosses nothing.
- A device's transmit offset $o$ comes on top of this: the tracker marginalises it ({eq}`eq-update`), and calibration fits it per survey group (see {doc}`notation`).

A calibration adds a Gaussian-process residual map $g_r(c)$ (see {doc}`calibration`):
$\mu_r(c) \leftarrow \mu_r(c) + g_r(c)$ (`Calibration.predict`).

Wall-loss priors (`LOSS_PRIOR`, mean ± sd in dB): drywall 3 ± 1.5, brick 10 ± 3,
concrete 15 ± 4, glass 2 ± 1.

## Observation likelihood

The per-second observation $y_r$ is the median RSSI of the adverts heard from the
device by receiver $r$ in that second, or ∅ if none were heard (`Locator.tick`). Given
the expected value $\mu = \mu_r(c)$:

$$
p(y_r \mid c) =
\begin{cases}
(1 - p_\text{drop})\; \dfrac{1}{\sigma}\, t_\nu\!\left(\dfrac{y_r - \mu}{\sigma}\right) & \text{heard} \\[2ex]
p_\text{drop} + (1 - p_\text{drop})\, F_\nu\!\left(\dfrac{s - \mu}{\sigma}\right) & \text{not heard}
\end{cases}
$$ (eq-likelihood)

(`Likelihood.__call__`). $t_\nu$ and $F_\nu$ are the Student-t density and CDF with
$\nu = 4$; $\sigma = 6$ dB, $s = -100$ dBm and $p_\text{drop} = 0.1$. The heavy tails keep
body shadowing and multipath outliers from dominating, the usual reason for a Student-t
error model {cite:p}`lange1989`. The not-heard case is a censored observation: the value
is only known to lie below the sensitivity {cite:p}`tobin1958`. The "not heard" term is real
evidence: a receiver predicted to hear the device at −70 dBm that hears nothing makes that
cell unlikely. Silence only counts from receivers that are **online**, i.e. listed on
`reiatsu/receivers` (`Tracker.set_listening`). An offline proxy says nothing.

Receivers are treated as independent given the cell:
$p(\mathbf{y} \mid c) = \prod_r p(y_r \mid c)$.

```{note}
Planned (v0.5): a per-receiver detection curve
$p(\text{heard} \mid \mu) = p_\text{max}\, \operatorname{logistic}\big((\mu - s_r)/\beta_r\big)$ with fitted
sensitivity $s_r$ and slope $\beta_r$, plus a per-receiver noise scale $\sigma_r$. These
replace the fixed $s$ and $p_\text{drop}$. See {doc}`learning`.
```
