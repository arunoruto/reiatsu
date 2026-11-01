# API reference

The Python library, for scripting, notebooks and tests. The daemon and the web UI are
built on it.

## Floor plan

```{eval-rst}
.. automodule:: reiatsu.plan
   :members: Plan, Wall, Door, Receiver, Grid, load_plan, make_grid, derive_walls, cut_door, room_at, crosses, inside
```

## Channel model

```{eval-rst}
.. automodule:: reiatsu.channel
   :members: ChannelParams, Likelihood, predict, geometry, wall_counts, rf_position
```

## Tracking

```{eval-rst}
.. automodule:: reiatsu.tracker
   :members: Tracker, Estimate, motion_kernel

.. automodule:: reiatsu.locator
   :members: Locator, Advert, Occupancy, Receivers
```

## Calibration and survey

```{eval-rst}
.. automodule:: reiatsu.calibrate
   :members: SurveyPoint, NodeLink, WeakLabel, Calibration, ResidualGP, fit, residual_gp, censored_median, summarize, offset_groups, starter_targets, starter_points, next_point

.. automodule:: reiatsu.survey
   :members: Survey, load_calibration, save_calibration, evaluate, leave_one_out, residuals, next_target, locate
```

## Daemon and web UI

```{eval-rst}
.. automodule:: reiatsu.daemon
   :members: main, track, tracker_factory, build_locator, parse, discovery, Publisher

.. automodule:: reiatsu.web
   :members: App, derive, from_espresense, serve
```
