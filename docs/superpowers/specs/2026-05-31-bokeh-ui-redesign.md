# Bokeh Web UI Redesign — Apple-Style Two-Column Layout

**Date:** 2026-05-31  
**Status:** Approved

## Problem

The current `geomag_web_app.py` UI is functional but visually unpolished: dark-themed Bokeh defaults, no visual hierarchy, controls scattered across two flat columns without grouping, and no result summary visible after simulation.

## Design

Apple-inspired clean white UI with card-based control grouping, colored legend, and inline result statistics.

### Layout (top to bottom)

```
┌─ Top bar ────────────────────────────────────┐
│  Geomagnetic Positioning Simulation     v7   │
├─ Main row ───────────────────────────────────┤
│ ┌ Left (240px)   ┐ ┌ Right (240px)  ┐ ┌ Plot (flex) ───────────┐
│ │ Card: 仿真控制  │ │ Card: 数据集   │ │                         │
│ │ · branch       │ │ · own          │ │  ● 真值路线  ● PDR  ● PF│
│ │ · window_size  │ │ · own_profile  │ │                         │
│ │ · max_frames   │ │ · dataset_key  │ │      (plot area)        │
│ ├────────────────┤ │ · data_dir     │ │                         │
│ │ Card: 航向设置  │ ├────────────────┤ ├─────────────────────────┤
│ │ · use_explicit │ │ Card: UJI 设置  │ │ [运行模拟] [下一步]      │
│ │ · heading_deg  │ │ · uji_test_file│ │ 3.99m │ 4.16m │ 6.67m .│
│ │ · mirror_y     │ │                │ │                         │
│ │ · offset       │ │                │ └─────────────────────────┘
│ ├────────────────┤ │                │
│ │ Card: 地图与裁剪│ │                │
│ │ · map_mode     │ │                │
│ │ · trim_head    │ │                │
│ │ · trim_tail    │ │                │
│ └────────────────┘ └────────────────┘
└──────────────────────────────────────────────┘
```

### Color Scheme

| Element | Color | Usage |
|---------|-------|-------|
| Page background | `#f5f5f7` | Overall page |
| Card background | `#ffffff` | All cards |
| Card shadow | `0 1px 3px rgba(0,0,0,0.06)` | Card elevation |
| Primary accent | `#0071e3` (Apple blue) | Buttons, slider values |
| Text primary | `#1d1d1f` | Labels and values |
| Text secondary | `#86868b` | Card group titles |
| Border | `#d2d2d7` | Input borders |
| Separator | `#e8e8ed` | Dividers |
| Route line | `#34c759` (green) | Ground truth |
| PDR line | `#ff9500` (orange) | PDR track |
| PF line | `#007aff` (blue) | PF track |
| Button primary | `#0071e3` | "运行模拟" |
| Button secondary | `#e8e8ed` | "下一步" |

### Typography

- Font: `-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif`
- Title: 16px, weight 600
- Card group labels: 11px, weight 600, uppercase, `#86868b`
- Control labels: 12px, `#1d1d1f`
- Slider values: blue (`#0071e3`)
- Stat values: 14px, weight 600
- Stat labels: 11px, `#86868b`

### Card Grouping

| Card | Controls |
|------|----------|
| 仿真控制 | `branch_select`, `window_size`, `max_frames` |
| 航向设置 | `use_explicit_heading`, `own_initial_heading`, `no_route_initial_heading`, `mirror_y`, `heading_offset` |
| 地图与裁剪 | `own_map_mode`, `trim_head`, `trim_tail` |
| 数据集 | `own_input`, `own_profile`, `own_dataset_key`, `own_data_dir` |
| UJI 设置 | `uji_input`, `uji_test_file`, `uji_data_root` |

### Plot Area

- Plot fills remaining width (`flex: 1`)
- Legend uses colored dots inline above the plot:
  - <span style="color:#34c759">● 真值路线</span>
  - <span style="color:#ff9500">● PDR</span>
  - <span style="color:#007aff">● PF</span>
- Buttons row below plot: "运行模拟" (blue pill) + "下一步" (gray pill)
- Result stats inline to the right of buttons: PF平均, 最终, P95, 步数, PDR平均
- Stats hidden before first run, visible after

### Responsive behavior

- Minimum plot size: 500×400
- Side columns fixed at 240px
- `match_aspect=True` on plot

## Implementation

### Files to modify

**`geomag_web_app.py`** — Only the `GeoMagApp.__init__` layout section (lines ~180-340)

### What changes

1. **Widget grouping**: Replace flat `controls_col1`/`controls_col2` with card-based layout using Bokeh `column(children=[Div(title), widget1, widget2, ...])`
2. **Card headers**: `Div` with inline CSS for uppercase gray headers
3. **Top bar**: `Div` with title text, styled as white card
4. **Legend**: Colored text `Div` above the plot
5. **Result bar**: Div below buttons, hidden initially, updated in `_on_run`
6. **CSS injection**: Bokeh doesn't natively support external CSS, so styles are applied via widget-level `style`/`css_classes` or a global `Div` with embedded `<style>`

### What does NOT change

- All widget definitions (Select, Slider, TextInput, Button, FileInput)
- `_collect_params()`, `_on_run()`, `_on_step()` callbacks
- `run_simulation()` function
- Plot glyph definitions (lines, colors)
- Error handling logic

### Bokeh-specific considerations

- Bokeh uses its own layout engine (`bokeh.layouts`), not HTML/CSS
- Card "shadows" and rounded corners on `Div` are approximated via `style` attribute (inline CSS in a `<style>` block)
- White background is achieved by setting `background` on the top-level `Div` and individual card `Div`s

## Verification

1. `python -m bokeh serve --show geomag_web_app.py` — UI renders correctly
2. Run simulation with route1_run2 — PF mean = 3.99m displayed in stat bar
3. Step through trajectory — plot updates normally
4. Switch branch to UJI — relevant controls shown
5. Error case (route2_run2 with wrong profile) — error message shown in title
6. `python -m pytest tests/` — 68 tests pass
