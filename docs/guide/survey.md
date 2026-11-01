# Survey walkthrough

The survey is the calibration input. Do it with the phone you want to track, and open
the UI on that phone.

1. **Save the plan** with the receivers placed. The survey uses the saved plan.
2. Open **Survey**. Pick the **device** you're holding (it must be a tracked device, so the
   bridge needs its IRK) and the **phone** position: *In hand*, *In pocket* or *Lying down*.
   Each position gets its own fitted offset, so mixing them is fine.
3. **Follow targets**: the panel says where to go ("Go to Kitchen: next to the wall to the
   Hallway") and a pulsing ring marks the spot. Stand there. If you're not exactly on it,
   drag the blue marker or tap the map where you are. Or switch to **Measure anywhere** and
   tap your position.
4. **Start**. For 30 s (45 s lying down), turn slowly once, holding the phone as you
   usually do; lying down, keep the screen on. The panel shows adverts and the median per
   receiver. If a receiver gets fewer than 10 adverts, the phone was probably locked.
5. Repeat. Points are coloured by how well the model fits them (green < 4 dB, yellow < 8,
   red worse); tap one to delete it. *Progress* shows the leave-one-out room accuracy, and
   a "good enough" hint once it stops improving.
6. **Fit…** compares the current calibration with the new fit and lists the parameters.
   **Apply** writes `calibration.json` and restarts tracking. Five to eight points already
   give a usable fit; the starter set (about 20–30 points) covers the flat.

Tips: corridors benefit from points near both walls. Survey in the way the phone is
normally carried. Points in doorways help the most at room boundaries.
