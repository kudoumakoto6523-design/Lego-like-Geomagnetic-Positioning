# Bokeh Web UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `geomag_web_app.py` UI to Apple-style white two-column layout with card grouping, colored legend, and inline result statistics.

**Architecture:** Single-file layout refactor — replace flat control columns with Bokeh `row()`/`column()` groupings inside styled `Div` cards. Global CSS injected via an embedded `<style>` `Div`. No changes to widget callbacks, `_collect_params()`, `run_simulation()`, or plot glyphs.

**Tech Stack:** Bokeh 3.x (Python), `bokeh.layouts` (row, column), `bokeh.models.Div`

---

### Task 1: Add global CSS stylesheet

**Files:**
- Modify: `geomag_web_app.py:177-180` (start of `GeoMagApp.__init__`)

- [ ] **Step 1: Add `<style>` block as the first layout element**

Replace the plain `self.title_div` with a CSS injection `Div` + a styled title bar `Div`.

Read current `__init__` lines 180-188:
```python
class GeoMagApp:
    def __init__(self) -> None:
        self.title_div = Div(text="<h2>Geomagnetic Positioning Simulation</h2>")
        ...
```

Change to:
```python
class GeoMagApp:
    def __init__(self) -> None:
        # Inject global styles via a hidden <style> Div
        self._style_div = Div(text="""
        <style>
        body { background: #f5f5f7; }
        .bk-root { background: #f5f5f7; }
        .card {
            background: #ffffff;
            border-radius: 10px;
            padding: 12px 14px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            margin-bottom: 10px;
        }
        .card-header {
            font-size: 11px;
            font-weight: 600;
            color: #86868b;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        .top-bar {
            background: #ffffff;
            border-radius: 10px;
            padding: 10px 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            margin-bottom: 12px;
        }
        </style>
        """, width=0, height=0)

        self.title_div = Div(text="""
        <div style="display:flex;align-items:center;justify-content:space-between;">
            <span style="font-size:16px;font-weight:600;color:#1d1d1f;
                         font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
                Geomagnetic Positioning Simulation
            </span>
            <span style="font-size:12px;color:#86868b;">v7 · systematic + EMA</span>
        </div>
        """)
```

- [ ] **Step 2: Verify it runs without error**

```bash
python -c "from geomag_web_app import GeoMagApp; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add geomag_web_app.py
git commit -m "feat(ui): add global CSS and styled top bar"
```

---

### Task 2: Create card helper and restructure left-column widgets

**Files:**
- Modify: `geomag_web_app.py:305-320` (controls_col1 definition)

- [ ] **Step 1: Replace `controls_col1` flat column with three card groups**

Read the current `controls_col1`:
```python
controls_col1 = column(
    self.branch_select,
    self.window_size,
    self.max_frames,
    self.use_explicit_heading,
    self.own_initial_heading,
    self.no_route_initial_heading,
    self.mirror_y,
    self.heading_offset,
    self.trim_head,
    self.trim_tail,
    width=300,
)
```

Replace with:
```python
def _card(header_text, *widgets):
    """Wrap widgets in a card-like column with a styled header."""
    header = Div(text=f'<div class="card-header">{header_text}</div>')
    children = [header] + list(widgets)
    return column(*children, css_classes=["card"], width=240)

controls_col1 = column(
    _card("仿真控制",
        self.branch_select,
        self.window_size,
        self.max_frames,
    ),
    _card("航向设置",
        self.use_explicit_heading,
        self.own_initial_heading,
        self.no_route_initial_heading,
        self.mirror_y,
        self.heading_offset,
    ),
    _card("地图与裁剪",
        self.own_map_mode,
        self.trim_head,
        self.trim_tail,
    ),
    width=240,
)
```

- [ ] **Step 2: Verify imports and widget references still work**

```bash
python -c "from geomag_web_app import GeoMagApp; a = GeoMagApp(); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add geomag_web_app.py
git commit -m "feat(ui): card-group left-column widgets"
```

---

### Task 3: Restructure right-column widgets into cards

**Files:**
- Modify: `geomag_web_app.py:320-340` (controls_col2 definition)

- [ ] **Step 1: Replace `controls_col2` flat column with card groups**

Read current `controls_col2`:
```python
controls_col2 = column(
    self.uji_input,
    self.uji_test_file,
    self.uji_data_root,
    self.own_input,
    self.own_profile,
    self.own_dataset_key,
    self.own_data_dir,
    self.own_map_mode,
    self.own_map_npz,
    self.own_route,
    self.file_input,
    self.run_button,
    self.step_button,
    width=350,
)
```

Replace with:
```python
controls_col2 = column(
    _card("数据集",
        self.own_input,
        self.own_profile,
        self.own_dataset_key,
        self.own_data_dir,
    ),
    _card("UJI 设置",
        self.uji_input,
        self.uji_test_file,
        self.uji_data_root,
    ),
    _card("其他",
        self.own_map_npz,
        self.own_route,
        self.file_input,
    ),
    width=240,
)
```

Note: `self.own_map_mode` moved to left column (地图与裁剪 card). `self.run_button` and `self.step_button` will move to plot area in Task 4.

- [ ] **Step 2: Verify**

```bash
python -c "from geomag_web_app import GeoMagApp; a = GeoMagApp(); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add geomag_web_app.py
git commit -m "feat(ui): card-group right-column widgets"
```

---

### Task 4: Add colored legend and restructure plot area with buttons + stats

**Files:**
- Modify: `geomag_web_app.py:295-304` (plot + legend definition)
- Modify: `geomag_web_app.py:336-340` (main layout assembly)

- [ ] **Step 1: Add colored legend Div above the plot**

Before `self.plot = figure(...)`, insert:
```python
self.legend_div = Div(text="""
<div style="display:flex;align-items:center;gap:14px;
            font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
    <span style="font-size:11px;font-weight:600;color:#86868b;
                 text-transform:uppercase;letter-spacing:0.5px;">
        定位轨迹
    </span>
    <span style="font-size:11px;font-weight:500;color:#34c759;">● 真值路线</span>
    <span style="font-size:11px;font-weight:500;color:#ff9500;">● PDR</span>
    <span style="font-size:11px;font-weight:500;color:#007aff;">● PF</span>
</div>
""")
```

- [ ] **Step 2: Add result stats Div (hidden initially)**

```python
self.stats_div = Div(text="", visible=False)
```

- [ ] **Step 3: Create button bar with inline stats**

```python
self.button_bar = row(
    self.run_button,
    self.step_button,
    Div(text="""
    <div style="width:1px;height:20px;background:#e8e8ed;margin:0 6px;"></div>
    """, width=10),
    self.stats_div,
)
```

- [ ] **Step 4: Assemble plot area as a column**

```python
self.plot_area = column(
    self.legend_div,
    self.plot,
    self.button_bar,
)
```

- [ ] **Step 5: Replace main layout assembly**

Old:
```python
self.layout = column(
    self.title_div,
    row(controls_col1, controls_col2, self.plot),
)
```

New:
```python
self.layout = column(
    self._style_div,
    self.title_div,
    row(controls_col1, controls_col2, self.plot_area),
)
```

- [ ] **Step 6: Verify layout renders**

```bash
python -c "from geomag_web_app import GeoMagApp; a = GeoMagApp(); print('OK')"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add geomag_web_app.py
git commit -m "feat(ui): colored legend, plot-area buttons, stats placeholder"
```

---

### Task 5: Update `_on_run` to populate stats bar

**Files:**
- Modify: `geomag_web_app.py:394-418` (`_on_run` method)

- [ ] **Step 1: After simulation succeeds, update `stats_div` with result metrics**

In `_on_run`, after `self.full_result = result` and before setting `self.step_button.disabled = False`, add:

```python
# Populate stats bar
if result and "pf_error_stats" in result:
    pf = result["pf_error_stats"]
    pdr = result["pdr_error_stats"]
    steps = result.get("steps_detected", 0)
    self.stats_div.text = f"""
    <div style="display:flex;align-items:center;gap:14px;
                font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:11px;">
        <span><span style="color:#86868b;">PF 平均</span>
              <span style="font-weight:600;color:#007aff;font-size:14px;">
              {pf['mean']:.2f}m</span></span>
        <span><span style="color:#86868b;">最终</span>
              <span style="font-weight:600;font-size:14px;">
              {pf['final']:.2f}m</span></span>
        <span><span style="color:#86868b;">P95</span>
              <span style="font-weight:600;font-size:14px;">
              {pf['p95']:.2f}m</span></span>
        <span><span style="color:#86868b;">步数</span>
              <span style="font-weight:600;font-size:14px;">{steps}</span></span>
        <span><span style="color:#86868b;">PDR</span>
              <span style="font-weight:600;font-size:14px;">
              {pdr['mean']:.2f}m</span></span>
    </div>
    """
    self.stats_div.visible = True
else:
    self.stats_div.visible = False
```

Also reset `self.stats_div.visible = False` at the start of `_on_run`.

- [ ] **Step 2: Also show stats on error**

In the error branch (after setting `self.title_div.text`), add:
```python
self.stats_div.visible = False
```

- [ ] **Step 3: Visual verification**

```bash
bokeh serve --show geomag_web_app.py
```
Run a simulation. Check that the stats bar appears below the plot with correct numbers.

- [ ] **Step 4: Commit**

```bash
git add geomag_web_app.py
git commit -m "feat(ui): populate result stats bar after simulation"
```

---

### Task 6: Run full test suite and final polish

- [ ] **Step 1: Run ruff lint**

```bash
ruff check geomag_web_app.py
```
Expected: All checks passed

- [ ] **Step 2: Run pytest**

```bash
python -m pytest tests/ -q
```
Expected: 68 passed

- [ ] **Step 3: Launch and visually verify the complete UI**

```bash
bokeh serve --show geomag_web_app.py
```
Checklist:
- [ ] White background, card grouping visible
- [ ] All widgets present and functional
- [ ] Colored legend above plot
- [ ] Buttons below plot with stats bar
- [ ] Run route1_run2 → PF mean ~3.99m displayed
- [ ] Switch branch dropdown works
- [ ] Error message shows in top bar on bad input

- [ ] **Step 4: Commit**

```bash
git add geomag_web_app.py
git commit -m "chore(ui): final polish and lint fixes"
```
