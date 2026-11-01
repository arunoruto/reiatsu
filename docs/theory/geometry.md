# Floor plan geometry

Code: {py:mod}`reiatsu.plan`.

## Rooms and walls

A room is a polygon $R \subset \mathbb{R}^2$ (or the rectangle shorthand
`[x0, y0, x1, y1]`). Walls aren't drawn separately: `derive_walls` derives them from the
room edges. For each edge $e = (\mathbf{a}, \mathbf{b})$ of room $A$, with unit direction
$\mathbf{u}$ and normal $\mathbf{n}$, every edge $f = (\mathbf{a}', \mathbf{b}')$ of
another room $B$ is compared:

- $f$ is parallel to $e$: $\lvert \mathbf{u} \times \mathbf{u}' \rvert \le 0.05$;
- the gap between them is at most `max_gap` (default 0.4 m): $\lvert (\mathbf{a}' - \mathbf{a}) \cdot \mathbf{n} \rvert \le \text{max\_gap}$;
- their projections onto $e$ overlap on $[t_0, t_1] \subseteq [0, \lVert \mathbf{b} - \mathbf{a} \rVert]$.

Each overlap becomes one **interior wall** between $A$ and $B$, placed on the midline
$\mathbf{a} + \tfrac{1}{2}\big((\mathbf{a}' - \mathbf{a}) \cdot \mathbf{n}\big)\mathbf{n}$.
The parts of the edge no other room covers become **exterior walls** of $A$. The
material comes from `materials.interior` / `materials.exterior`, or from a `walls`
override: two rooms name their shared wall, one room names its exterior walls.

## Doors

A door `{rooms, at, width}` cuts a gap of `width` into the wall between its rooms,
centred at the projection of `at` onto the nearest shared wall segment (`cut_door`). With
one room it is a door to the outside: a gap in that room's exterior wall, with no effect
on motion.

## Walkable grid

`make_grid` places cells $c$ on a regular grid (resolution 0.2 m) and keeps those inside a
room, recording each cell's room $\rho(c)$. The walkable graph $G$ links

- cells of the **same room** within $1.5 \times$ the resolution (8-neighbourhood), with
  weight $\lVert \mathbf{x}_c - \mathbf{x}_{c'} \rVert$;
- across each interior **door**: every cell of one room to every cell of the other that
  lies within $\text{width}/2 + \text{res}$ of the door centre.

Walls are never crossed otherwise, so the geodesic distance $d_G(c, c')$, the shortest
path in $G$, is the walking distance through doors. Zones (`zones`, for mmWave zone
sensors) are extra cell masks; they don't affect the graph.

## Coordinates

x points right and **y up** (`y_axis: up`, as in ESPresense and Leaflet's `CRS.Simple`).
The engine is invariant to mirroring, so `y_axis` only tells the web UI how to draw. A
background image's `x, y` is its lower-left corner, and `scale` is metres per pixel.
