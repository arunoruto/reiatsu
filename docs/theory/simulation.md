# Simulator and evaluation

Code: {py:mod}`sim.world`, {py:mod}`sim.run`. Run `python -m sim.run` (5 seeds) or
`python -m sim.run --check`, which fails CI if mean room accuracy drops more than 3 points
below `sim/expected.json`. The benchmark plan is `sim/flat.yaml`, frozen so the numbers
stay comparable.

## Hidden truth

The truth (`make_truth`) differs from the priors the way a real home would:

- $P_{0,r}$ = prior $+\ \mathcal{U}(-5, 5)$, $n \sim \mathcal{U}(1.8, 2.6)$, $L_m$ = prior mean $\times\ \mathcal{U}(0.6, 1.5)$, a node-link offset $\mathcal{U}(-6, 6)$ dB;
- a spatially correlated **shadowing field** per receiver: 40 Gaussian bumps of length 1 m, scaled to sd 3 dB. The channel model doesn't know about it; the GP should learn it;
- **body shadowing** $12\,(1 - \cos\varphi)/2$ dB, where $\varphi$ is the angle between the way the person faces and the receiver;
- **fading** $\mathcal{N}(0, 5^2)$ in dB, i.e. log-normal {cite:p}`rappaport2002`, rounded to whole dBm; heard if $\ge -100$ dBm and not dropped ($p_\text{drop} = 0.1$).

A person walks (`walk`) from room to room through the doors at 0.6–1.2 m/s, staying
$\max(20, \text{Exp}(180))$ s in each. The phone alternates between **awake** (mean 90 s,
advertising at 0.5–2 Hz) and **locked** (mean 300 s, 0.02 Hz). mmWave (`mmwave`) has 1.5 s
lag, sometimes loses a person who sits still, and raises 5–30 s false alarms at 1 per
hour per sensor. In the default scenario a second, untracked resident also triggers
the sensors, and only the bathroom, hallway, kitchen and living room have sensors, as in
the real flat.

## Metrics

Each configuration is scored once per second after a 30 s warm-up:

`room acc`
: fraction of seconds in the true room; also split into **awake** and **locked**.

`time to correct room`
: median over stays of at least 30 s of the seconds until the first correct second.

`false flips/h`
: changes of the reported room *into a wrong room*, per hour.

`pos err`
: median distance between the estimate and the truth.

`RSSI map err`
: mean $\lvert \mu_r(c) - \mu^\text{true}_r(c) \rvert$ over receivers and cells, against the truth including half the body loss (what a 360° survey median sees).

The **baseline** is Bermuda-like (`NearestReceiver`). It converts RSSI to distance with
a fixed reference power and $n = 2$, lets readings bring a receiver closer at most 3 m/s
faster, smooths increases, and switches area only when another receiver is clearly
closer (factor 0.8) for 3 s, or the current one goes stale (30 s).

## Results

`--scenario default`, seeds 1–5, mean ± sd:

| config | room acc | acc awake | acc locked | time to correct room (s) | false flips/h | pos err (m) | RSSI map err (dB) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline: nearest receiver (Bermuda-like) | 56.8% ± 7.9 | 70.4% ± 5.7 | 51.7% ± 9.0 | 34 ± 6 | 19.8 ± 4.6 | 2.65 ± 0.44 | – |
| priors only | 61.4% ± 8.9 | 70.6% ± 12.3 | 57.4% ± 8.4 | 34 ± 12 | 14.4 ± 4.2 | 1.42 ± 0.19 | 7.2 ± 1.0 |
| node-to-node only | 65.1% ± 2.4 | 78.5% ± 4.6 | 59.8% ± 4.3 | 30 ± 9 | 13.5 ± 2.7 | 1.26 ± 0.08 | 7.2 ± 1.5 |
| survey: starter | 67.8% ± 6.5 | 80.2% ± 8.0 | 62.6% ± 8.5 | 25 ± 12 | 10.8 ± 3.3 | 1.20 ± 0.13 | 1.8 ± 0.3 |
| survey: +10 active | 67.4% ± 3.8 | 79.5% ± 8.7 | 62.5% ± 4.3 | 26 ± 10 | 12.2 ± 3.1 | 1.20 ± 0.13 | 1.6 ± 0.3 |
| survey: +10 random | 68.2% ± 5.2 | 80.6% ± 7.5 | 63.2% ± 7.3 | 25 ± 9 | 10.9 ± 2.6 | 1.20 ± 0.11 | 1.6 ± 0.3 |
| survey: +10 active, no walls | 66.7% ± 5.7 | 79.9% ± 3.5 | 61.5% ± 8.2 | 31 ± 11 | 10.6 ± 4.3 | 1.31 ± 0.12 | 4.4 ± 1.0 |
| survey: +10 active + mmWave | 77.8% ± 3.2 | 85.1% ± 8.2 | 74.8% ± 1.9 | 17 ± 6 | 13.6 ± 1.9 | 1.09 ± 0.10 | 1.6 ± 0.3 |

With mmWave in every room and nobody else home (`--scenario ideal-mmwave`) the last row
reaches about 91%. Most of the remaining error happens while the phone is locked: with
one advert every ~50 s, a room change only shows up when the next advert arrives.
