# Sensor fusion

Code: `Tracker.set_occupancy`, `Tracker.set_listening`.

## mmWave presence

An mmWave sensor (or an FP2-style zone) reports whether **someone** is in its area $a$
(a room or a `zones` polygon). The device's person may be the one it sees, or another
resident, or it may be a false alarm. With miss rate $q$ (`mmwave_fnr` = 0.15) and
$\pi$ = P(area reported occupied ∣ device elsewhere) (`mmwave_p_other` = 0.5), the
factor for cell $c$ is

$$
f_\text{occ}(c) = \Bigg[\prod_{a:\ \text{occupied}} \big((1-q)\,\mathbb{1}_{c \in a} + \pi\, \mathbb{1}_{c \notin a}\big)
\prod_{a:\ \text{empty}} \big(q\, \mathbb{1}_{c \in a} + (1-\pi)\, \mathbb{1}_{c \notin a}\big)\Bigg]^{1/\tau}
$$ (eq-occupancy)

It is multiplied into the belief every second ({eq}`eq-predict`). A persisting state
isn't a new observation every second, so the exponent $1/\tau$ ($\tau$ = `mmwave_tau`
= 5 s) counts it as one observation per $\tau$ seconds.

$\pi = 0.5$ is deliberately mild. "Occupied" pulls the belief in by a factor of
$(1-q)/\pi = 1.7$ per $\tau$, because another resident may be the one the sensor sees. "Empty"
pushes it out by a factor of $(1-\pi)/q = 3.3$. In the simulator, letting only "empty"
count lowered false room switches but cost about 5 points of accuracy, and the original
$\pi = 0.3$ switched rooms more often.

**Only carried devices.** mmWave says where *people* are. A device nobody carries (any
device placed as a reference in the plan, such as a door lock) ignores occupancy
entirely (`Tracker.carried = False`).

## Online receivers

The bridge publishes the scanners that are currently registered in Home Assistant and
heard anything in the last 60 s (`reiatsu/receivers`). Only those receivers' silence
enters {eq}`eq-likelihood` as "not heard" (`Tracker.set_listening`). Until the first such
message arrives, every receiver in the plan counts as online.
