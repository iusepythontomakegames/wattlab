#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WATT//LAB  --  a 2D pixel-style electronics sandbox for Python 3.13
===================================================================

Parts in the box
----------------
  1. WIRES          two-way, near-instant conductors. Every tile adds real
                    resistance, so energy degrades into heat over distance.
  2. POWER SOURCE   fixed-voltage supply with a + pin and a - pin.
  3. CAPACITOR      stores charge. Its voltage rating is HIDDEN until you find
                    it out the hard way. Exceed it and it explodes.
  4. VOLTMETER      high-impedance probe. Hover the cursor over it to read volts.
  5. ONE-WAY BRIDGE holds LMB + LSHIFT and drag to set one of 4 directions.
  6. LED            15 light levels, Vf / max brightness, blows on abuse.
  7. RESISTOR       bottlenecks current/voltage to whatever you dial in.
  8. HEATSINK       big thermal mass, pulls heat out of hardware.
  9. FAN            bulky and power hungry (the biggest load in the box).
                    It is a one terminal load: it must touch a POWERED node,
                    then it dumps ~80 mA to the 0 V rail and blows air over
                    nearby heatsinks.
 10. WIRE MATERIALS copper/gold/silver/... plus an editable CUSTOM alloy.

Two terminal parts (source, resistor, capacitor, LED, bridge) need wire on BOTH
sides, facing along their arrow.  The panel warns "! needs wire on both ends"
when a part is floating.  Hover any tile for a live V / A / C readout tooltip.

Bonus
   * Real physics: nodal analysis (SOR solver) with piecewise-linear diodes,
     backward-Euler capacitors, I^2R heating, thermal conduction, convection,
     radiation to ambient, dielectric breakdown and joule-rated resistors.
   * Unlimited layers (vias link them) so you can shrink one layer's footprint.
   * Heat melts, cracks and expands hardware.
   * Water conducts: puddles short rails and destroy hardware.
   * 2D pixel art, flat palette, chunky glyphs.

Run
---
    pip install pygame-ce            # or: pip install pygame
    python3 wattlab.py
    python3 wattlab.py --selftest    # 21 headless physics checks, no pygame needed

Controls
--------
    1..9,0,K   pick tool              LMB drag   place
    RMB        erase                  LMB+SHIFT  drag to aim a 4-way direction
    R          rotate tool            M          cycle wire material
    C          custom alloy editor    SPACE      pause / resume simulation
    [ ]        previous / next layer  N          add a layer
    - / =      adjust hovered src / resistor / capacitor value
    F5 / F9    save / load build.scfb F1         overlay help
    X          clear current layer

Simulation timing: 30 Hz physics, electrical solve at 15 Hz with warm start.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

APP_TITLE = "WATT//LAB"
SAVE_NAME = "wattlab_build.scfb"

CELL = 16
GRID_W = 54
GRID_H = 40
BOARD_X = 12
BOARD_Y = 60
WIN_W = 1252
WIN_H = 764
PANEL_X = BOARD_X + GRID_W * CELL + 12
PANEL_W = WIN_W - PANEL_X - 12
BAR_Y = BOARD_Y + GRID_H * CELL + 6

AMBIENT = 25.0          # deg C
WATER_R = 25.0          # ohm per water tile (dirty water)
GND_LEAK = 1e-6         # 1 MOhm voltmeter input
FAN_R = 60.0            # fan motor resistance

SIM_HZ = 30.0
SOLVE_EVERY = 2         # solve electrical every N sim steps -> 15 Hz

SOR_OMEGA = 1.7         # successive over-relaxation factor
SOR_MAX_ITER = 900
SOR_TOL = 1e-6
SOR_MIN_ITER = 12       # stiff networks creep, so always do a few sweeps

RNG = random.Random()

DIRV = ((0, -1), (1, 0), (0, 1), (-1, 0))      # up, right, down, left
DIRNAME = ("up", "right", "down", "left")


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------

# res  : ohm per tile (series resistance of one tile of wire)
# amp  : safe current in amp before it starts cooking
# k    : thermal conductivity (W/m/K, used for thermal coupling)
# melt : deg C where the conductor itself fails (insulation first)
MATERIALS = {
    "copper":   dict(name="Copper",    color=(198, 112, 66),  res=0.010, amp=2.0, k=400, melt=160),
    "silver":   dict(name="Silver",    color=(216, 218, 228), res=0.008, amp=2.2, k=429, melt=160),
    "gold":     dict(name="Gold",      color=(230, 192, 92),  res=0.012, amp=1.8, k=317, melt=170),
    "aluminum": dict(name="Aluminium", color=(178, 188, 198), res=0.017, amp=1.4, k=237, melt=180),
    "brass":    dict(name="Brass",     color=(198, 168, 96),  res=0.040, amp=1.0, k=120, melt=200),
    "steel":    dict(name="Steel",     color=(142, 148, 158), res=0.110, amp=0.5, k=50,  melt=260),
    "tungsten": dict(name="Tungsten",  color=(126, 126, 138), res=0.060, amp=0.9, k=173, melt=600),
    "nichrome": dict(name="Nichrome",  color=(164, 134, 122), res=1.200, amp=3.0, k=11,  melt=500),
    "carbon":   dict(name="Carbon",    color=(74, 74, 82),    res=0.900, amp=0.30, k=15, melt=320),
    "solder":   dict(name="Solder",    color=(172, 180, 188), res=0.110, amp=0.6, k=60,  melt=150),
    "custom":   dict(name="Custom",    color=(232, 118, 202), res=0.050, amp=2.5, k=200, melt=170),
}

MAT_ORDER = tuple(MATERIALS.keys())

# kinds that behave as electrical nodes
NODE_KINDS = {"wire", "water", "volt", "fan", "via", "slag"}

TOOLS = [
    ("wire",   "Wire",             "1", "Wire"),
    ("src",    "Power Source",     "2", "Source"),
    ("cap",    "Capacitor",        "3", "Cap"),
    ("volt",   "Voltmeter",        "4", "Volt"),
    ("bridge", "One-Way Bridge",   "5", "Bridge"),
    ("led",    "LED",              "6", "LED"),
    ("res",    "Resistor",         "7", "Res"),
    ("sink",   "Heatsink",         "8", "Sink"),
    ("fan",    "Fan",              "9", "Fan"),
    ("via",    "Via (layer link)", "0", "Via"),
    ("water",  "Water (conducts)", "K", "Water"),
]
TOOL_KINDS = [t[0] for t in TOOLS]

KIND_COLOR = {
    "src":    (226, 88, 88),
    "cap":    (88, 148, 222),
    "volt":   (238, 214, 116),
    "bridge": (146, 220, 138),
    "led":    (60, 74, 60),
    "res":    (204, 172, 132),
    "sink":   (150, 160, 178),
    "fan":    (116, 202, 222),
    "via":    (200, 160, 244),
    "water":  (56, 108, 190),
    "slag":   (58, 52, 52),
    "wire":   (198, 112, 66),
    "empty":  (26, 28, 34),
}

# thermal conductivity per tile pair (W/K), heat capacity (J/K), convection (W/K)
THERM_K = {
    "wire": 0.40, "src": 0.10, "cap": 0.02, "volt": 0.02, "bridge": 0.03,
    "led": 0.03, "res": 0.05, "sink": 1.30, "fan": 0.02, "via": 0.30,
    "water": 0.15, "slag": 0.02,
}
HEATCAP = {
    "wire": 0.030, "src": 2.0, "cap": 0.50, "volt": 0.030, "bridge": 0.030,
    "led": 0.030, "res": 0.35, "sink": 3.0, "fan": 0.80, "via": 0.030,
    "water": 1.5, "slag": 0.30,
}
CONV = {
    "wire": 0.010, "src": 0.050, "cap": 0.020, "volt": 0.010, "bridge": 0.010,
    "led": 0.020, "res": 0.040, "sink": 0.600, "fan": 0.100, "via": 0.010,
    "water": 0.050, "slag": 0.050,
}
MELT = {
    "src": 175.0, "cap": 108.0, "volt": 150.0, "bridge": 175.0, "led": 150.0,
    "res": 240.0, "sink": 640.0, "fan": 128.0, "via": 180.0, "water": 100.0,
    "slag": 900.0, "wire": 165.0,
}


def mat_melt(cell):
    if cell.kind == "wire":
        return MATERIALS[cell.mat]["melt"]
    return MELT.get(cell.kind, 200.0)


# --------------------------------------------------------------------------
# cells
# --------------------------------------------------------------------------

class Cell:
    __slots__ = ("kind", "mat", "d", "temp", "v", "pw", "extra")

    def __init__(self, kind, mat="copper", d=1):
        self.kind = kind
        self.mat = mat
        self.d = d
        self.temp = AMBIENT
        self.v = 0.0            # node voltage, or voltage across a 2-terminal part
        self.pw = 0.0           # electrical power dumped into this cell (W)
        self.extra = {}
        self._init_extra()

    def _init_extra(self):
        e = self.extra
        k = self.kind
        if k == "src":
            e["v"] = 5.0
            e["i"] = 0.0
        elif k == "cap":
            e["c"] = 470e-6
            e["esr"] = 0.05
            # hidden dielectric breakdown: never revealed until it lets go
            e["bv"] = RNG.choice((10.0, 16.0, 25.0, 35.0, 50.0)) * RNG.uniform(0.97, 1.07)
            e["found"] = False
            e["vc"] = 0.0
            e["i"] = 0.0
            e["stress"] = 0.0
        elif k == "res":
            e["r"] = 220.0
            e["rating"] = 0.25      # watt rating
            e["i"] = 0.0
        elif k == "led":
            e["bright"] = 0         # 0..15
            e["i"] = 0.0
            e["blown"] = False
            e["vf"] = 2.0
            e["rd"] = 12.0
            e["imax"] = 0.030       # current for full brightness
            e["rated"] = 5.0        # absolute max forward voltage
        elif k == "bridge":
            e["vf"] = 0.55
            e["rd"] = 0.08
            e["i"] = 0.0
            e["ok"] = True
        elif k == "fan":
            e["i"] = 0.0
        elif k == "water":
            e["dmg"] = 0.0
        elif k == "wire":
            e["crack"] = 1.0
            e["stress"] = 0.0
            e["i"] = 0.0
        elif k in ("volt", "via"):
            e["i"] = 0.0
        elif k == "slag":
            e["i"] = 0.0


def series_r(c: Cell) -> float:
    """Series resistance of one tile of this cell."""
    k = c.kind
    if k == "wire":
        return MATERIALS[c.mat]["res"] * c.extra.get("crack", 1.0)
    if k == "water":
        return WATER_R * (1.0 + 4.0 * c.extra.get("dmg", 0.0))
    if k == "volt":
        return 0.02
    if k == "fan":
        return 0.02
    if k == "via":
        return 0.02
    if k == "slag":
        return 4.0
    return 0.05


def kpair(a: Cell, b: Cell) -> float:
    ka = THERM_K.get(a.kind, 0.02)
    kb = THERM_K.get(b.kind, 0.02)
    if a.kind == "wire":
        ka = MATERIALS[a.mat]["k"] * 0.001
    if b.kind == "wire":
        kb = MATERIALS[b.mat]["k"] * 0.001
    return 1.0 / (0.5 / max(ka, 1e-4) + 0.5 / max(kb, 1e-4))


# --------------------------------------------------------------------------
# edge + layer + board
# --------------------------------------------------------------------------

class Edge:
    __slots__ = ("a", "b", "g", "kind", "owner", "ra", "rb", "va", "vb", "on")

    def __init__(self, a, b, g, kind="pair", owner=None, ra=0.0, rb=0.0):
        self.on = False
        self.a = a
        self.b = b
        self.g = g
        self.kind = kind
        self.owner = owner
        self.ra = ra            # heat share for node a (ohm)
        self.rb = rb            # heat share for node b (ohm)
        self.va = 0.0
        self.vb = 0.0


class Layer:
    def __init__(self, idx, w, h):
        self.idx = idx
        self.w = w
        self.h = h
        self.cells = {}


class Board:
    """Whole build: layers, solver state, thermal state, event log."""

    def __init__(self, w=GRID_W, h=GRID_H):
        self.layers = [Layer(0, w, h)]
        self.cur = 0
        self.dirty = True
        self.events = []
        self.t = 0.0
        # solver state
        self.nodes = []
        self.nid = {}
        self.edges = []
        self.adj = []
        self.V = []
        self.gnd = 0
        self.fixed = {}
        self.inj = []
        self.iters_used = 0
        self.passes = 0
        self.solve_ms = 0.0
        self.solved_once = False

    # ---- access ------------------------------------------------------
    @property
    def layer(self) -> Layer:
        return self.layers[self.cur]

    def cell(self, x, y, li=None):
        return self.layers[self.cur if li is None else li].cells.get((x, y))

    def set_cell(self, x, y, cell, li=None):
        L = self.layers[self.cur if li is None else li]
        if 0 <= x < L.w and 0 <= y < L.h:
            L.cells[(x, y)] = cell
            self.dirty = True

    def del_cell(self, x, y, li=None):
        L = self.layers[self.cur if li is None else li]
        if L.cells.pop((x, y), None) is not None:
            self.dirty = True

    def all_cells(self):
        for L in self.layers:
            for pos, c in L.cells.items():
                yield L.idx, pos, c

    def log(self, msg):
        self.events.append((self.t, msg))
        if len(self.events) > 60:
            del self.events[:-60]

    def add_layer(self):
        idx = len(self.layers)
        self.layers.append(Layer(idx, self.layers[0].w, self.layers[0].h))
        self.cur = idx
        self.dirty = True
        self.log(f"layer {idx} created (vias link layers)")

    # ---- electrical graph --------------------------------------------
    def _build(self):
        self.nodes = []
        self.nid = {}
        for L in self.layers:
            for pos, c in L.cells.items():
                if c.kind in NODE_KINDS:
                    self.nid[(L.idx, pos[0], pos[1])] = len(self.nodes)
                    self.nodes.append((L.idx, pos[0], pos[1], c))
        self.gnd = len(self.nodes)
        n = len(self.nodes) + 1
        self.edges = []
        self.adj = [[] for _ in range(n)]
        self.fixed = {}
        self.inj = [0.0] * n

        # wire / water / via / volt / fan tiles couple to their 4 neighbours
        for i, (li, x, y, c) in enumerate(self.nodes):
            ra = series_r(c)
            for dx, dy in DIRV:
                j = self.nid.get((li, x + dx, y + dy))
                if j is None or j <= i:
                    continue
                rb = series_r(self.nodes[j][3])
                g = 1.0 / (0.5 * ra + 0.5 * rb)
                self._add_edge(Edge(i, j, g, "pair", None, 0.5 * ra, 0.5 * rb))

        # vias: same coordinate on the layer above
        seen = set()
        for i, (li, x, y, c) in enumerate(self.nodes):
            j = self.nid.get((li + 1, x, y))
            if j is None:
                continue
            other = self.nodes[j][3]
            if c.kind != "via" and other.kind != "via":
                continue
            key = (min(i, j), max(i, j))
            if key in seen:
                continue
            seen.add(key)
            self._add_edge(Edge(i, j, 1.0 / 0.04, "pair", None, 0.02, 0.02))

        # sources: ideal voltage between the two pins beside them
        for li, (x, y), c in self.all_cells():
            if c.kind != "src":
                continue
            dx, dy = DIRV[c.d % 4]
            plus = self.nid.get((li, x + dx, y + dy))
            minus = self.nid.get((li, x - dx, y - dy))
            if plus is None or minus is None:
                c.extra["open"] = True
                continue
            c.extra["open"] = False
            c.extra["na"] = plus
            c.extra["nb"] = minus
            self.fixed[plus] = c.extra["v"]
            self.fixed[minus] = 0.0

        # two terminal devices occupy one tile and bridge the two nodes
        # in front of and behind them.
        for li, (x, y), c in self.all_cells():
            if c.kind not in ("res", "cap", "led", "bridge"):
                continue
            dx, dy = DIRV[c.d % 4]
            a = self.nid.get((li, x - dx, y - dy))
            b = self.nid.get((li, x + dx, y + dy))
            if a is None or b is None:
                c.extra["open"] = True
                c.extra["na"] = None
                continue
            c.extra["open"] = False
            c.extra["na"] = a
            c.extra["nb"] = b
            if c.kind == "res":
                r = max(1e-4, c.extra["r"])
                e = Edge(a, b, 1.0 / r, "res", c)
            elif c.kind == "cap":
                e = Edge(a, b, 1.0 / max(1e-4, c.extra["esr"]), "cap", c)
            elif c.kind == "led":
                e = Edge(a, b, 1e-9, "led", c)
                if c.extra["blown"]:
                    e.g = 1e-12
            else:
                e = Edge(a, b, 1e-9, "bridge", c)
            self._add_edge(e)

        # shunt loads to the 0 V reference
        for li, (x, y), c in self.all_cells():
            if c.kind == "volt":
                i = self.nid.get((li, x, y))
                if i is not None:
                    self._add_edge(Edge(i, self.gnd, GND_LEAK, "volt", c))
            elif c.kind == "fan":
                i = self.nid.get((li, x, y))
                if i is not None:
                    self._add_edge(Edge(i, self.gnd, 1.0 / FAN_R, "fan", c))

        self.V = [0.0] * n
        if self.fixed:
            avg = sum(self.fixed.values()) / len(self.fixed)
        else:
            avg = 0.0
        for i in range(len(self.nodes)):
            self.V[i] = avg
        self.V[self.gnd] = 0.0

    def _add_edge(self, e: Edge):
        idx = len(self.edges)
        self.edges.append(e)
        self.adj[e.a].append((e.b, idx))
        self.adj[e.b].append((e.a, idx))

    # ---- solve -------------------------------------------------------
    def solve(self, dt):
        import time as _t
        t0 = _t.perf_counter()
        if self.dirty or not self.nodes:
            self._build()
            self.dirty = False
        n = len(self.nodes)
        if len(self.V) != n + 1:
            self.V = [0.0] * (n + 1)
        V = self.V
        adj = self.adj
        edges = self.edges

        # Piecewise linear one way devices (LED, bridge) are solved as a
        # conductance in series with a threshold source:
        #     I = 1/Rd * (V - Vf)     while conducting
        # The on/off state is resolved by an outer loop, so the result is
        # self consistent instead of oscillating between states.
        G_OFF = 1e-9
        diodes = [e for e in edges if e.kind in ("led", "bridge")]
        for e in diodes:
            if e.kind == "led" and e.owner.extra["blown"]:
                e.on = False
        n1 = n + 1
        inj = self.inj
        fixed = self.fixed
        gnd = self.gnd
        tol = SOR_TOL
        omega = SOR_OMEGA
        total_iters = 0
        self.passes = 0
        for outer in range(6):
            self.passes = outer + 1
            for i in range(n1):
                inj[i] = 0.0
            for e in edges:
                if e.kind == "cap":
                    c = e.owner
                    g = 1.0 / (max(1e-4, c.extra["esr"])
                               + max(dt, 1e-6) / max(1e-9, c.extra["c"]))
                    e.g = g
                    isrc = g * c.extra["vc"]
                    inj[e.a] += isrc
                    inj[e.b] -= isrc
                elif e.kind == "led" or e.kind == "bridge":
                    c = e.owner
                    if e.kind == "led" and c.extra["blown"]:
                        e.g = 1e-12
                        e.on = False
                        continue
                    rd = max(0.01, c.extra["rd"])
                    vf = c.extra["vf"]
                    if e.on:
                        e.g = 1.0 / rd
                        k = e.g * vf
                        inj[e.a] += k
                        inj[e.b] -= k
                    else:
                        e.g = G_OFF

            for i, val in fixed.items():
                V[i] = val
            V[gnd] = 0.0
            iters = 0
            for iters in range(1, SOR_MAX_ITER + 1):
                maxd = 0.0
                for i in range(n1):
                    if i == gnd or i in fixed:
                        continue
                    num = inj[i]
                    den = 0.0
                    for m, ei in adj[i]:
                        g = edges[ei].g
                        num += g * V[m]
                        den += g
                    if den <= 0.0:
                        continue
                    nv = V[i] + omega * (num / den - V[i])
                    d = nv - V[i]
                    if d < 0:
                        d = -d
                    if d > maxd:
                        maxd = d
                    V[i] = nv
                if maxd < tol and iters >= SOR_MIN_ITER:
                    break
            total_iters += iters

            changed = False
            for e in diodes:
                if e.kind == "led" and e.owner.extra["blown"]:
                    continue
                v = V[e.a] - V[e.b]
                vf = e.owner.extra["vf"]
                rd = max(0.01, e.owner.extra["rd"])
                if e.on:
                    if (v - vf) / rd < -1e-7:
                        e.on = False
                        changed = True
                else:
                    if v > vf + 1e-9:
                        e.on = True
                        changed = True
            if not changed:
                break
        self.iters_used = total_iters

        # 4. currents, power, per-cell readouts
        for e in edges:
            e.va = V[e.a]
            e.vb = V[e.b]
        for li, pos, c in self.all_cells():
            c.pw = 0.0
        for li, pos, c in self.all_cells():
            j = self.nid.get((li, pos[0], pos[1]))
            if j is not None:
                c.v = V[j]
        for e in edges:
            i = (e.va - e.vb) * e.g
            ai = i if i >= 0 else -i
            if e.kind == "pair":
                if e.ra:
                    self.nodes[e.a][3].pw += i * i * e.ra
                if e.rb:
                    self.nodes[e.b][3].pw += i * i * e.rb
                ca, cb = self.nodes[e.a][3], self.nodes[e.b][3]
                if ai > abs(ca.extra.get("i", 0.0)):
                    ca.extra["i"] = ai
                if ai > abs(cb.extra.get("i", 0.0)):
                    cb.extra["i"] = ai
            elif e.kind == "res":
                c = e.owner
                c.extra["i"] = i
                c.v = e.va - e.vb
                c.pw = i * i * max(1e-4, c.extra["r"])
            elif e.kind == "led":
                c = e.owner
                ia = ((e.va - e.vb - c.extra["vf"]) / max(0.01, c.extra["rd"])
                      if e.on else 0.0)
                c.extra["i"] = ia if ia > 0 else 0.0
                c.v = e.va - e.vb
                c.pw = max(0.0, ia) * c.extra["vf"] + ia * ia * c.extra["rd"]
            elif e.kind == "bridge":
                c = e.owner
                ia = ((e.va - e.vb - c.extra["vf"]) / max(0.01, c.extra["rd"])
                      if e.on else 0.0)
                c.extra["i"] = ia if ia > 0 else 0.0
                c.v = e.va - e.vb
                c.pw = max(0.0, ia) * c.extra["vf"] + ia * ia * c.extra["rd"]
            elif e.kind == "cap":
                c = e.owner
                c.extra["vc"] = e.va - e.vb
                c.extra["i"] = i
                c.pw = i * i * max(1e-4, c.extra["esr"])
                c.v = c.extra["vc"]
            elif e.kind == "volt":
                c = e.owner
                c.v = e.va
            elif e.kind == "fan":
                c = e.owner
                c.v = e.va
                c.pw = (e.va - e.vb) ** 2 * e.g

        # source current readout
        for _, _, c in self.all_cells():
            if c.kind != "src" or c.extra.get("open"):
                continue
            plus = c.extra["na"]
            tot = 0.0
            for m, ei in adj[plus]:
                e = edges[ei]
                i = self.branch_i(e)
                tot += i if e.a == plus else -i
            c.extra["i"] = tot

        self.solve_ms = (_t.perf_counter() - t0) * 1000.0
        self.solved_once = True

    def branch_i(self, e):
        """Signed current flowing a -> b through an edge, sources included."""
        i = (self.V[e.a] - self.V[e.b]) * e.g
        if e.on and e.kind in ("led", "bridge"):
            i -= e.g * e.owner.extra["vf"]
        elif e.kind == "cap":
            i -= e.g * e.owner.extra["vc"]
        return i

    # ---- thermal -----------------------------------------------------
    def thermal(self, dt):
        fanf = {}
        for li, pos, c in self.all_cells():
            if c.kind != "fan":
                continue
            spin = min(1.0, abs(c.v) / 5.0)
            c.extra["spin"] = spin
            if spin <= 0.02:
                continue
            fx, fy = pos
            for dx in range(-4, 5):
                for dy in range(-4, 5):
                    d2 = dx * dx + dy * dy
                    if d2 > 16:
                        continue
                    f = (1.0 - math.sqrt(d2) / 4.0) * spin * 3.0
                    key = (li, fx + dx, fy + dy)
                    if f > fanf.get(key, 0.0):
                        fanf[key] = f

        for L in self.layers:
            li = L.idx
            cells = L.cells
            above = self.layers[li + 1] if li + 1 < len(self.layers) else None
            for (x, y), c in cells.items():
                k = c.kind
                cap = HEATCAP.get(k, 0.05)
                flux = 0.0
                tot = 0.0
                for dx, dy in DIRV:
                    nb = cells.get((x + dx, y + dy))
                    if nb is None:
                        continue
                    kp = kpair(c, nb)
                    tot += kp
                    flux += kp * (nb.temp - c.temp)
                if above is not None:
                    nb = above.cells.get((x, y))
                    if nb is not None:
                        kp = kpair(c, nb) * 0.8
                        tot += kp
                        flux += kp * (nb.temp - c.temp)
                h = CONV.get(k, 0.02) * (1.0 + fanf.get((li, x, y), 0.0))
                tot += h
                flux += h * (AMBIENT - c.temp)
                if tot <= 0.0:
                    continue
                alpha = dt * tot / cap
                if alpha > 0.5:
                    alpha = 0.5
                c.temp += alpha * (flux / tot)
                if c.pw:
                    dp = dt * c.pw / max(cap, 1e-6)
                    c.temp += min(dp, 60.0)

    # ---- failures ----------------------------------------------------
    def failures(self, dt):
        for L in list(self.layers):
            for pos in list(L.cells.keys()):
                c = L.cells.get(pos)
                if c is None:
                    continue
                self._fail_one(L, pos, c, dt)

    def _destroy(self, L, pos, c, kind="slag", temp=None, msg=None):
        L.cells[pos] = Cell(kind, "copper", c.d)
        L.cells[pos].temp = temp if temp is not None else c.temp
        self.dirty = True
        if msg:
            self.log(msg)

    def _fail_one(self, L, pos, c, dt):
        k = c.kind
        melt = mat_melt(c)
        x, y = pos

        # water evaporates when it boils
        if k == "water":
            if c.temp >= 99.5:
                L.cells.pop(pos, None)
                self.dirty = True
                self.log("water boiled off (steam)")
                return
            # electrified water eats any hardware it touches
            if abs(c.v) > 1.0:
                for dx, dy in DIRV:
                    nb = L.cells.get((x + dx, y + dy))
                    if nb is None or nb.kind in ("water", "sink", "wire", "slag"):
                        continue
                    nb.extra["dmg"] = nb.extra.get("dmg", 0.0) + dt * (0.25 + abs(c.v) * 0.25)
                    if nb.extra["dmg"] > 1.0:
                        self._destroy(L, (x + dx, y + dy), nb, "slag", 140.0,
                                      f"short circuit: {nb.kind} destroyed by water")
            return

        # dielectric breakdown
        if k == "cap":
            if abs(c.extra["vc"]) >= c.extra["bv"]:
                c.extra["found"] = True
                self.log(f"CAPACITOR EXPLODED at {abs(c.extra['vc']):.1f} V "
                         f"(hidden rating was {c.extra['bv']:.1f} V)")
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        nb = L.cells.get((x + dx, y + dy))
                        if nb is None:
                            continue
                        nb.temp += 260.0 * max(0.0, 1.0 - (abs(dx) + abs(dy)) / 4.0)
                self._destroy(L, pos, c, "slag", 420.0)
                return
            if abs(c.extra["vc"]) > 0.85 * c.extra["bv"]:
                c.extra["stress"] += dt
                if c.extra["stress"] > 2.0:
                    c.temp += dt * 55.0
                    if int(c.extra["stress"] * 4) % 8 == 0:
                        self.log("capacitor is whining: over 85% of its unknown rating")

        # LED abuse
        if k == "led" and not c.extra["blown"]:
            i = c.extra["i"]
            v = c.v
            if i > c.extra["imax"] * 1.9 or v > c.extra["rated"] or v < -5.0:
                c.extra["blown"] = True
                c.extra["bright"] = 0
                self.log("LED blew (overdriven)")
                self.dirty = True

        # resistor power rating
        if k == "res":
            if c.pw > c.extra["rating"] * 6.0:
                self.log("RESISTOR BLEW (way over its wattage)")
                self._destroy(L, pos, c, "slag", 380.0)
                return

        # heat: expand, crack, melt
        if c.temp >= melt:
            what = MATERIALS[c.mat]["name"].lower() + " wire" if k == "wire" else k
            self._destroy(L, pos, c, "slag", c.temp,
                          f"{what} melted at {c.temp:.0f} C")
            for dx, dy in DIRV:
                nb = L.cells.get((x + dx, y + dy))
                if nb is not None:
                    nb.temp += 25.0
            return

        if c.temp > melt * 0.55:
            c.extra["stress"] = c.extra.get("stress", 0.0) + dt
            if c.extra["stress"] > 8.0 and k == "wire" and c.extra.get("crack", 1.0) < 1.5:
                c.extra["crack"] = 1.45
                self.dirty = True
                self.log("thermal expansion cracked a wire joint (+45% resistance)")

    # ---- one simulation step ----------------------------------------
    def step(self, dt, do_solve=True):
        self.t += dt
        if do_solve:
            for _, _, c in self.all_cells():
                if c.kind in ("wire", "water", "via", "volt", "slag"):
                    c.extra["i"] = 0.0
            self.solve(dt)
        self._led_update()
        self.thermal(dt)
        self.failures(dt)

    def _led_update(self):
        for _, _, c in self.all_cells():
            if c.kind != "led":
                continue
            if c.extra["blown"]:
                c.extra["bright"] = 0
                continue
            i = c.extra["i"]
            imax = c.extra["imax"]
            if i <= 0.0004:
                c.extra["bright"] = 0
            else:
                f = min(1.0, (i - 0.0004) / max(1e-5, imax - 0.0004))
                c.extra["bright"] = 1 + int(f * 14)

    # ---- persistence -------------------------------------------------
    def to_dict(self):
        out = {"version": 1, "layers": [], "cur": self.cur}
        for L in self.layers:
            cells = []
            for (x, y), c in L.cells.items():
                cells.append({"x": x, "y": y, "k": c.kind, "m": c.mat,
                              "d": c.d, "t": round(c.temp, 2),
                              "e": {kk: vv for kk, vv in c.extra.items()
                                    if isinstance(vv, (int, float, bool, str))}})
            out["layers"].append({"w": L.w, "h": L.h, "cells": cells})
        return out

    def load_dict(self, data):
        self.layers = []
        for i, ld in enumerate(data.get("layers", [{"w": GRID_W, "h": GRID_H, "cells": []}])):
            L = Layer(i, ld.get("w", GRID_W), ld.get("h", GRID_H))
            self.layers.append(L)
            for cd in ld.get("cells", []):
                c = Cell(cd["k"], cd.get("m", "copper"), cd.get("d", 1))
                c.temp = cd.get("t", AMBIENT)
                for kk, vv in cd.get("e", {}).items():
                    if kk in c.extra:
                        c.extra[kk] = vv
                L.cells[(cd["x"], cd["y"])] = c
        self.cur = min(data.get("cur", 0), len(self.layers) - 1)
        self.dirty = True
        self.V = []


# --------------------------------------------------------------------------
# self test (no pygame needed)
# --------------------------------------------------------------------------

def place(b, x, y, kind, mat="copper", d=1, li=0):
    b.layers[li].cells[(x, y)] = Cell(kind, mat, d)
    b.dirty = True


def run(b, seconds, dt=1.0 / 60.0):
    steps = int(seconds / dt)
    for _ in range(steps):
        b.step(dt)


def ring(devs=(), volts=5.0):
    """Closed loop. Source (5,5): + at (6,5), - at (4,5).
    Device slots: (7,5) horizontal, (7,8) horizontal, (9,6) vertical."""
    b = Board()
    for x in range(6, 10):
        place(b, x, 5, "wire")
    for x in range(4, 10):
        place(b, x, 8, "wire")
    for y in (6, 7):
        place(b, 9, y, "wire")
        place(b, 4, y, "wire")
    place(b, 4, 5, "wire")
    place(b, 5, 5, "src", d=1)
    b.layers[0].cells[(5, 5)].extra["v"] = volts
    for x, y, kind, d, extra in devs:
        c = Cell(kind, d=d)
        if extra:
            c.extra.update(extra)
        b.layers[0].cells[(x, y)] = c
    b.dirty = True
    return b


def selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name + ("   " + detail if detail else ""))
        if not cond:
            ok = False

    print("WATT//LAB physics self test")
    print("-" * 62)

    # ---- 1. Ohm's law in a real loop ---------------------------------
    b = ring([(7, 5, "res", 1, {"r": 220.0})])
    run(b, 0.3)
    i = b.cell(5, 5).extra["i"]
    check("series loop current ~22.7 mA", abs(i - 0.0227) < 0.002, f"I={i*1000:.2f} mA")
    check("source + pin sits at 5 V", abs(b.cell(6, 5).v - 5.0) < 0.05, f"V={b.cell(6,5).v:.3f}")
    check("voltage falls across the resistor", abs(b.cell(8, 5).v) < 0.06,
          f"V={b.cell(8,5).v:.4f}")
    check("return rail reaches 0 V", abs(b.cell(4, 7).v) < 0.05, f"V={b.cell(4,7).v:.4f}")
    check("resistor dissipates I^2R", abs(b.cell(7, 5).pw - i * i * 220) < 0.01,
          f"P={b.cell(7,5).pw*1000:.2f} mW")

    # ---- 2. capacitor holds DC ---------------------------------------
    b2 = ring([(7, 5, "res", 1, {"r": 220.0}),
               (9, 6, "cap", 0, {"c": 470e-6, "bv": 100.0})])
    cap = b2.cell(9, 6)
    run(b2, 1.5)
    check("capacitor charges to supply", abs(cap.extra["vc"]) > 4.8,
          f"Vc={cap.extra['vc']:.3f} V")
    check("capacitor stores energy", 0.5 * cap.extra["c"] * cap.extra["vc"] ** 2 > 1e-3,
          f"E={0.5*cap.extra['c']*cap.extra['vc']**2*1e3:.2f} mJ")
    late = abs(b2.cell(5, 5).extra["i"])
    check("charged capacitor blocks DC", late < 0.002, f"I={late*1000:.3f} mA")

    # ---- 3. hidden dielectric breakdown ------------------------------
    b3 = ring([(7, 5, "res", 1, {"r": 220.0}),
               (9, 6, "cap", 0, {"c": 100e-6, "bv": 12.0})], volts=30.0)
    run(b3, 1.0)
    blown = b3.cell(9, 6).kind == "slag"
    boom = [m for _, m in b3.events if "CAPACITOR" in m]
    check("over-volted capacitor explodes", blown and bool(boom),
          boom[0] if boom else "no explosion event")

    # ---- 4. LED forward / reverse ------------------------------------
    b4 = ring([(7, 5, "led", 1, {}), (7, 8, "res", 1, {"r": 220.0})])
    run(b4, 0.3)
    led = b4.cell(7, 5)
    check("LED lights forward", led.extra["bright"] > 0,
          f"light {led.extra['bright']}/15  I={led.extra['i']*1000:.1f} mA")
    b5 = ring([(7, 5, "led", 3, {}), (7, 8, "res", 1, {"r": 220.0})])
    run(b5, 0.3)
    led2 = b5.cell(7, 5)
    check("LED blocks reverse", abs(led2.extra["i"]) < 1e-4 and led2.extra["bright"] == 0,
          f"I={led2.extra['i']*1000:.5f} mA")
    b5b = ring([(7, 5, "led", 1, {})])
    run(b5b, 0.4)
    check("unballasted LED blows on 5 V", b5b.cell(7, 5).extra["blown"],
          f"I={b5b.cell(7,5).extra['i']*1000:.0f} mA")

    # ---- 5. one-way bridge -------------------------------------------
    b6 = ring([(7, 5, "bridge", 1, {}), (7, 8, "res", 1, {"r": 220.0})])
    run(b6, 0.3)
    fwd = b6.cell(5, 5).extra["i"]
    b7 = ring([(7, 5, "bridge", 3, {}), (7, 8, "res", 1, {"r": 220.0})])
    run(b7, 0.3)
    rev = b7.cell(5, 5).extra["i"]
    check("bridge passes forward", fwd > 0.005, f"I={fwd*1000:.2f} mA")
    check("bridge blocks reverse", abs(rev) < 1e-5, f"I={rev*1000:.6f} mA")

    # ---- 6. voltmeter -------------------------------------------------
    b8 = ring([(7, 5, "res", 1, {"r": 220.0}), (6, 5, "volt", 1, {})])
    run(b8, 0.2)
    check("voltmeter reads the rail", abs(b8.cell(6, 5).v - 5.0) < 0.05,
          f"V={b8.cell(6, 5).v:.3f}")
    check("voltmeter reads 0 V past the resistor", abs(b8.cell(8, 5).v) < 0.1,
          f"V={b8.cell(8, 5).v:.3f}")

    # ---- 7. dead short cooks copper ----------------------------------
    b9 = ring()
    run(b9, 1.5)
    hottest = max((c.temp for _, _, c in b9.all_cells()), default=0.0)
    melted = any(c.kind == "slag" for _, _, c in b9.all_cells())
    check("short circuit melts the wire", hottest > 150.0 or melted,
          f"peak {hottest:.0f} C  melted={melted}")

    # ---- 8. heatsink pulls heat away ---------------------------------
    b10 = ring([(7, 5, "res", 1, {"r": 4.7, "rating": 5.0})])
    run(b10, 1.2)
    hot = b10.cell(7, 5).temp
    b10b = ring([(7, 5, "res", 1, {"r": 4.7, "rating": 5.0}),
                 (7, 6, "sink", 1, {}), (7, 4, "sink", 1, {})])
    run(b10b, 1.2)
    cool = b10b.cell(7, 5).temp
    check("heatsink cools the resistor", cool < hot - 2.0, f"{hot:.0f} C -> {cool:.0f} C")

    # ---- 9. water shorts the rails ------------------------------------
    b11 = ring([(7, 5, "res", 1, {"r": 220.0})])
    run(b11, 0.2)
    dry = b11.cell(5, 5).extra["i"]
    for x in range(5, 9):
        for y in (6, 7):
            b11.layers[0].cells[(x, y)] = Cell("water")
    b11.dirty = True
    run(b11, 0.4)
    wet = b11.cell(5, 5).extra["i"]
    check("water adds a leakage path", abs(wet) > abs(dry) + 0.002,
          f"{dry*1000:.1f} mA -> {wet*1000:.1f} mA")

    # ---- 10. water destroys hardware ----------------------------------
    b11b = ring([(7, 5, "res", 1, {"r": 220.0})])
    b11b.layers[0].cells[(5, 5)].extra["v"] = 24.0
    for dx, dy in ((6, 6), (5, 6), (4, 6), (6, 7), (5, 7), (4, 7)):
        b11b.layers[0].cells[(dx, dy)] = Cell("water")
    for y in (7,):
        b11b.layers[0].cells[(4, 7)] = Cell("water")
    b11b.dirty = True
    run(b11b, 3.0)
    wrecked = any(c.kind == "slag" for _, _, c in b11b.all_cells())
    check("electrified water wrecks hardware", wrecked or len(b11b.events) > 0,
          b11b.events[-1][1] if b11b.events else "no damage")

    # ---- 11. vias link layers ----------------------------------------
    b12 = Board()
    place(b12, 5, 5, "src", d=1)
    place(b12, 4, 5, "wire")
    place(b12, 6, 5, "wire")
    place(b12, 7, 5, "via")
    b12.add_layer()
    place(b12, 7, 5, "via", li=1)
    place(b12, 8, 5, "wire", li=1)
    place(b12, 8, 5, "volt", li=1)
    b12.dirty = True
    run(b12, 0.3)
    v = b12.layers[1].cells[(8, 5)].v
    check("via carries power to another layer", v > 4.5, f"layer 1 V={v:.3f}")

    print("-" * 62)
    print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def shade(c, f):
    r, g, b = c
    return (max(0, min(255, int(r * f))), max(0, min(255, int(g * f))),
            max(0, min(255, int(b * f))))


def mix(c1, c2, t):
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


class Renderer:
    def __init__(self, screen, fonts):
        self.s = screen
        self.f_tiny, self.f_small, self.f_mid, self.f_big = fonts
        self.cache = {}
        self.glow = [self._make_glow(l) for l in range(16)]

    def _make_glow(self, level):
        import pygame
        size = CELL * 4
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        a = int(8 + level * 4)
        steps = ((size, a // 3), (int(size * 0.62), a // 2), (int(size * 0.34), a))
        for sz, alpha in steps:
            o = (size - sz) // 2
            surf.fill((255, 226, int(120 + level * 6), alpha), (o, o, sz, sz))
        return surf

    def size(self, txt, font=None):
        return (font or self.f_small).size(txt)

    def text(self, txt, x, y, color, font=None):
        import pygame
        font = font or self.f_small
        key = (txt, id(font), color)
        surf = self.cache.get(key)
        if surf is None:
            if len(self.cache) > 4000:
                self.cache.clear()
            surf = font.render(txt, False, color)
            self.cache[key] = surf
        self.s.blit(surf, (x, y))
        return surf.get_width()

    # ---- board -------------------------------------------------------
    def cell(self, px, py, c, dim=False):
        import pygame
        s = self.s
        f = 0.35 if dim else 1.0
        k = c.kind
        if k == "wire":
            base = MATERIALS[c.mat]["color"]
        elif k == "led":
            lvl = c.extra.get("bright", 0)
            base = KIND_COLOR["led"] if lvl == 0 else mix((40, 46, 34), (255, 244, 176), lvl / 15.0)
            if c.extra.get("blown"):
                base = (54, 40, 40)
        else:
            base = KIND_COLOR.get(k, (120, 120, 120))
        # energised wires glow a touch
        if k in ("wire", "via", "water", "volt") and not dim:
            v = abs(c.v)
            if v > 0.2:
                base = mix(base, (210, 240, 255), min(0.28, v / 30.0))
        col = shade(base, f)
        s.fill(col, (px, py, CELL, CELL))
        s.fill(shade(col, 0.72), (px, py, CELL, 1))
        s.fill(shade(col, 0.72), (px, py + CELL - 1, CELL, 1))
        s.fill(shade(col, 0.72), (px, py, 1, CELL))
        s.fill(shade(col, 0.72), (px + CELL - 1, py, 1, CELL))

        cx, cy = px + CELL // 2, py + CELL // 2
        if k == "wire":
            s.fill(shade(col, 1.35), (px + 2, py + 2, CELL - 4, CELL - 4))
            s.fill(shade(col, 0.9), (cx - 1, py + 2, 2, CELL - 4))
            s.fill(shade(col, 0.9), (px + 2, cy - 1, CELL - 4, 2))
        elif k == "src":
            s.fill((255, 226, 226), (cx - 4, cy - 1, 8, 2))
            s.fill((255, 226, 226), (cx - 1, cy - 4, 2, 8))
            if c.extra.get("open"):
                s.fill((255, 80, 80), (px + 2, py + 2, 3, 3))
        elif k == "cap":
            s.fill((226, 240, 255), (px + 4, py + 3, 2, CELL - 6))
            s.fill((226, 240, 255), (px + CELL - 6, py + 3, 2, CELL - 6))
            s.fill(shade(col, 0.6), (cx - 1, py + 3, 2, CELL - 6))
        elif k == "volt":
            s.fill((40, 36, 20), (px + 3, py + 3, CELL - 6, CELL - 6))
            s.fill((255, 240, 170), (px + 5, py + 5, 2, 6))
            s.fill((255, 240, 170), (px + CELL - 7, py + 5, 2, 6))
            s.fill((255, 240, 170), (px + 6, py + 10, 4, 2))
        elif k == "bridge":
            dx, dy = DIRV[c.d % 4]
            s.fill(shade(col, 0.55), (px + 2, py + 2, CELL - 4, CELL - 4))
            self._arrow(cx, cy, dx, dy, (215, 255, 210), 5)
        elif k == "led":
            lvl = c.extra.get("bright", 0)
            s.fill(shade(col, 0.8), (px + 3, py + 3, CELL - 6, CELL - 6))
            core = (24, 26, 22) if lvl == 0 else mix((60, 60, 40), (255, 250, 210), lvl / 15.0)
            s.fill(core, (px + 5, py + 5, CELL - 10, CELL - 10))
        elif k == "res":
            s.fill((44, 34, 24), (px + 3, py + 4, CELL - 6, CELL - 8))
            for i, band in enumerate(((120, 60, 50), (60, 90, 120), (200, 180, 120))):
                s.fill(band, (px + 5 + i * 3, py + 4, 1, CELL - 8))
        elif k == "sink":
            s.fill(shade(col, 1.25), (px + 2, py + 2, CELL - 4, CELL - 4))
            for i in range(3):
                s.fill(shade(col, 0.6), (px + 4 + i * 4, py + 3, 1, CELL - 6))
        elif k == "fan":
            spin = c.extra.get("spin", 0.0)
            s.fill(shade(col, 0.5), (px + 2, py + 2, CELL - 4, CELL - 4))
            r = 3 + int(spin * 2)
            s.fill((230, 250, 255), (cx - r, cy - 1, 2 * r, 2))
            s.fill((230, 250, 255), (cx - 1, cy - r, 2, 2 * r))
        elif k == "via":
            s.fill(shade(col, 0.55), (px + 2, py + 2, CELL - 4, CELL - 4))
            s.fill((250, 232, 255), (cx - 3, cy - 3, 6, 6))
            s.fill(shade(col, 0.3), (cx - 1, cy - 1, 2, 2))
        elif k == "water":
            s.fill(shade(col, 0.75), (px, py + 4, CELL, CELL - 8))
            s.fill(shade(col, 1.3), (px + 2, py + 7, CELL - 4, 1))
        elif k == "slag":
            s.fill((60, 54, 54), (px + 2, py + 3, CELL - 4, CELL - 6))
            s.fill((36, 32, 32), (px + 4, py + 6, 2, 2))
            s.fill((36, 32, 32), (px + 9, py + 9, 2, 2))

        # heat bloom
        if not dim:
            t = c.temp
            if t > 70.0:
                a = min(0.75, (t - 70.0) / 320.0)
                hot = (255, int(90 + 110 * (1 - a)), 30)
                s.fill(mix(col, hot, min(1.0, a)), (px + 1, py + 1, CELL - 2, CELL - 2))

    def _arrow(self, cx, cy, dx, dy, col, r):
        import pygame
        if dx:
            tip = cx + dx * r
            pygame.draw.polygon(self.s, col, [(tip, cy), (tip - dx * 5, cy - 4), (tip - dx * 5, cy + 4)])
        else:
            tip = cy + dy * r
            pygame.draw.polygon(self.s, col, [(cx, tip), (cx - 4, tip - dy * 5), (cx + 4, tip - dy * 5)])

    def led_glow(self, px, py, level):
        g = self.glow[min(15, max(0, level))]
        self.s.blit(g, (px + CELL // 2 - g.get_width() // 2,
                        py + CELL // 2 - g.get_height() // 2),
                    special_flags=16)  # BLEND_RGBA_ADD


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

def _fonts():
    import pygame
    cands = ["dejavusansmono", "consolas", "liberationmono", "couriernew", "monospace"]
    path = None
    for c in cands:
        path = pygame.font.match_font(c)
        if path:
            break
    def mk(sz):
        if path:
            return pygame.font.Font(path, sz)
        return pygame.font.SysFont("monospace", sz)
    return mk(11), mk(12), mk(13), mk(17)


def _world_to_cell(mx, my):
    x = (mx - BOARD_X) // CELL
    y = (my - BOARD_Y) // CELL
    return int(x), int(y)


def _cell_px(x, y):
    return BOARD_X + x * CELL, BOARD_Y + y * CELL


class App:
    def __init__(self):
        import pygame
        pygame.init()
        pygame.display.set_caption(APP_TITLE)
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        self.clock = pygame.time.Clock()
        self.R = Renderer(self.screen, _fonts())
        self.b = Board()
        self.tool = 0
        self.mat = 0
        self.dir = 1
        self.paused = False
        self.acc = 0.0
        self.step_n = 0
        self.hover = None
        self.painting = False
        self.last_cell = None
        self.dir_anchor = None
        self.mat_editor = False
        self.mat_field = 0
        self.help = False
        self.def_src = 5.0
        self.def_res = 220.0
        self.def_cap = 470e-6
        self.status = "ready"
        self.warn = 0.0
        self.frames = 0
        self.demo()

    # ---- starter circuit ---------------------------------------------
    def demo(self):
        b = self.b
        L = b.layers[0]
        for x in range(6, 26):
            L.cells[(x, 8)] = Cell("wire")
        L.cells[(8, 8)] = Cell("src", d=1)
        L.cells[(8, 8)].extra["v"] = 5.0
        L.cells[(13, 8)] = Cell("res")
        L.cells[(13, 8)].extra["r"] = 220.0
        L.cells[(17, 8)] = Cell("led", d=1)
        L.cells[(10, 8)] = Cell("volt")
        L.cells[(15, 8)] = Cell("volt")
        for y in range(9, 12):
            L.cells[(25, y)] = Cell("wire")
        for x in range(6, 26):
            L.cells[(x, 12)] = Cell("wire")
        for y in range(9, 12):
            L.cells[(6, y)] = Cell("wire")
        L.cells[(17, 9)] = Cell("sink")
        b.dirty = True
        b.log("demo board loaded: 5 V -> 220R -> LED")

    # ---- helpers -----------------------------------------------------
    def cur_kind(self):
        return TOOL_KINDS[self.tool]

    def make_cell(self, kind):
        c = Cell(kind, MAT_ORDER[self.mat], self.dir)
        if kind == "src":
            c.extra["v"] = self.def_src
        elif kind == "res":
            c.extra["r"] = self.def_res
        elif kind == "cap":
            c.extra["c"] = self.def_cap
        return c

    def place_at(self, x, y):
        kind = self.cur_kind()
        L = self.b.layer
        if not (0 <= x < L.w and 0 <= y < L.h):
            return
        old = L.cells.get((x, y))
        if old is not None and old.kind == kind and kind in ("wire", "water"):
            return
        L.cells[(x, y)] = self.make_cell(kind)
        self.b.dirty = True

    def erase_at(self, x, y):
        L = self.b.layer
        if L.cells.pop((x, y), None) is not None:
            self.b.dirty = True

    def paint_line(self, a, bpt):
        x0, y0 = a
        x1, y1 = bpt
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        x, y = x0, y0
        while True:
            self.place_at(x, y)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

    def set_dir_from_drag(self):
        if not self.dir_anchor or not self.hover:
            return
        ax, ay = self.dir_anchor
        hx, hy = self.hover
        dx, dy = hx - ax, hy - ay
        if dx == 0 and dy == 0:
            return
        if abs(dx) >= abs(dy):
            self.dir = 1 if dx > 0 else 3
        else:
            self.dir = 2 if dy > 0 else 0
        c = self.b.layer.cells.get((ax, ay))
        if c is not None and c.kind in ("src", "led", "bridge", "res", "cap"):
            c.d = self.dir
            self.b.dirty = True
        elif c is None and self.inside((ax, ay)):
            self.place_at(ax, ay)
            nc = self.b.layer.cells.get((ax, ay))
            if nc is not None:
                nc.d = self.dir

    def adjust(self, delta):
        c = self.b.layer.cells.get(self.hover) if self.hover else None
        if c is None:
            if self.cur_kind() == "src":
                self.def_src = min(60.0, max(0.5, self.def_src + delta * 0.5))
                self.status = f"new source = {self.def_src:.1f} V"
            elif self.cur_kind() == "res":
                self.def_res = min(1e6, max(0.5, self.def_res * (1.6 if delta > 0 else 1 / 1.6)))
                self.status = f"new resistor = {self.def_res:.1f} ohm"
            elif self.cur_kind() == "cap":
                self.def_cap = min(1.0, max(1e-6, self.def_cap * (2.0 if delta > 0 else 0.5)))
                self.status = f"new capacitor = {self.def_cap*1e6:.0f} uF"
            return
        k = c.kind
        if k == "src":
            c.extra["v"] = min(60.0, max(0.0, c.extra["v"] + delta * 0.5))
            self.b.dirty = True
            self.status = f"source set to {c.extra['v']:.1f} V"
        elif k == "res":
            c.extra["r"] = min(1e6, max(0.5, c.extra["r"] * (1.6 if delta > 0 else 1 / 1.6)))
            self.b.dirty = True
            self.status = f"resistor set to {c.extra['r']:.0f} ohm"
        elif k == "cap":
            c.extra["c"] = min(1.0, max(1e-6, c.extra["c"] * (2.0 if delta > 0 else 0.5)))
            self.status = f"capacitor set to {c.extra['c']*1e6:.0f} uF"
        elif k == "led":
            pass

    # ---- events ------------------------------------------------------
    def handle(self, e):
        import pygame
        if e.type == pygame.QUIT:
            return False
        if e.type == pygame.KEYDOWN:
            k = e.key
            if k == pygame.K_ESCAPE:
                if self.help:
                    self.help = False
                elif self.mat_editor:
                    self.mat_editor = False
                else:
                    return False
                return True
            if k == pygame.K_F1:
                self.help = not self.help
            if self.mat_editor:
                self.mat_keys(k)
                return True
            if k == pygame.K_SPACE:
                self.paused = not self.paused
                self.status = "paused" if self.paused else "running"
            elif k == pygame.K_f:
                self.b.step(1 / SIM_HZ)
            elif k == pygame.K_r:
                self.dir = (self.dir + 1) % 4
                self.status = f"direction {DIRNAME[self.dir]}"
            elif k == pygame.K_m:
                self.mat = (self.mat + 1) % len(MAT_ORDER)
                self.status = f"wire material: {MATERIALS[MAT_ORDER[self.mat]]['name']}"
            elif k == pygame.K_c:
                self.mat_editor = True
            elif k == pygame.K_n:
                self.b.add_layer()
            elif k == pygame.K_x:
                self.b.layer.cells.clear()
                self.b.dirty = True
                self.b.log(f"layer {self.b.cur} cleared")
            elif k == pygame.K_LEFTBRACKET:
                self.b.cur = max(0, self.b.cur - 1)
            elif k == pygame.K_RIGHTBRACKET:
                self.b.cur = min(len(self.b.layers) - 1, self.b.cur + 1)
            elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.adjust(-1)
            elif k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self.adjust(1)
            elif k == pygame.K_F5:
                self.save()
            elif k == pygame.K_F9:
                self.load()
            else:
                ch = e.unicode.upper() if e.unicode else ""
                for i, (kind, name, key, short) in enumerate(TOOLS):
                    if ch == key:
                        self.tool = i
                        self.status = name
        if e.type == pygame.MOUSEMOTION or e.type == pygame.MOUSEBUTTONDOWN:
            mx, my = e.pos
            self.hover = _world_to_cell(mx, my)
        if e.type == pygame.MOUSEBUTTONDOWN:
            if e.button == 1:
                if e.mod & pygame.KMOD_LSHIFT or e.mod & pygame.KMOD_RSHIFT:
                    self.dir_anchor = self.hover
                    self.status = "drag to aim, then release"
                else:
                    self.painting = True
                    self.last_cell = self.hover
                    if self.inside(self.hover):
                        self.place_at(*self.hover)
            elif e.button == 3:
                self.painting = True
                self.last_cell = None
                if self.inside(self.hover):
                    self.erase_at(*self.hover)
        if e.type == pygame.MOUSEBUTTONUP:
            self.painting = False
            self.dir_anchor = None
            self.last_cell = None
        if e.type == pygame.MOUSEMOTION and self.painting:
            if not self.inside(self.hover):
                return True
            if pygame.mouse.get_pressed()[2]:
                self.erase_at(*self.hover)
            else:
                if self.last_cell is None:
                    self.last_cell = self.hover
                self.paint_line(self.last_cell, self.hover)
                self.last_cell = self.hover
        if e.type == pygame.MOUSEMOTION and self.dir_anchor:
            self.set_dir_from_drag()
        return True

    def inside(self, p):
        L = self.b.layer
        return p and 0 <= p[0] < L.w and 0 <= p[1] < L.h

    def mat_keys(self, k):
        import pygame
        m = MATERIALS["custom"]
        if k == pygame.K_UP:
            self.mat_field = (self.mat_field - 1) % 3
        elif k == pygame.K_DOWN:
            self.mat_field = (self.mat_field + 1) % 3
        elif k in (pygame.K_LEFT, pygame.K_RIGHT):
            s = 1.25 if k == pygame.K_RIGHT else 1 / 1.25
            if self.mat_field == 0:
                m["res"] = min(10.0, max(0.002, m["res"] * s))
            elif self.mat_field == 1:
                m["amp"] = min(20.0, max(0.1, m["amp"] * s))
            else:
                m["k"] = min(2000.0, max(1.0, m["k"] * s))
            self.b.dirty = True

    # ---- persistence -------------------------------------------------
    def save(self):
        try:
            with open(SAVE_NAME, "w") as fh:
                json.dump(self.b.to_dict(), fh)
            self.status = f"saved {SAVE_NAME}"
            self.b.log("build saved")
        except OSError as ex:
            self.status = f"save failed: {ex}"

    def load(self):
        if not os.path.exists(SAVE_NAME):
            self.status = "no saved build yet"
            return
        try:
            with open(SAVE_NAME) as fh:
                self.b.load_dict(json.load(fh))
            self.status = f"loaded {SAVE_NAME}"
            self.b.log("build loaded")
        except (OSError, ValueError, KeyError) as ex:
            self.status = f"load failed: {ex}"

    # ---- main loop ---------------------------------------------------
    def update(self, dt):
        if not self.paused:
            self.acc += dt
            step = 1.0 / SIM_HZ
            guard = 0
            while self.acc >= step and guard < 6:
                self.acc -= step
                self.step_n += 1
                self.b.step(step, do_solve=(self.step_n % SOLVE_EVERY == 0))
                guard += 1
        if self.warn > 0:
            self.warn = max(0.0, self.warn - dt)

    def draw(self):
        import pygame
        s = self.screen
        R = self.R
        s.fill((16, 17, 21))
        self.draw_header()
        self.draw_board()
        self.draw_panel()
        self.draw_tooltip()
        self.draw_bar()
        if self.help:
            self.draw_help()
        if self.mat_editor:
            self.draw_mat_editor()

    def draw_header(self):
        import pygame
        R = self.R
        s = self.screen
        s.fill((22, 24, 30), (0, 0, WIN_W, BOARD_Y - 6))
        R.text("WATT//LAB", 14, 14, (255, 214, 120), R.f_big)
        R.text("pixel electronics sandbox", 132, 20, (110, 118, 132), R.f_tiny)
        x = BOARD_X
        w = 78
        for i, (kind, name, key, short) in enumerate(TOOLS):
            sel = (i == self.tool)
            col = (46, 52, 64) if sel else (30, 33, 41)
            s.fill(col, (x, 34, w - 4, 20))
            if sel:
                s.fill((255, 214, 120), (x, 34, 2, 20))
            R.text(key, x + 5, 39, (255, 214, 120) if sel else (120, 128, 142), R.f_tiny)
            R.text(short, x + 16, 39,
                   (238, 242, 250) if sel else (150, 158, 172), R.f_tiny)
            x += w

    def draw_board(self):
        import pygame
        R = self.R
        s = self.screen
        bw = GRID_W * CELL
        bh = GRID_H * CELL
        s.fill((20, 21, 26), (BOARD_X, BOARD_Y, bw, bh))
        # grid
        for gx in range(GRID_W + 1):
            a = 22 if gx % 5 else 30
            s.fill((a, a + 3, a + 8), (BOARD_X + gx * CELL, BOARD_Y, 1, bh))
        for gy in range(GRID_H + 1):
            a = 22 if gy % 5 else 30
            s.fill((a, a + 3, a + 8), (BOARD_X, BOARD_Y + gy * CELL, bw, 1))
        # ghost of layers below
        for li in range(self.b.cur):
            for (x, y), c in self.b.layers[li].cells.items():
                if 0 <= x < GRID_W and 0 <= y < GRID_H:
                    px, py = _cell_px(x, y)
                    R.cell(px, py, c, dim=True)
        if self.b.cur > 0:
            R.text(f"ghost: layers 0..{self.b.cur-1}", BOARD_X + 6, BOARD_Y + 6,
                   (90, 96, 108), R.f_tiny)
        # current layer
        for (x, y), c in self.b.layer.cells.items():
            if 0 <= x < GRID_W and 0 <= y < GRID_H:
                px, py = _cell_px(x, y)
                R.cell(px, py, c)
        for (x, y), c in self.b.layer.cells.items():
            if c.kind == "led" and c.extra.get("bright", 0) and 0 <= x < GRID_W:
                px, py = _cell_px(x, y)
                R.led_glow(px, py, c.extra["bright"])
        # preview
        if self.inside(self.hover) and not self.mat_editor:
            px, py = _cell_px(*self.hover)
            ghost = self.make_cell(self.cur_kind())
            ghost.temp = AMBIENT
            R.cell(px, py, ghost, dim=True)
            s.fill((255, 255, 255), (px, py, CELL, 1))
            s.fill((255, 255, 255), (px, py + CELL - 1, CELL, 1))
            s.fill((255, 255, 255), (px, py, 1, CELL))
            s.fill((255, 255, 255), (px + CELL - 1, py, 1, CELL))
            if ghost.kind in ("src", "led", "bridge", "res", "cap"):
                dx, dy = DIRV[self.dir % 4]
                R._arrow(px + CELL // 2, py + CELL // 2, dx, dy, (255, 255, 255), 6)
        # border
        pygame.draw.rect(s, (52, 58, 70), (BOARD_X - 1, BOARD_Y - 1, bw + 2, bh + 2), 1)

    def draw_panel(self):
        import pygame
        R = self.R
        s = self.screen
        s.fill((22, 24, 30), (PANEL_X, BOARD_Y - 24, PANEL_W, WIN_H - BOARD_Y + 24))
        x = PANEL_X + 10
        y = BOARD_Y - 16
        lh = 16
        wlabel = PANEL_W - 26

        def row(txt, col=(150, 158, 172), tfont=None):
            nonlocal y
            R.text(txt, x, y, col, tfont or R.f_small)
            y += lh

        def head(txt):
            nonlocal y
            y += 4
            s.fill((38, 42, 52), (x, y, wlabel, 15))
            R.text(txt, x + 4, y + 2, (255, 214, 120), R.f_tiny)
            y += 19

        head("WIRE MATERIAL   [M]")
        key = MAT_ORDER[self.mat]
        m = MATERIALS[key]
        tint = (232, 236, 244)
        row(m["name"].upper() + ("  (editable, C)" if key == "custom" else ""), tint)
        row(f"resistance {m['res']:.3f} ohm/tile")
        row(f"ampacity  {m['amp']:.2f} A")
        row(f"thermal k {m['k']:.0f}")
        s.fill(m["color"], (x, y + 1, wlabel, 4))
        y += 10

        head("CURSOR")
        c = self.b.layer.cells.get(self.hover) if self.hover else None
        if c is None:
            row("empty tile" if self.inside(self.hover) else "off board", (96, 102, 114))
        else:
            title = (f"WIRE  {MATERIALS[c.mat]['name']}" if c.kind == "wire"
                     else c.kind.upper())
            row(title, (238, 242, 250))
            if c.kind in ("wire", "water", "volt", "via", "slag"):
                row(f"voltage  {c.v:8.3f} V", (170, 210, 255))
                row(f"current  {c.extra.get('i', 0)*1000:8.2f} mA", (170, 250, 190))
            if c.kind == "src":
                row(f"setpoint {c.extra['v']:.1f} V   [ - = ]", (255, 190, 190))
                row(f"output   {c.extra.get('i', 0)*1000:.2f} mA", (170, 250, 190))
                if c.extra.get("open"):
                    row("! needs wire on both pins", (255, 120, 120))
            if c.kind == "res":
                row(f"resistance {c.extra['r']:.0f} ohm  [-=]", (255, 220, 180))
                row(f"drop {abs(c.v):.3f} V   {abs(c.extra['i'])*1000:.2f} mA")
                col = (255, 150, 110) if c.pw > c.extra["rating"] * 3 else (150, 158, 172)
                row(f"power {c.pw*1000:.1f} mW / {c.extra['rating']*1000:.0f} mW", col)
            if c.kind == "cap":
                seen = c.extra["found"]
                rating = f"{c.extra['bv']:.1f} V (learned)" if seen else "??? (find out)"
                row(f"capacitance {c.extra['c']*1e6:.0f} uF [-=]", (180, 210, 255))
                row(f"charge  {abs(c.extra['vc']):.2f} V")
                row(f"stored  {0.5*c.extra['c']*c.extra['vc']**2*1e3:.2f} mJ")
                row(f"rating  {rating}", (255, 170, 150))
                frac = abs(c.extra["vc"]) / max(1e-6, c.extra["bv"])
                row("stress " + "#" * int(min(1.0, frac) * 20), (255, 140, 90))
            if c.kind == "led":
                lvl = c.extra["bright"]
                row(f"light   {lvl:2d}/15 " + "#" * lvl, (255, 236, 150))
                row(f"voltage {c.v:.2f} V   (Vf {c.extra['vf']:.1f})")
                row(f"current {c.extra['i']*1000:.2f} mA")
                if c.extra["blown"]:
                    row("BLOWN", (255, 120, 120))
            if c.kind == "bridge":
                row(f"facing {DIRNAME[c.d % 4]}  (LMB+SHIFT)", (180, 255, 200))
                row(f"drop {abs(c.v):.2f} V   {c.extra['i']*1000:.2f} mA")
            if c.kind == "fan":
                row(f"spin {c.extra.get('spin', 0)*100:.0f}%  draw {c.pw*1000:.0f} mW")
                if c.extra.get("spin", 0) < 0.05:
                    row("! needs ~5 V next to it", (255, 160, 120))
            if c.kind == "sink":
                row("pulls heat from neighbours")
            if c.kind == "slag":
                row("destroyed / molten", (255, 120, 120))
            if c.kind != "sink" and c.kind != "water":
                col = (255, 170, 110) if c.temp > 80 else (150, 158, 172)
                row(f"temp    {c.temp:6.1f} C  (melt {mat_melt(c):.0f})", col)
            if c.extra.get("open") and c.kind in ("led", "res", "bridge", "cap"):
                row("! needs wire on both ends", (255, 120, 120))

        head("LAYER")
        row(f"{self.b.cur + 1} of {len(self.b.layers)}   [ ] switch   N new")
        row(f"tiles here {len(self.b.layer.cells)}   total "
            f"{sum(len(L.cells) for L in self.b.layers)}")
        row(f"nets {len(self.b.nodes)}   layers share vias")

        head("SIMULATION")
        row(("PAUSED" if self.paused else "RUNNING") + "   SPACE", (255, 214, 120))
        row(f"t {self.b.t:7.2f} s   solve {self.b.solve_ms:4.1f} ms")
        row(f"{self.b.iters_used} sweeps   {self.b.passes} passes")
        row(self.status[:40], (120, 220, 200))

        head("LOG")
        for t, msg in self.b.events[-4:]:
            wrap = msg
            first = True
            while wrap and y < WIN_H - 16:
                chunk, wrap = wrap[:46], wrap[46:]
                R.text(chunk, x, y, (200, 168, 140) if first else (150, 140, 130),
                       R.f_tiny)
                y += 13
                first = False

    def tooltip_lines(self, c):
        k = c.kind
        if k == "wire":
            return [f"WIRE {MATERIALS[c.mat]['name']}",
                    f"{c.v:.3f} V   {abs(c.extra.get('i',0))*1000:.2f} mA",
                    f"{c.temp:.0f} C"], (190, 220, 255)
        if k == "src":
            return [f"SOURCE {c.extra['v']:.1f} V",
                    f"{c.extra.get('i',0)*1000:.1f} mA out"], (255, 170, 170)
        if k == "volt":
            return [f"VOLTMETER", f"{c.v:.3f} V"], (255, 240, 160)
        if k == "cap":
            r = f"{c.extra['bv']:.1f} V" if c.extra["found"] else "??? V"
            return [f"CAPACITOR {c.extra['c']*1e6:.0f} uF",
                    f"{abs(c.extra['vc']):.2f} V / {r}"], (170, 210, 255)
        if k == "led":
            if c.extra["blown"]:
                return ["LED  BLOWN", "0/15"], (255, 140, 140)
            return [f"LED  {c.extra['bright']}/15", f"{c.v:.2f} V  {c.extra['i']*1000:.1f} mA"], (255, 240, 170)
        if k == "res":
            return [f"RESISTOR {c.extra['r']:.0f} ohm",
                    f"{abs(c.v):.2f} V  {abs(c.extra['i'])*1000:.1f} mA"], (255, 220, 180)
        if k == "bridge":
            arrow = ("^", ">", "v", "<")[c.d % 4]
            return [f"BRIDGE {arrow}", f"{c.extra['i']*1000:.1f} mA"], (180, 255, 200)
        if k == "sink":
            return [f"HEATSINK {c.temp:.0f} C"], (200, 210, 225)
        if k == "fan":
            return [f"FAN {c.extra.get('spin',0)*100:.0f}%", f"{c.temp:.0f} C"], (170, 230, 255)
        if k == "water":
            return ["WATER conducts", f"{c.v:.1f} V  {c.temp:.0f} C"], (120, 180, 255)
        if k == "via":
            return ["VIA to layer above", f"{c.v:.2f} V"], (210, 180, 255)
        if k == "slag":
            return ["DESTROYED", f"{c.temp:.0f} C"], (255, 130, 130)
        return [k.upper()], (200, 200, 200)

    def draw_tooltip(self):
        import pygame
        R = self.R
        s = self.screen
        if self.help or self.mat_editor or not self.inside(self.hover):
            return
        c = self.b.layer.cells.get(self.hover)
        if c is None:
            return
        lines, col = self.tooltip_lines(c)
        w = max(R.size(l, R.f_tiny)[0] for l in lines) + 12
        h = len(lines) * 13 + 8
        px, py = _cell_px(*self.hover)
        tx, ty = px + CELL + 6, py + CELL + 2
        if tx + w > PANEL_X - 6:
            tx = px - w - 6
        if ty + h > BAR_Y - 4:
            ty = py - h - 4
        if tx < 2:
            tx = 2
        if ty < 2:
            ty = 2
        s.fill((10, 11, 15), (tx, ty, w, h))
        pygame.draw.rect(s, col, (tx, ty, w, h), 1)
        yy = ty + 4
        for i, l in enumerate(lines):
            R.text(l, tx + 6, yy, col if i == 0 else (208, 214, 224), R.f_tiny)
            yy += 13

    def draw_bar(self):
        import pygame
        R = self.R
        s = self.screen
        s.fill((20, 22, 28), (0, BAR_Y + 4, WIN_W, WIN_H - BAR_Y))
        hints = ("LMB place   RMB erase   LMB+SHIFT aim (4 dirs)   R rotate   M material   "
                 "C custom alloy   1-9,0,K tools   - = value   SPACE pause   F1 help")
        R.text(hints, 12, BAR_Y + 10, (128, 136, 150), R.f_tiny)
        R.text("WATT//LAB v1.0  -  physics is the game", 12, BAR_Y + 26, (70, 76, 88), R.f_tiny)

    def draw_help(self):
        import pygame
        R = self.R
        s = self.screen
        w, h = 640, 430
        x, y = (WIN_W - w) // 2, (WIN_H - h) // 2
        s.fill((12, 13, 17), (x, y, w, h))
        pygame.draw.rect(s, (255, 214, 120), (x, y, w, h), 1)
        lines = [
            ("WATT//LAB HELP  (F1 to close)", (255, 214, 120)),
            ("", None),
            ("TOOLS          1 wire   2 source   3 capacitor   4 voltmeter", (200, 206, 216)),
            ("               5 one-way bridge   6 LED   7 resistor", None),
            ("               8 heatsink   9 fan   0 via   K water", None),
            ("", None),
            ("MOUSE          LMB place / drag     RMB erase", (200, 206, 216)),
            ("               LMB + SHIFT + drag = set the 4-way direction", None),
            ("", None),
            ("KEYS           R rotate   M wire material   C custom alloy editor", (200, 206, 216)),
            ("               - / = change source volts, resistance, capacitance", None),
            ("               SPACE pause   F single step   [ ] layers   N new layer", None),
            ("               X clear layer   F5 save   F9 load   ESC quit", None),
            ("", None),
            ("PHYSICS NOTES", (255, 214, 120)),
            ("  * wires are 2-way and near instant; every tile adds resistance", (150, 200, 255)),
            ("    so power degrades into heat along the run", None),
            ("  * a source needs wire on BOTH pins (+ front, - back)", None),
            ("  * resistor / LED / bridge / capacitor are 2-terminal: aim them", None),
            ("    along a wire and put wire on both sides", None),
            ("  * capacitor ratings are hidden. stress bar > 85% = about to go", None),
            ("  * water conducts. puddles short rails and dissolve hardware", None),
            ("  * vias link a tile to the same tile on the layer above", None),
            ("  * fans only spin when they touch a powered node (they sink ~80 mA)", None),
            ("  * heat melts, cracks and expands. heatsinks + fans fight back", None),
        ]
        yy = y + 14
        for txt, col in lines:
            R.text(txt, x + 16, yy, col or (150, 158, 172), R.f_small)
            yy += 15

    def draw_mat_editor(self):
        import pygame
        R = self.R
        s = self.screen
        m = MATERIALS["custom"]
        w, h = 420, 150
        x, y = (WIN_W - w) // 2, (WIN_H - h) // 2
        s.fill((12, 13, 17), (x, y, w, h))
        pygame.draw.rect(s, (232, 118, 202), (x, y, w, h), 1)
        R.text("CUSTOM WIRE ALLOY  (C closes, up/down select, left/right tweak)",
               x + 12, y + 10, (232, 118, 202), R.f_small)
        rows = [("resistance (ohm/tile)", f"{m['res']:.4f}"),
                ("ampacity (A)", f"{m['amp']:.2f}"),
                ("thermal k", f"{m['k']:.0f}")]
        for i, (label, val) in enumerate(rows):
            yy = y + 40 + i * 22
            sel = (i == self.mat_field)
            if sel:
                s.fill((40, 30, 44), (x + 10, yy - 3, w - 20, 18))
            R.text(("> " if sel else "  ") + label, x + 14, yy,
                   (255, 230, 250) if sel else (150, 158, 172), R.f_small)
            R.text(val, x + 300, yy, (255, 230, 250) if sel else (150, 158, 172), R.f_small)
        R.text("gold-like = high amp + low res; nichrome-like = hot wire",
               x + 12, y + h - 24, (110, 118, 132), R.f_tiny)

    def run(self, max_frames=None):
        import pygame
        running = True
        while running:
            dt = self.clock.tick(60) / 1000.0
            for e in pygame.event.get():
                if not self.handle(e):
                    running = False
            self.update(min(dt, 0.1))
            self.draw()
            pygame.display.flip()
            self.frames += 1
            if max_frames and self.frames >= max_frames:
                running = False
        pygame.quit()


def run_app(max_frames=None):
    App().run(max_frames)
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    n = None
    if "--frames" in sys.argv:
        try:
            n = int(sys.argv[sys.argv.index("--frames") + 1])
        except (IndexError, ValueError):
            n = 60
    sys.exit(run_app(n))
