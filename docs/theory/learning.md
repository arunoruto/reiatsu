# Learning from data

```{admonition} Planned
:class: warning
Nothing on this page has shipped yet. It is the v0.5 plan; the page will be rewritten as
the items land.
```

## Planned for v0.5

**Per-receiver noise.** Replace the global $\sigma$ in {eq}`eq-likelihood` with $\sigma_r$,
the robust scale of each receiver's survey residuals with a prior around 6 dB. A dongle
in a TV console and an ESP32 on a shelf differ.

**Per-receiver detection curve.** Replace the fixed sensitivity $s$ and $p_\text{drop}$
with

$$
p(\text{heard} \mid \mu) = p_\text{max}\; \frac{1}{1 + e^{-(\mu - s_r)/\beta_r}},
$$

fitted from heard/sent counts at survey points (binomial likelihood, priors on $s_r$ and
$\beta_r$). This directly shapes the "not heard" evidence that dominated the first real
single-receiver results.

**Parameter uncertainty.** A Laplace approximation from the least-squares Jacobian,
$\operatorname{Cov}(\hat\theta) \approx (J^\top J)^{-1}$ (in units of the scaled
residuals), shown as ± next to each value in the Fit table.

**Replay metrics.** `reiatsu --plan plan.yaml --replay record.jsonl` with a metrics
summary: reference-device room accuracy and position error, optionally with another
`calibration.json`. This compares configurations on real recordings.

## Deliberately not planned (yet)

With one flat, two or three receivers and a few dozen labels, these parameters are not
identifiable. They would echo their priors and add fit time and UI noise:

- per-wall losses pooled by material (hierarchical): revisit with node-to-node data;
- a per-receiver path-loss exponent $n_r$: confounded with $P_{0,r}$ at indoor distances;
- runtime carry-state marginalisation: body shadow is a constant offset, already covered by the tracker's transmit-offset marginalisation ({eq}`eq-update`); calibration already fits per-position offsets;
- per-sensor mmWave rates, motion and transition matrices, time-of-day priors: no labels to learn them from yet;
- weak labels from Home Assistant context (charging at night → bedroom, …) and nightly re-fits with automatic rollback: worth it once there is a steady stream of labels.

## Guardrails

- Learn only from labels (survey points, reference devices, explicit weak labels), never from the tracker's own estimates.
- Every fit is judged on held-out data (leave-one-out) before it is applied, and applying stays a manual step; `calibration.json.bak` keeps the previous one.
