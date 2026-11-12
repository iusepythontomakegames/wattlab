# WATT//LAB

A 2D pixel-style electronics sandbox in a single Python file. You lay down tiles on a
grid and it solves the actual circuit behind them every frame: nodal analysis for the
electrics, then conduction / convection / I2R heating for the thermal side. Parts get
hot, melt, crack and explode on their own terms.

```
pip install pygame-ce        # or: pip install pygame
python3 wattlab.py
```

Headless physics check (no pygame needed, 21 assertions):

```
python3 wattlab.py --selftest
```

If it feels slow to appear, this prints how long each startup stage took:

```
python3 wattlab.py --profile
```

## Parts

| Part | Key | Behaviour |
| --- | --- | --- |
| Wire | 1 | Two-way, near-instant. Every tile adds real resistance, so energy degrades into heat over a long run. |
| Power source | 2 | Fixed-voltage supply with a `+` pin (front) and `-` pin (back). Both need wire. `-`/`=` change the voltage. |
| Capacitor | 3 | Stores charge, blocks DC. Its dielectric rating is hidden until it lets go; drift past 85% and it starts whining, past 100% and it detonates. |
| Voltmeter | 4 | High-impedance probe. Hover it for a live reading. |
| One-way bridge | 5 | Hold LMB + LSHIFT and drag to face it one of 4 ways. Forward drop 0.55 V. |
| LED | 6 | 15 light levels between conduction threshold and full brightness. Blows on too much current or reverse voltage. |
| Resistor | 7 | Bottlenecks current to whatever you dial in, and dies past its watt rating. |
| Heatsink | 8 | Big thermal mass, pulls heat out of whatever it touches. |
| Fan | 9 | Bulky and the heaviest load in the box (~80 mA). Must touch a powered node; cools heatsinks in a radius. |
| Via | 0 | Links a tile to the same tile on the layer above. |
| Water | - | Conducts. Puddles short rails, boil off, and dissolve hardware. |
| Eraser | = | Removes tiles. Drag to sweep. |
| CPU | ` | A programmable 8-bit chip with four I/O pins. Hover it and press `E`. |

Tools are numbered along the keyboard's number row, left to right: `1 2 3 4 5 6 7 8 9 0 - =`.

Wire material: `M` cycles copper, silver, gold, aluminium, brass, steel, tungsten,
nichrome, carbon and solder. `C` opens an editor for a custom alloy with its own
resistance, ampacity and thermal conductivity.

## The CPU

`` ` `` drops a chip. Hover it and press `E` for the program editor (`F2` demo, `F3`
clear the buffer, `ESC` assembles and closes). Errors are reported with the line
number in red, and the tooltip shows the program counter, registers and pin states.

Four pins: `0` up, `1` right, `2` down, `3` left. The chip powers itself from any
adjacent wire at 2.5 V or more (about 20 mA), and each pin is a real 40 ohm push
stage that drives 0 to Vcc onto the wire, so loading it with a resistor and LED
makes it sag exactly like a microcontroller pin would. High-Z (`OFF`) is 1 MOhm.

One instruction runs per clock tick; the clock is set per chip with `,` / `.` from
0.5 Hz up to 60 Hz, so you can watch a program walk through the board.

Registers: `A`, `B`, 32 bytes of RAM, flags `Z` and `C`. `HLT` stops the chip until
you re-assemble. Labels are `name:` at the start of a line, comments start with `;`,
operands accept `123` or `0x7B`.

| Group | Ops |
| --- | --- |
| Load / store | `LDA n` `LDB n` `ST n` `LD n` |
| Maths | `ADD n` `SUB n` `INC` `DEC` |
| Logic | `AND n` `ORA n` `XOR n` `SHL` `SHR` `CMP n` |
| Flow | `JMP label` `JZ label` `JNZ label` `JC label` `DLY n` `HLT` `NOP` |
| Pins | `OUT p` (drive pin p with A) `OFF p` (high-Z) `IN p` (analog 0-255) `DIN p` (digital) |

```
; blink whatever is wired to the right pin
loop:
  LDA 255
  OUT 1
  DLY 6
  LDA 0
  OUT 1
  DLY 6
  JMP loop
```

## Rules the sim actually enforces

- Two-terminal parts need wire on both ends; the panel warns when one is floating.
- Heat conducts between neighbouring tiles, convects to ambient, and is carried away
  by heatsinks and fans. Past the melt point a tile becomes slag; below it, thermal
  expansion can crack a wire joint and add 45% resistance.
- Water carries current, so a puddle across two rails is a short that eats components.
- Layers are unlimited; vias let you route in 3D and shrink one layer's footprint.

## Controls

```
1 2 3 4 5 6 7 8 9 0 - = `  pick tool (number row order, ` = CPU)
LMB drag   place                RMB          erase
LMB+SHIFT  aim a part (4 dirs)  R            rotate
M          wire material        C            custom alloy
E          edit the CPU under the cursor
, . / wheel  adjust hovered source / R / C
SPACE      pause                F            single step
[ ]        layer switch         N            new layer
X          clear layer          F1           help overlay
F5 / F9    save / load wattlab_build.scfb
```
