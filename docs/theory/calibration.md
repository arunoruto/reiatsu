# Calibration

Code: {py:mod}`reiatsu.calibrate`, {py:mod}`reiatsu.survey`.

Calibration fits the parameters of {eq}`eq-channel` to labelled measurements and adds a
residual map for what the physics misses. In the web UI it runs from
*Survey → Fit… → Apply*, which writes `calibration.json`.

## Data

**Survey points.** At a known position $\mathbf{x}_i$, the phone advertises for about
30 s while the person turns once. For each receiver, Reiatsu keeps the **censored median**
over all adverts *sent* (`censored_median`), treating the unheard ones as censored
observations {cite:p}`tobin1958`. The unheard ones were either dropped at
random (probability $p_\text{drop}$) or below sensitivity, so they rank at the bottom.
With $N$ adverts sent and the heard values sorted in descending order $v_{(1)} \ge v_{(2)} \ge \dots$,

$$
\tilde y_{ir} = v_{(\lfloor N (1 - p_\text{drop}) / 2 \rfloor + 1)},
$$

or *censored* (∅) if fewer values were heard. The median of only the heard values would
bias weak, through-wall receivers high; in the simulator it fitted brick and concrete
1.4 and 2.9 dB too low. The number sent is estimated from the receiver that heard the
most: $N = \operatorname{round}(\max_r n_{ir} / (1 - p_\text{drop}))$ (`survey.n_sent`).

**Reference devices.** A device placed as a reference contributes its last 10 minutes
of adverts as one more point at its known spot (`Survey.reference_points`).

**Node links.** Receiver $u$ hearing receiver $v$ beacon gives an observation at $v$'s RF
position (`NodeLink`). Nothing in the real pipeline produces these yet; the simulator uses
them.

## MAP fit

Parameters $\theta = (P_{0,r}, n, L_m, \delta_g)$. Every observation $i$ (receiver
$r_i$, offset group $g_i$, distance $d_i$, wall counts $\mathbf{W}_i$) predicts

$$
\hat y_i(\theta) = P_{0,r_i} + \delta_{g_i} - 10\, n \log_{10} d_i - \mathbf{W}_i^\top \mathbf{L}.
$$

Offset groups are (device, phone position) pairs, plus one for node links. The most common
group is the reference with $\delta = 0$. The others get their own transmit offset, so a
door lock, or a phone lying on a table without body shadowing, doesn't bias the
receivers' $P_0$ (`offset_groups`). The residuals are

$$
e_i = \frac{w_i}{\sigma_\text{fit}}
\begin{cases}
\hat y_i - \tilde y_i & \text{heard} \\
\max(0,\ \hat y_i - s) & \text{censored}
\end{cases}
\qquad
e_j^\text{prior} = \frac{\theta_j - m_j}{s_j}
$$

with $\sigma_\text{fit} = 3$ dB. A censored observation only penalises predictions above
the sensitivity. The estimate minimises the Huber loss {cite:p}`huber1964` over all residuals
(`scipy.optimize.least_squares(loss="huber", f_scale=1)`):

$$
\hat\theta = \arg\min_{\theta \in \Theta} \sum_i h(e_i) + \sum_j h(e^\text{prior}_j),
\qquad
h(e) = \begin{cases} e^2 & \lvert e \rvert \le 1 \\ 2 \lvert e \rvert - 1 & \text{otherwise} \end{cases}
$$ (eq-fit)

with priors $P_{0,r} \sim \mathcal{N}(-59, 6^2)$ (or the plan's `p0` as the mean),
$n \sim \mathcal{N}(2, 0.4^2)$, $L_m$ from `LOSS_PRIOR`, $\delta_g \sim \mathcal{N}(0, 10^2)$.
The bounds $\Theta$ are $P_0 \in [-100, -20]$, $n \in [1.2, 5]$, $L \in [0, 40]$,
$\delta \in [-30, 30]$. Weak labels (a room but no position) are placed at their most
likely cell in that room and refitted with weight 0.3 (`WeakLabel`).

## Residual map

What the walls-and-distance model can't explain (furniture, a metal cabinet) is learned
per receiver by Gaussian-process regression {cite:p}`rasmussen2006` on the reference
group's residuals $\varepsilon_i = \tilde y_{ir} - \hat\mu_r(\mathbf{x}_i)$
(`ResidualGP`). This makes the model a hybrid: a physical model, plus RSSI fingerprints
measured at known spots, the approach introduced by RADAR {cite:p}`bahl2000`.

$$
k_\text{GP}(\mathbf{x}, \mathbf{x}') = s_f^2 \exp\!\left(-\frac{\lVert \mathbf{x} - \mathbf{x}' \rVert^2}{2\eta^2}\right),
\quad
g_r(\mathbf{x}) = \mathbf{k}_\mathbf{x}^\top (\mathbf{G} + \sigma_n^2 I)^{-1} \boldsymbol{\varepsilon},
\quad
v_r(\mathbf{x}) = s_f^2 - \mathbf{k}_\mathbf{x}^\top (\mathbf{G} + \sigma_n^2 I)^{-1} \mathbf{k}_\mathbf{x}
$$

with $\mathbf{G}_{ij} = k_\text{GP}(\mathbf{x}_i, \mathbf{x}_j)$,
$(\mathbf{k}_\mathbf{x})_i = k_\text{GP}(\mathbf{x}, \mathbf{x}_i)$, $\eta = 1.5$ m,
$s_f = 3$ dB and $\sigma_n = 2$ dB. The tracker uses
$\mu_r + g_r$. The isotropic kernel ignores walls; a wall-aware kernel is the upgrade if
residuals jump at walls.

## Evaluation

The Fit table compares the current calibration on the points with the new fit,
**leave-one-out**: each point $i$ is located with $\hat\theta^{(-i)}$, fitted on all the
other points, residual GP included (`survey.leave_one_out`, `survey.locate`),

$$
\hat c_i = \arg\max_c \sum_r \log p\big(\tilde y_{ir} \mid \mu^{(-i)}_r(c) + \delta^{(-i)}_{g_i}\big),
$$

where $\delta^{(-i)}_{g_i} = 0$ if no other point shares the group of point $i$.

which gives room accuracy $\frac{1}{N}\sum_i \mathbb{1}[\rho(\hat c_i) = \rho(\mathbf{x}_i)]$, the
median error $\lVert \mathbf{x}_{\hat c_i} - \mathbf{x}_i \rVert$, and the mean absolute
RSSI error over heard receivers.

## Survey planner

**Starter set** (`starter_targets`): room centres, a point about 1 m from each receiver,
a pair of points 0.6 m either side of each interior wall at least 0.6 m long (a side is
skipped if no grid cell lies within 0.3 m of it), and every doorway. Points closer than
0.5 m to an earlier one are merged. The wizard visits them nearest-first, and
a target counts as done once a captured point lies within 0.75 m.

**Active points** (`next_point`): the cell whose fingerprint is both uncertain and easy to
confuse with another room. With $\boldsymbol{\mu}(c) \in \mathbb{R}^R$,

$$
\kappa(c) = 1 - \frac{\sum_{c':\, \rho(c') = \rho(c)} e^{-\lVert \boldsymbol\mu(c) - \boldsymbol\mu(c') \rVert^2 / 2 \cdot 5^2}}{\sum_{c'} e^{-\lVert \boldsymbol\mu(c) - \boldsymbol\mu(c') \rVert^2 / 2 \cdot 5^2}},
\qquad
\text{score}(c) = \big(\kappa(c) + 0.05\big)\; \frac{1}{R}\sum_r v_r(c),
$$

excluding cells within 0.75 m of points already captured. The UI says "good enough" once
the starter set is done (the next target comes from the planner) and the leave-one-out
accuracy has changed by at most 3 points over the last three captured points. In the simulator, active points did no better than random ones after the starter
set: the map error is already about 1.7 dB, and per-advert noise dominates.
