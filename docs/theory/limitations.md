# Limitations

## Limitations

**Ambiguity with few receivers.** RSSI from one receiver constrains distance, not
direction: the posterior is an annulus. Two receivers on the same wall leave a mirror
ambiguity. With few receivers, the honest output is a spread over rooms; evidence
forgetting keeps it that way instead of collapsing onto one room. More receivers in
different rooms, or survey points near the ambiguous spots, resolve it.

**iPhone advertising.** iPhones use resolvable private addresses (resolved with the IRK)
and advertise often while awake but rarely when locked: about once per 50 s in the
simulator, sometimes not for minutes in reality. Silence handling keeps the last room
rather than guessing.

**Home Assistant's Apple pre-filter.** Home Assistant drops Apple manufacturer frames
other than iBeacon, HomeKit, "nearby" (0x10) and Find My before any integration sees
them. iPhones send nearby frames, so they get through, but some frames are lost. Bermuda
has the same limit.

**Single floor.** Plans are 2-D with a receiver height; floors are not modelled.

**Body shadowing.** The model treats body loss as noise. Calibration fits a separate
offset per phone position (hand, pocket, lying down), and at runtime it is part of the
device offset.

The literature behind the model is listed under {doc}`../references`.
