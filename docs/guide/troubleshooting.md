# Troubleshooting

**"p0 … is outside -100..-20 (it is negative, e.g. -59)"**
: P0 is a negative dBm value. Fix the receiver's `p0` in `plan.yaml` or in *Devices*.

**"receiver 'x' is not in the floor plan; its adverts are ignored"** (daemon log)
: The bridge publishes a receiver id the plan doesn't have. Place it in *Devices*, or
  rename it under the bridge's *Name receivers*.

**A device sits in one room with 100% while it's elsewhere**
: Check P0 and heights first. Then check for a receiver that is online but never hears the
  device: its silence counts as evidence. Survey a few points and *Fit*; a receiver that
  hears badly gets a low P0.

**Spread over several rooms**
: Expected with one or two receivers, or several on the same wall (see
  {doc}`../theory/limitations`). Add a receiver in another room, or survey points near the
  ambiguous spots.

**The plan is mirrored**
: Plans are y-up. A plan saved by Reiatsu ≤ 0.3.1 is y-down: add `y_axis: down` (the UI
  converts it on the next save), or use *Rooms → Orientation*.

**The UI doesn't load through ingress**
: Check the add-on log. With `log_level: debug` it shows every HTTP request.

**The iPhone is rarely heard**
: A locked iPhone advertises rarely. Home Assistant also drops some Apple frames before
  any integration sees them. The tracker holds the last room through silence.
