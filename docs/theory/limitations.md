# Limitations and references

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

## References

- A. J. Motley and J. M. Keenan, "Personal communication radio coverage in buildings at 900 MHz and 1700 MHz," *Electronics Letters* 24(12):763–764, 1988 (multi-wall model).
- E. Damosso (ed.), *Digital Mobile Radio Towards Future Generation Systems: COST 231 Final Report*, European Commission, 1999 (COST 231 multi-wall model).
- P. Bahl and V. N. Padmanabhan, "RADAR: an in-building RF-based user location and tracking system," *IEEE INFOCOM*, 2000.
- D. Fox, J. Hightower, L. Liao, D. Schulz and G. Borriello, "Bayesian filtering for location estimation," *IEEE Pervasive Computing* 2(3):24–33, 2003.
- S. Thrun, W. Burgard and D. Fox, *Probabilistic Robotics*, MIT Press, 2005 (grid-based Bayes filters).
- L. R. Rabiner, "A tutorial on hidden Markov models and selected applications in speech recognition," *Proceedings of the IEEE* 77(2):257–286, 1989.
- K. L. Lange, R. J. A. Little and J. M. G. Taylor, "Robust statistical modeling using the t distribution," *JASA* 84(408):881–896, 1989.
- J. Tobin, "Estimation of relationships for limited dependent variables," *Econometrica* 26(1):24–36, 1958 (censored observations).
- P. J. Huber, "Robust estimation of a location parameter," *Annals of Mathematical Statistics* 35(1):73–101, 1964.
- C. E. Rasmussen and C. K. I. Williams, *Gaussian Processes for Machine Learning*, MIT Press, 2006.
