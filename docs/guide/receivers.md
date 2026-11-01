# Floor plan and receivers

Open the web UI (sidebar *Reiatsu* in the add-on, or `http://<host>:8099/`).

## Draw the plan

1. **Background** (optional): upload a PNG/JPEG floor plan, then *Draw a line of known
   length* across something you know (a wall, a door) and type its real length. Or import
   an ESPresense companion `config.yaml`.
2. **Rooms**: *Polygon* or *Rectangle*, tap the corners. Corners snap to other rooms;
   nearly square corners and nearly aligned edges get straightened. Rooms closer than the
   wall gap (`max_gap`, 0.4 m) share a wall. If the plan comes out mirrored or rotated, use
   *Orientation*.
3. **Walls & doors**: tap a wall to set its material (drywall, brick, concrete, glass).
   *Add door* → tap the wall; drag the green square along it; set its width. Doors are the
   only way between rooms for the tracker, so add every one you walk through.
4. **Save**. Tracking restarts on the new plan.

## Place the receivers

In **Devices**, drag each receiver's ⠿ handle onto the map where the proxy really is
(or select it and tap the map). Then tap its marker:

- **Height z**: metres above the floor (default 1.0).
- **P0**: the signal at 1 m, *negative* dBm (e.g. −59). Leave it empty for the prior or
  the calibrated value. Lower it for a receiver that hears badly, such as an adapter in a
  cabinet. Typing `70` stores −70.

Receivers marked *offline* (from the bridge) don't count as "not heard".

## Reference devices

A tracked device that never moves (a door lock, a hub) is useful ground truth. Drag it
from *Tracked devices* onto its real spot. It then:

- shows its live error on the Live tab (dashed line to its spot);
- feeds its last 10 minutes of adverts into calibration;
- is tracked as *not carried*: no motion, and mmWave doesn't affect it.
