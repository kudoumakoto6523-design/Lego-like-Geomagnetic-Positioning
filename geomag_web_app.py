"""Polished Bokeh front-end for the geomagnetic positioning experiments.

Run with::

    bokeh serve --show geomag_web_app.py

The page intentionally stays inside the existing Bokeh stack.  It presents
the real PF/PDR/route outputs returned by ``Geomag.branching`` and never hides
simulation failures behind synthetic data.
"""

from __future__ import annotations

import base64
import html
import json
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable

import numpy as np

from bokeh.io import curdoc
from bokeh.layouts import column, row
from bokeh.models import (
    Button,
    CheckboxButtonGroup,
    ColumnDataSource,
    CustomJS,
    Div,
    FileInput,
    HoverTool,
    InlineStyleSheet,
    PasswordInput,
    RadioButtonGroup,
    Select,
    Slider,
    TabPanel,
    Tabs,
    TextInput,
)
from bokeh.plotting import figure

from Geomag.branching import (
    BranchConfig,
    parse_route_control_points,
    resolve_outdoor_selection,
    resolve_own_selection,
    resolve_uji_selection,
    run_branch_simulation,
)
from Geomag.outdoor_navigation import available_outdoor_navigation_keys
from Geomag.own_dataset_registry import available_own_dataset_keys
from Geomag.pipeline import GeomagPipeline


PROJECT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = PROJECT_DIR / "data" / "uploads"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
UI_FONT_CSS = '"SimHei", "黑体", "Microsoft YaHei", sans-serif'
PLOT_FONT = "SimHei"

PALETTE = {
    "page": "#07111f",
    "panel": "#0d1b2a",
    "panel_alt": "#102235",
    "border": "#213b54",
    "text": "#e8f0f7",
    "muted": "#8ca2b8",
    "accent": "#3ad6c7",
    "accent_dark": "#163e44",
    "blue": "#62a8ff",
    "orange": "#ffb35c",
    "route": "#9eabc0",
    "danger": "#ff6f7d",
    "success": "#57d38c",
}

CARD_STYLE = {
    "background": PALETTE["panel"],
    "border": f"1px solid {PALETTE['border']}",
    "border-radius": "16px",
    "padding": "16px",
    "box-shadow": "0 14px 36px rgba(0, 0, 0, 0.18)",
}

WIDGET_STYLESHEET = InlineStyleSheet(
    css=f"""
    :host {{
      color: {PALETTE['text']};
      font-family: {UI_FONT_CSS};
    }}
    label, .bk-input-group label {{ color: {PALETTE['muted']} !important; font-weight: 600; }}
    .bk-input, select, input {{
      color: {PALETTE['text']} !important;
      background: #091725 !important;
      border: 1px solid {PALETTE['border']} !important;
      border-radius: 10px !important;
      min-height: 38px;
    }}
    .bk-input:focus, select:focus, input:focus {{
      border-color: {PALETTE['accent']} !important;
      box-shadow: 0 0 0 3px rgba(58, 214, 199, 0.12) !important;
    }}
    .bk-btn {{
      border-radius: 10px !important;
      min-height: 38px;
      font-weight: 700;
      border-width: 1px !important;
    }}
    .bk-btn-success {{ background: {PALETTE['accent']} !important; border-color: {PALETTE['accent']} !important; color: #042522 !important; }}
    .bk-btn-primary {{ background: {PALETTE['blue']} !important; border-color: {PALETTE['blue']} !important; color: #07111f !important; }}
    .bk-btn-default {{ background: #12253a !important; border-color: {PALETTE['border']} !important; color: {PALETTE['text']} !important; }}
    """
)

APP_TEMPLATE = f"""
{{% block preamble %}}
<style>
  :root {{ color-scheme: dark; }}
  html, body {{
    margin: 0;
    min-height: 100%;
    background: {PALETTE['page']};
    color: {PALETTE['text']};
    font-family: {UI_FONT_CSS};
  }}
  body {{
    background-image:
      radial-gradient(circle at 8% 0%, rgba(58, 214, 199, .09), transparent 26rem),
      radial-gradient(circle at 92% 8%, rgba(98, 168, 255, .08), transparent 28rem);
  }}
  .bk-root {{ width: 100%; }}
  .gm-shell {{ max-width: 1560px; margin: 0 auto; }}
  .gm-card {{ overflow: hidden; }}
</style>
{{% endblock %}}
"""


def _style_widget(widget):
    widget.stylesheets = [WIDGET_STYLESHEET]
    return widget


def _section_title(title: str, subtitle: str = "") -> Div:
    detail = f'<span class="gm-section-subtitle">{html.escape(subtitle)}</span>' if subtitle else ""
    return Div(
        text=f"""
        <div class="gm-section-heading">
          <strong>{html.escape(title)}</strong>{detail}
        </div>
        """,
        stylesheets=[InlineStyleSheet(css=f"""
          :host {{ color: {PALETTE['text']}; }}
          .gm-section-heading {{ display:flex; align-items:baseline; gap:10px; margin-bottom:4px; }}
          strong {{ font-size:15px; letter-spacing:.02em; }}
          .gm-section-subtitle {{ color:{PALETTE['muted']}; font-size:12px; }}
        """)],
        sizing_mode="stretch_width",
    )


def _card(*children, width: int | None = None, sizing_mode: str = "stretch_width"):
    return column(
        *children,
        width=width,
        sizing_mode=sizing_mode,
        styles=CARD_STYLE,
        css_classes=["gm-card"],
        margin=0,
    )


def _as_track(value: Any) -> np.ndarray:
    if value is None:
        return np.empty((0, 2), dtype=float)
    arr = np.asarray(value, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 2:
        return np.empty((0, 2), dtype=float)
    return np.asarray(arr[:, :2], dtype=float)


def _as_series(value: Any) -> np.ndarray:
    if value is None:
        return np.empty(0, dtype=float)
    return np.asarray(value, dtype=float).reshape(-1)


def _json_default(value: Any):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _uji_route_to_xy(route: np.ndarray) -> np.ndarray:
    """Use the same route conversion as ``main.py`` / ``GeomagPipeline``."""
    if route.size == 0:
        return route

    model_path = PROJECT_DIR / "data" / "processed" / "uji_mag_model_kriging.npz"
    geomag_map = {
        "source": "uji",
        "output_model_npz": str(model_path) if model_path.exists() else None,
    }
    x, y = GeomagPipeline._route_to_xy_for_error(route, geomag_map)
    if x is None or y is None:
        raise ValueError("无法按主程序坐标系转换 UJI 参考路线。")
    return np.column_stack([x, y])


@dataclass
class SimulationViewResult:
    raw: dict[str, Any]
    branch: str
    dataset: str
    pf_track: np.ndarray
    pdr_track: np.ndarray
    route_track: np.ndarray
    pf_error: np.ndarray
    pdr_error: np.ndarray
    particle_counts: np.ndarray
    frames_used: int | None
    steps_detected: int
    pf_stats: dict[str, float] | None
    pdr_stats: dict[str, float] | None


def run_simulation(
    params: dict[str, Any],
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> SimulationViewResult:
    """Run the selected real branch and normalize its result for the UI."""
    branch = params["branch"]
    uji_defaults = resolve_uji_selection(params.get("uji"))
    own_defaults = resolve_own_selection(params.get("own"))
    outdoor_defaults = resolve_outdoor_selection(params.get("outdoor"))

    cfg = BranchConfig(
        branch=branch,
        window_size=int(params["window_size"]),
        max_frames=params.get("max_frames"),
        show=False,
        output_json=str(PROJECT_DIR / "results" / f"web_{branch}_last.json"),
        output_png=str(PROJECT_DIR / "results" / f"web_{branch}_last.png"),
        progress_callback=progress_callback,
        uji_test_file=params.get("uji_test_file") or uji_defaults["uji_test_file"],
        uji_data_root=params.get("uji_data_root") or uji_defaults["uji_data_root"],
        own_profile=params.get("own_profile") or own_defaults["own_profile"],
        own_dataset_key=params.get("own_dataset_key") or own_defaults["own_dataset_key"],
        own_data_dir=params.get("own_data_dir") or own_defaults["own_data_dir"],
        own_map_mode=params.get("own_map_mode") or "raw",
        own_map_npz_path=params.get("own_map_npz_path"),
        own_route_xy_m=(
            parse_route_control_points(params["own_route"])
            if params.get("own_route")
            else None
        ),
        own_initial_heading_deg=params.get("own_initial_heading_deg"),
        own_use_route_initial_heading=bool(params.get("own_use_route_initial_heading", True)),
        own_mirror_y=bool(params.get("mirror_y", False)),
        own_heading_offset_deg=float(params.get("own_heading_offset_deg", -90.0)),
        own_trim_head=int(params.get("own_trim_head", 0)),
        own_trim_tail=int(params.get("own_trim_tail", 0)),
        outdoor_navigation_key=(
            params.get("outdoor_navigation_key")
            or outdoor_defaults["outdoor_navigation_key"]
        ),
        outdoor_data_root=(
            params.get("outdoor_data_root") or outdoor_defaults["outdoor_data_root"]
        ),
        outdoor_auto_tune=bool(params.get("outdoor_auto_tune", False)),
        outdoor_tune_iterations=int(params.get("outdoor_tune_iterations", 3)),
        outdoor_tune_dry_run=bool(params.get("outdoor_tune_dry_run", False)),
        outdoor_tune_config_path=str(
            params.get("outdoor_tune_config_path")
            or "config/deepseek_auto_tuning.json"
        ),
        outdoor_deepseek_api_key=params.get("deepseek_api_key"),
    )

    raw = run_branch_simulation(cfg)
    if not isinstance(raw, dict):
        raise TypeError(f"Simulation returned {type(raw).__name__}, expected a result dictionary.")

    if branch == "own":
        pf_track = _as_track(raw.get("pf_track"))
        pdr_track = _as_track(raw.get("pdr_track"))
        route_track = _as_track(raw.get("route_xy_m"))
        dataset = str(raw.get("dataset_key") or params.get("own") or "own")
    elif branch == "outdoor":
        pf_track = _as_track(raw.get("pos_list"))
        pdr_track = _as_track(raw.get("pdr_list"))
        route_track = _as_track(raw.get("route_xy_m"))
        dataset = str(raw.get("outdoor_navigation_key") or params.get("outdoor") or "outdoor")
    else:
        pf_track = _as_track(raw.get("pos_list"))
        pdr_track = _as_track(raw.get("pdr_list"))
        route_track = _uji_route_to_xy(_as_track(raw.get("route")))
        dataset = str(raw.get("uji_test_file") or params.get("uji") or "UJI")

    if pf_track.size == 0 and pdr_track.size == 0:
        raise ValueError("Simulation completed but returned no PF or PDR trajectory points.")

    frames_used = raw.get("sensor_frames_used")
    if frames_used is None:
        frames_used = params.get("max_frames")

    steps_detected = int(raw.get("steps_detected", max(0, len(pf_track) - 1)))
    return SimulationViewResult(
        raw=raw,
        branch=branch,
        dataset=dataset,
        pf_track=pf_track,
        pdr_track=pdr_track,
        route_track=route_track,
        pf_error=_as_series(raw.get("pf_error_series")),
        pdr_error=_as_series(raw.get("pdr_error_series")),
        particle_counts=_as_series(raw.get("particle_counts")),
        frames_used=None if frames_used is None else int(frames_used),
        steps_detected=steps_detected,
        pf_stats=raw.get("pf_error_stats"),
        pdr_stats=raw.get("pdr_error_stats"),
    )


class GeoMagApp:
    """Stateful Bokeh application with branch-aware controls and playback."""

    def __init__(self) -> None:
        self.doc = curdoc()
        self.result: SimulationViewResult | None = None
        self.playback_callback = None
        self.running = False
        self._applying_preset = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="geomag-web")
        self._run_future: Future | None = None
        self._progress_percent = -1

        self._build_header()
        self._build_controls()
        self._build_charts()
        self._build_result_area()
        self._wire_callbacks()
        self._apply_branch_state()
        self._apply_heading_state()
        self._reset_result_view()
        self.doc.on_session_destroyed(self._on_session_destroyed)

        sidebar = column(
            self.control_card,
            self.status_card,
            width=370,
            sizing_mode="stretch_height",
            margin=0,
        )
        content = column(
            self.metrics_div,
            self.chart_tabs,
            self.result_summary,
            sizing_mode="stretch_width",
            margin=0,
        )
        body = row(
            sidebar,
            content,
            sizing_mode="stretch_width",
            styles={"gap": "16px", "align-items": "flex-start"},
            margin=0,
        )
        self.layout = column(
            self.header,
            body,
            sizing_mode="stretch_width",
            styles={"gap": "16px", "padding": "18px 22px 28px 22px"},
            css_classes=["gm-shell"],
            margin=0,
        )

    def _build_header(self) -> None:
        self.header = Div(
            text=f"""
            <div class="gm-header">
              <div class="gm-mark">GM</div>
              <div class="gm-title-block">
                <div class="gm-kicker">LEGO-LIKE RESEARCH CONSOLE</div>
                <h1>地磁定位实验台</h1>
                <p>组合参数、运行 PF / PDR 仿真，并在同一工作区检查轨迹与误差。</p>
              </div>
              <div class="gm-env"><span></span> Python 环境已连接</div>
            </div>
            """,
            stylesheets=[InlineStyleSheet(css=f"""
              :host {{ color:{PALETTE['text']}; }}
              .gm-header {{ display:flex; align-items:center; gap:16px; min-height:88px; padding:4px 4px 2px; }}
              .gm-mark {{ display:grid; place-items:center; width:52px; height:52px; flex:0 0 52px; border-radius:15px;
                background:{PALETTE['accent']}; color:#042522; font-weight:900; letter-spacing:.08em; box-shadow:0 10px 30px rgba(58,214,199,.2); }}
              .gm-title-block {{ flex:1; }}
              .gm-kicker {{ color:{PALETTE['accent']}; font-size:11px; font-weight:800; letter-spacing:.16em; margin-bottom:4px; }}
              h1 {{ font-size:27px; line-height:1.12; margin:0; letter-spacing:-.02em; }}
              p {{ color:{PALETTE['muted']}; margin:7px 0 0; font-size:13px; }}
              .gm-env {{ color:{PALETTE['muted']}; font-size:12px; border:1px solid {PALETTE['border']}; background:{PALETTE['panel']};
                padding:9px 12px; border-radius:999px; white-space:nowrap; }}
              .gm-env span {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:{PALETTE['success']};
                margin-right:7px; box-shadow:0 0 0 4px rgba(87,211,140,.12); }}
              @media (max-width: 800px) {{ .gm-env {{ display:none; }} h1 {{ font-size:23px; }} }}
            """)],
            sizing_mode="stretch_width",
        )

    def _build_controls(self) -> None:
        self.branch_toggle = _style_widget(RadioButtonGroup(
            labels=["自有室内数据", "UJI 基准数据", "室外 RTK 数据"],
            active=0,
            sizing_mode="stretch_width",
        ))

        own_options = []
        friendly = {
            "route1_run1": "路线 1 · 采集 1",
            "route1_run2": "路线 1 · 采集 2",
            "route2_run1": "路线 2 · 采集 1",
        }
        for key in available_own_dataset_keys():
            own_options.append((key, friendly.get(key, key)))
        own_options.append(("own_branch", "原始室内采集目录"))

        self.own_input = _style_widget(Select(
            title="室内数据集", value="route1_run2", options=own_options, sizing_mode="stretch_width"
        ))
        self.uji_input = _style_widget(Select(
            title="UJI 测试序列",
            value="tt02",
            options=[(f"tt{i:02d}", f"tt{i:02d} · 测试序列 {i}") for i in range(1, 12)],
            sizing_mode="stretch_width",
        ))
        self.outdoor_input = _style_widget(Select(
            title="室外测试序列",
            value="nav1",
            options=[(key, key.upper()) for key in available_outdoor_navigation_keys()],
            sizing_mode="stretch_width",
        ))
        self.own_group = column(self.own_input, sizing_mode="stretch_width")
        self.uji_group = column(self.uji_input, sizing_mode="stretch_width")
        self.outdoor_group = column(self.outdoor_input, sizing_mode="stretch_width")

        self.preset_select = _style_widget(Select(
            title="运行预设",
            value="full",
            options=[
                ("full", "Main 默认 · 完整序列"),
                ("quick", "快速检查 · 80 帧"),
                ("balanced", "平衡分析 · 600 帧"),
                ("custom", "自定义参数"),
            ],
            sizing_mode="stretch_width",
        ))
        self.window_size = _style_widget(Slider(
            title="地磁历史窗口", start=20, end=800, step=20, value=400, sizing_mode="stretch_width"
        ))
        self.max_frames = _style_widget(Slider(
            title="最大帧数 · 0 表示完整序列", start=0, end=2000, step=20, value=0, sizing_mode="stretch_width"
        ))

        self.heading_mode = _style_widget(Select(
            title="初始航向策略",
            value="route",
            options=[
                ("route", "按参考路线自动推断"),
                ("manual", "手动指定角度"),
                ("gyro", "仅使用陀螺仪"),
            ],
            sizing_mode="stretch_width",
        ))
        self.own_initial_heading = _style_widget(Slider(
            title="手动初始航向 · 0°=+X / 90°=+Y",
            start=-180,
            end=180,
            step=5,
            value=0,
            sizing_mode="stretch_width",
        ))
        self.heading_offset = _style_widget(Slider(
            title="航向偏移校正 (°)", start=-180, end=180, step=5, value=-90, sizing_mode="stretch_width"
        ))
        self.mirror_y = _style_widget(Select(
            title="Y 轴镜像修正", value="false", options=[("false", "关闭"), ("true", "启用")], sizing_mode="stretch_width"
        ))
        self.trim_head = _style_widget(Slider(
            title="丢弃开头帧", start=0, end=300, step=1, value=0, sizing_mode="stretch_width"
        ))
        self.trim_tail = _style_widget(Slider(
            title="丢弃末尾帧", start=0, end=300, step=1, value=0, sizing_mode="stretch_width"
        ))
        self.own_map_mode = _style_widget(Select(
            title="室内地图模式", value="raw", options=[("raw", "原始地图"), ("tile12", "12 列重采样")], sizing_mode="stretch_width"
        ))
        self.own_map_npz = _style_widget(TextInput(
            title="自定义磁图 NPZ 路径 · 可留空", value="", sizing_mode="stretch_width"
        ))
        self.own_route = _style_widget(TextInput(
            title="自定义路线 · x1,y1; x2,y2", value="", sizing_mode="stretch_width"
        ))
        self.own_profile = _style_widget(Select(
            title="数据配置", value="package", options=[("package", "标准数据包"), ("own_branch", "原始采集目录")], sizing_mode="stretch_width"
        ))
        self.own_dataset_key = _style_widget(TextInput(
            title="数据集键", value="route1_run2", sizing_mode="stretch_width"
        ))
        self.own_data_dir = _style_widget(TextInput(
            title="室内数据目录", value="", sizing_mode="stretch_width"
        ))
        self.uji_test_file = _style_widget(TextInput(
            title="UJI 测试文件覆盖", value="tt02.txt", sizing_mode="stretch_width"
        ))
        self.uji_data_root = _style_widget(TextInput(
            title="UJI 数据根目录", value="data/raw", sizing_mode="stretch_width"
        ))
        self.outdoor_data_root = _style_widget(TextInput(
            title="室外数据根目录",
            value="data/raw/outdoor_rtk_map",
            sizing_mode="stretch_width",
        ))
        self.outdoor_auto_tune = _style_widget(Select(
            title="DeepSeek 自动调参",
            value="false",
            options=[("false", "关闭 · 单次定位"), ("true", "启用 · 多轮优化")],
            sizing_mode="stretch_width",
        ))
        self.outdoor_tune_iterations = _style_widget(Slider(
            title="DeepSeek 建议轮数",
            start=1,
            end=10,
            step=1,
            value=3,
            sizing_mode="stretch_width",
        ))
        self.outdoor_tune_dry_run = _style_widget(Select(
            title="调参运行模式",
            value="false",
            options=[
                ("false", "正式运行 · 调用 DeepSeek"),
                ("true", "Dry Run · 只生成提示词"),
            ],
            sizing_mode="stretch_width",
        ))
        self.deepseek_api_key = _style_widget(PasswordInput(
            title="DeepSeek API Key · 可留空读取环境变量",
            value="",
            placeholder="sk-…（仅在本次服务会话中使用）",
            sizing_mode="stretch_width",
        ))
        self.outdoor_tuning_hint = Div(sizing_mode="stretch_width")
        self.outdoor_group.children = [
            self.outdoor_input,
            _section_title("DeepSeek 自动调参", "优先优化 PDR · PF 作为第二阶段"),
            self.outdoor_auto_tune,
            self.outdoor_tune_iterations,
            self.outdoor_tune_dry_run,
            self.deepseek_api_key,
            self.outdoor_tuning_hint,
        ]

        self.file_input = _style_widget(FileInput(
            accept=".npz,.txt", multiple=False, sizing_mode="stretch_width"
        ))
        self.file_hint = Div(
            text=f'<span style="color:{PALETTE["muted"]};font-size:12px">上传 NPZ 磁图或 UJI TXT 测试文件，最大 12 MB。</span>',
            sizing_mode="stretch_width",
        )

        self.own_advanced = column(
            self.heading_mode,
            self.own_initial_heading,
            self.heading_offset,
            self.mirror_y,
            self.trim_head,
            self.trim_tail,
            self.own_map_mode,
            self.own_map_npz,
            self.own_route,
            self.own_profile,
            self.own_dataset_key,
            self.own_data_dir,
            sizing_mode="stretch_width",
        )
        self.uji_advanced = column(
            self.uji_test_file,
            self.uji_data_root,
            sizing_mode="stretch_width",
        )
        self.outdoor_advanced = column(
            self.outdoor_data_root,
            sizing_mode="stretch_width",
        )

        basic_panel = TabPanel(
            title="常用参数",
            child=column(
                _section_title("数据源", "切换后参数会自动联动"),
                self.branch_toggle,
                self.own_group,
                self.uji_group,
                self.outdoor_group,
                _section_title("计算规模", "可先用快速预设验证流程"),
                self.preset_select,
                self.window_size,
                self.max_frames,
                sizing_mode="stretch_width",
            ),
        )
        advanced_panel = TabPanel(
            title="高级参数",
            child=column(
                self.own_advanced,
                self.uji_advanced,
                self.outdoor_advanced,
                _section_title("上传数据", "上传后自动填入对应路径"),
                self.file_input,
                self.file_hint,
                sizing_mode="stretch_width",
            ),
        )
        self.control_tabs = Tabs(
            tabs=[basic_panel, advanced_panel], sizing_mode="stretch_width"
        )

        self.run_button = _style_widget(Button(
            label="运行定位仿真", button_type="success", sizing_mode="stretch_width", height=44
        ))
        self.reset_button = _style_widget(Button(
            label="恢复默认", button_type="default", sizing_mode="stretch_width", height=40
        ))
        self.control_card = _card(
            _section_title("实验配置", "所有参数均映射到 BranchConfig"),
            self.control_tabs,
            row(self.reset_button, self.run_button, sizing_mode="stretch_width"),
            sizing_mode="stretch_width",
        )

        self.context_div = Div(sizing_mode="stretch_width")
        self.status_div = Div(sizing_mode="stretch_width")
        self.progress_div = Div(sizing_mode="stretch_width")
        self.status_card = _card(
            _section_title("运行状态"),
            self.status_div,
            self.progress_div,
            self.context_div,
            sizing_mode="stretch_width",
        )

    def _build_charts(self) -> None:
        self.pf_source = ColumnDataSource(data=dict(x=[], y=[], step=[], error=[]))
        self.pdr_source = ColumnDataSource(data=dict(x=[], y=[], step=[], error=[]))
        self.route_source = ColumnDataSource(data=dict(x=[], y=[]))
        self.current_source = ColumnDataSource(data=dict(x=[], y=[]))
        self.error_source = ColumnDataSource(data=dict(step=[], pf=[], pdr=[]))
        self.particle_source = ColumnDataSource(data=dict(step=[], count=[]))
        self.export_source = ColumnDataSource(data=dict(json=["{}"]))

        self.trajectory_plot = figure(
            title="等待运行",
            x_axis_label="X / m",
            y_axis_label="Y / m",
            height=590,
            sizing_mode="stretch_width",
            tools="pan,wheel_zoom,box_zoom,reset,save",
            active_scroll="wheel_zoom",
        )
        self._style_plot(self.trajectory_plot)
        self.route_renderer = self.trajectory_plot.line(
            "x", "y", source=self.route_source, line_width=2.2, color=PALETTE["route"],
            line_dash="dashed", alpha=0.8, legend_label="参考路线"
        )
        self.pdr_renderer = self.trajectory_plot.line(
            "x", "y", source=self.pdr_source, line_width=2.2, color=PALETTE["orange"],
            alpha=0.9, legend_label="PDR"
        )
        self.pf_renderer = self.trajectory_plot.line(
            "x", "y", source=self.pf_source, line_width=3.2, color=PALETTE["accent"],
            legend_label="PF 融合定位"
        )
        self.trajectory_plot.scatter(
            "x", "y", source=self.current_source, size=11, color=PALETTE["accent"],
            line_color=PALETTE["text"], line_width=1.2
        )
        self.trajectory_plot.add_tools(HoverTool(
            renderers=[self.pf_renderer],
            tooltips=[("步骤", "@step"), ("X", "@x{0.000} m"), ("Y", "@y{0.000} m"), ("误差", "@error{0.000} m")],
        ))

        self.layer_toggle = _style_widget(CheckboxButtonGroup(
            labels=["PF 融合", "PDR", "参考路线"], active=[0, 1, 2], sizing_mode="stretch_width"
        ))
        self.step_slider = _style_widget(Slider(
            title="轨迹回放进度", start=1, end=2, step=1, value=1, disabled=True, sizing_mode="stretch_width"
        ))
        self.rewind_button = _style_widget(Button(label="回到起点", button_type="default", width=104))
        self.play_button = _style_widget(Button(label="播放", button_type="primary", width=92, disabled=True))
        self.next_button = _style_widget(Button(label="下一步", button_type="default", width=92, disabled=True))
        self.export_button = _style_widget(Button(label="导出 JSON", button_type="default", width=110, disabled=True))
        self.export_button.js_on_click(CustomJS(args=dict(source=self.export_source), code="""
          const payload = source.data.json[0] || '{}';
          const blob = new Blob([payload], {type: 'application/json;charset=utf-8'});
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = 'geomagnetic-positioning-result.json';
          document.body.appendChild(link);
          link.click();
          link.remove();
          URL.revokeObjectURL(url);
        """))

        playback_bar = column(
            self.layer_toggle,
            self.step_slider,
            row(
                self.rewind_button,
                self.play_button,
                self.next_button,
                self.export_button,
                sizing_mode="stretch_width",
                styles={"gap": "8px"},
            ),
            sizing_mode="stretch_width",
        )
        trajectory_panel = TabPanel(
            title="定位轨迹",
            child=column(self.trajectory_plot, playback_bar, sizing_mode="stretch_width"),
        )

        self.error_plot = figure(
            title="定位误差随步骤变化",
            x_axis_label="步骤",
            y_axis_label="误差 / m",
            height=335,
            sizing_mode="stretch_width",
            tools="pan,wheel_zoom,box_zoom,reset,save",
            active_scroll="wheel_zoom",
        )
        self._style_plot(self.error_plot)
        error_pf = self.error_plot.line(
            "step", "pf", source=self.error_source, color=PALETTE["accent"], line_width=2.8, legend_label="PF 误差"
        )
        self.error_plot.line(
            "step", "pdr", source=self.error_source, color=PALETTE["orange"], line_width=2.2, legend_label="PDR 误差"
        )
        self.error_plot.add_tools(HoverTool(
            renderers=[error_pf], tooltips=[("步骤", "@step"), ("PF 误差", "@pf{0.000} m")]
        ))

        self.particle_plot = figure(
            title="粒子数量",
            x_axis_label="步骤",
            y_axis_label="粒子数",
            height=255,
            sizing_mode="stretch_width",
            tools="pan,wheel_zoom,box_zoom,reset,save",
            active_scroll="wheel_zoom",
        )
        self._style_plot(self.particle_plot)
        self.particle_plot.line(
            "step", "count", source=self.particle_source, color=PALETTE["blue"], line_width=2.5
        )
        diagnostics_panel = TabPanel(
            title="误差与粒子",
            child=column(self.error_plot, self.particle_plot, sizing_mode="stretch_width"),
        )
        self.chart_tabs = Tabs(
            tabs=[trajectory_panel, diagnostics_panel],
            sizing_mode="stretch_width",
            styles={**CARD_STYLE, "padding": "12px"},
            css_classes=["gm-card"],
        )
        self.trajectory_plot.legend.label_text_font = PLOT_FONT
        self.error_plot.legend.label_text_font = PLOT_FONT

    def _build_result_area(self) -> None:
        self.metrics_div = Div(sizing_mode="stretch_width")
        self.result_summary = Div(
            sizing_mode="stretch_width",
            styles={**CARD_STYLE, "padding": "14px 18px"},
            css_classes=["gm-card"],
        )

    def _style_plot(self, plot) -> None:
        plot.background_fill_color = "#091725"
        plot.border_fill_color = PALETTE["panel"]
        plot.outline_line_color = PALETTE["border"]
        plot.outline_line_alpha = 0.8
        plot.grid.grid_line_color = PALETTE["border"]
        plot.grid.grid_line_alpha = 0.38
        plot.axis.axis_line_color = PALETTE["border"]
        plot.axis.major_tick_line_color = PALETTE["muted"]
        plot.axis.minor_tick_line_color = None
        plot.axis.major_label_text_color = PALETTE["muted"]
        plot.axis.axis_label_text_color = PALETTE["muted"]
        plot.axis.major_label_text_font = PLOT_FONT
        plot.axis.axis_label_text_font = PLOT_FONT
        plot.title.text_color = PALETTE["text"]
        plot.title.text_font = PLOT_FONT
        plot.title.text_font_size = "15px"
        plot.toolbar.logo = None

    def _wire_callbacks(self) -> None:
        self.branch_toggle.on_change("active", lambda attr, old, new: self._apply_branch_state())
        self.own_input.on_change("value", lambda attr, old, new: self._sync_own_selection())
        self.uji_input.on_change("value", lambda attr, old, new: self._sync_uji_selection())
        self.outdoor_input.on_change("value", lambda attr, old, new: self._sync_outdoor_selection())
        self.outdoor_auto_tune.on_change("value", lambda attr, old, new: self._apply_outdoor_tuning_state())
        self.outdoor_tune_iterations.on_change("value", lambda attr, old, new: self._update_context())
        self.outdoor_tune_dry_run.on_change("value", lambda attr, old, new: self._apply_outdoor_tuning_state())
        self.heading_mode.on_change("value", lambda attr, old, new: self._apply_heading_state())
        self.preset_select.on_change("value", self._on_preset_change)
        self.window_size.on_change("value", self._mark_custom_preset)
        self.max_frames.on_change("value", self._mark_custom_preset)
        self.file_input.on_change("value", self._on_file_upload)
        self.run_button.on_click(self._on_run)
        self.reset_button.on_click(self._on_reset)
        self.layer_toggle.on_change("active", self._on_layer_toggle)
        self.step_slider.on_change("value", self._on_step_slider)
        self.rewind_button.on_click(self._on_rewind)
        self.play_button.on_click(self._on_play)
        self.next_button.on_click(self._on_next)

    @property
    def branch(self) -> str:
        return ("own", "uji", "outdoor")[self.branch_toggle.active]

    def _apply_branch_state(self) -> None:
        is_own = self.branch == "own"
        is_uji = self.branch == "uji"
        is_outdoor = self.branch == "outdoor"
        self.own_group.visible = is_own
        self.own_advanced.visible = is_own
        self.uji_group.visible = is_uji
        self.uji_advanced.visible = is_uji
        self.outdoor_group.visible = is_outdoor
        self.outdoor_advanced.visible = is_outdoor
        self.file_input.accept = ".npz" if is_own else ".txt" if is_uji else ""
        self.file_input.disabled = is_outdoor
        if is_own:
            self._sync_own_selection()
        elif is_uji:
            self._sync_uji_selection()
        else:
            self._sync_outdoor_selection()
        self._apply_outdoor_tuning_state()
        self._update_context()

    def _sync_own_selection(self) -> None:
        defaults = resolve_own_selection(self.own_input.value)
        self.own_profile.value = defaults["own_profile"]
        self.own_dataset_key.value = defaults["own_dataset_key"]
        self.own_data_dir.value = str(defaults["own_data_dir"])
        self._update_context()

    def _sync_uji_selection(self) -> None:
        defaults = resolve_uji_selection(self.uji_input.value)
        self.uji_test_file.value = defaults["uji_test_file"]
        self.uji_data_root.value = defaults["uji_data_root"]
        self._update_context()

    def _sync_outdoor_selection(self) -> None:
        defaults = resolve_outdoor_selection(self.outdoor_input.value)
        self.outdoor_data_root.value = defaults["outdoor_data_root"]
        self._update_context()

    def _apply_heading_state(self) -> None:
        self.own_initial_heading.disabled = self.heading_mode.value != "manual"

    def _apply_outdoor_tuning_state(self) -> None:
        enabled = self.outdoor_auto_tune.value == "true"
        dry_run = self.outdoor_tune_dry_run.value == "true"
        self.outdoor_tune_iterations.disabled = not enabled or dry_run
        self.outdoor_tune_dry_run.disabled = not enabled
        self.deepseek_api_key.disabled = not enabled or dry_run
        if not enabled:
            detail = "关闭时只运行一次室外 PF / PDR 定位。"
        elif dry_run:
            detail = "不会调用 API；运行一次后保存 DeepSeek 提示词预览。"
        else:
            detail = "模型 deepseek-v4-flash；密码框内容不会写入配置、报告或导出结果。"
        self.outdoor_tuning_hint.text = (
            f'<div style="color:{PALETTE["muted"]};font-size:11px;line-height:1.65">{detail}</div>'
        )
        self._update_context()

    def _on_preset_change(self, attr: str, old: str, new: str) -> None:
        presets = {
            "quick": (40, 80),
            "balanced": (200, 600),
            "full": (400, 0),
        }
        if new not in presets:
            return
        self._applying_preset = True
        self.window_size.value, self.max_frames.value = presets[new]
        self._applying_preset = False
        self._update_context()

    def _mark_custom_preset(self, attr: str, old: float, new: float) -> None:
        if not self._applying_preset:
            self.preset_select.value = "custom"
        self._update_context()

    def _update_context(self) -> None:
        if not hasattr(self, "context_div"):
            return
        if self.branch == "own":
            source = self.own_input.value
            label = "室内数据"
        elif self.branch == "uji":
            source = self.uji_test_file.value
            label = "UJI 基准"
        else:
            source = self.outdoor_input.value
            label = "室外 RTK"
        tuning = ""
        if self.branch == "outdoor" and self.outdoor_auto_tune.value == "true":
            if self.outdoor_tune_dry_run.value == "true":
                tuning = " · DeepSeek Dry Run"
            else:
                tuning = f" · DeepSeek {int(self.outdoor_tune_iterations.value)} 轮建议"
        frames = "完整序列" if int(self.max_frames.value) == 0 else f"{int(self.max_frames.value)} 帧"
        self.context_div.text = f"""
        <div style="color:{PALETTE['muted']};font-size:12px;line-height:1.75;border-top:1px solid {PALETTE['border']};padding-top:10px">
          <div><b style="color:{PALETTE['text']}">{label}</b> · {html.escape(source)}</div>
          <div>窗口 {int(self.window_size.value)} · {frames}{tuning}</div>
        </div>
        """

    def _collect_params(self) -> dict[str, Any]:
        if self.heading_mode.value == "manual":
            initial_heading = float(self.own_initial_heading.value)
            use_route_heading = False
        elif self.heading_mode.value == "route":
            initial_heading = None
            use_route_heading = True
        else:
            initial_heading = None
            use_route_heading = False

        max_frames = int(self.max_frames.value)
        params = {
            "branch": self.branch,
            "window_size": int(self.window_size.value),
            "max_frames": max_frames if max_frames > 0 else None,
            "uji": self.uji_input.value,
            "uji_test_file": self.uji_test_file.value.strip() or None,
            "uji_data_root": self.uji_data_root.value.strip() or None,
            "own": self.own_input.value,
            "own_profile": self.own_profile.value,
            "own_dataset_key": self.own_dataset_key.value.strip() or None,
            "own_data_dir": self.own_data_dir.value.strip() or None,
            "own_map_mode": self.own_map_mode.value,
            "own_map_npz_path": self.own_map_npz.value.strip() or None,
            "own_route": self.own_route.value.strip() or None,
            "own_initial_heading_deg": initial_heading,
            "own_use_route_initial_heading": use_route_heading,
            "mirror_y": self.mirror_y.value == "true",
            "own_heading_offset_deg": float(self.heading_offset.value),
            "own_trim_head": int(self.trim_head.value),
            "own_trim_tail": int(self.trim_tail.value),
            "outdoor": self.outdoor_input.value,
            "outdoor_navigation_key": self.outdoor_input.value,
            "outdoor_data_root": self.outdoor_data_root.value.strip() or None,
            "outdoor_auto_tune": self.outdoor_auto_tune.value == "true",
            "outdoor_tune_iterations": int(self.outdoor_tune_iterations.value),
            "outdoor_tune_dry_run": self.outdoor_tune_dry_run.value == "true",
            "outdoor_tune_config_path": "config/deepseek_auto_tuning.json",
            "deepseek_api_key": self.deepseek_api_key.value.strip() or None,
        }
        self._validate_params(params)
        return params

    def _validate_params(self, params: dict[str, Any]) -> None:
        if params["window_size"] < 2:
            raise ValueError("地磁历史窗口必须大于 1。")
        if params["branch"] == "own":
            if not params.get("own_data_dir"):
                raise ValueError("请选择室内数据集或填写数据目录。")
            if params.get("own_route"):
                parse_route_control_points(params["own_route"])
        elif params["branch"] == "uji" and not params.get("uji_test_file"):
            raise ValueError("请选择 UJI 测试序列。")
        elif params["branch"] == "outdoor" and not params.get("outdoor_navigation_key"):
            raise ValueError("请选择室外 Navigation 测试序列。")
        elif params["branch"] == "outdoor" and not 1 <= params["outdoor_tune_iterations"] <= 20:
            raise ValueError("DeepSeek 建议轮数必须在 1 到 20 之间。")

    def _on_file_upload(self, attr: str, old: str, new: str) -> None:
        if not new or not self.file_input.filename:
            return
        try:
            filename = Path(self.file_input.filename).name
            suffix = Path(filename).suffix.lower()
            allowed = {".npz"} if self.branch == "own" else {".txt"}
            if suffix not in allowed:
                raise ValueError(f"当前分支只支持：{', '.join(sorted(allowed))}")
            encoded = new.split(",", 1)[-1]
            payload = base64.b64decode(encoded, validate=True)
            if len(payload) > MAX_UPLOAD_BYTES:
                raise ValueError("文件超过 12 MB 限制。")
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            target = UPLOAD_DIR / filename
            target.write_bytes(payload)
            if suffix == ".npz":
                self.own_map_npz.value = str(target)
                self.own_map_mode.value = "raw"
            else:
                self.uji_test_file.value = str(target)
            self.file_hint.text = (
                f'<span style="color:{PALETTE["success"]};font-size:12px">已载入 {html.escape(filename)} · {len(payload) / 1024:.1f} KB</span>'
            )
            self._set_status("ready", "文件已载入", "路径已自动填入高级参数，可直接运行。")
        except Exception as exc:
            self.file_hint.text = f'<span style="color:{PALETTE["danger"]};font-size:12px">{html.escape(str(exc))}</span>'
            self._set_status("error", "上传失败", str(exc))

    def _on_run(self) -> None:
        if self.running:
            return
        try:
            params = self._collect_params()
        except Exception as exc:
            self._set_status("error", "参数需要调整", str(exc))
            return

        self._stop_playback()
        self.running = True
        self.run_button.disabled = True
        self.run_button.label = "正在计算…"
        self._progress_percent = -1
        self._set_progress(0, "准备数据", "正在初始化传感器、地图与粒子滤波器")
        self._set_status("running", "仿真运行中", "正在读取传感器数据并执行 PF / PDR 管线。")
        started = time.perf_counter()
        future = self._executor.submit(run_simulation, params, self._queue_progress)
        self._run_future = future
        future.add_done_callback(partial(self._queue_run_finished, started=started))

    def _queue_progress(self, current: int, total: int, phase: str) -> None:
        """Receive backend progress on the worker and marshal it to Bokeh."""
        total = max(int(total), 1)
        current = min(max(int(current), 0), total)
        percent = int(round(current * 100.0 / total))
        if percent == self._progress_percent and current < total:
            return
        self._progress_percent = percent
        detail = (
            f"总进度 {current} / {total}"
            if str(phase).startswith("自动调参") or str(phase).startswith("DeepSeek")
            else f"{current} / {total} 帧"
        )
        try:
            self.doc.add_next_tick_callback(
                partial(self._set_progress, percent, phase, detail)
            )
        except RuntimeError:
            pass

    def _queue_run_finished(self, future: Future, started: float) -> None:
        try:
            self.doc.add_next_tick_callback(partial(self._finish_run, future, started))
        except RuntimeError:
            pass

    def _finish_run(self, future: Future, started: float) -> None:
        try:
            result = future.result()
            elapsed = time.perf_counter() - started
            self._apply_result(result)
            self._set_progress(100, "计算完成", f"结果整理完毕 · 用时 {elapsed:.1f} 秒", state="success")
            self._set_status(
                "success",
                "仿真完成",
                f"{result.dataset} · {result.steps_detected} 个步态 · 用时 {elapsed:.1f} 秒",
            )
        except Exception as exc:
            traceback.print_exc()
            self._set_progress(max(self._progress_percent, 0), "运行中断", str(exc), state="error")
            self._set_status("error", "运行失败", str(exc))
        finally:
            self.running = False
            self._run_future = None
            self.run_button.disabled = False
            self.run_button.label = "重新运行定位仿真" if self.result else "运行定位仿真"

    def _on_session_destroyed(self, session_context) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _apply_result(self, result: SimulationViewResult) -> None:
        self.result = result
        total_steps = max(len(result.pf_track), len(result.pdr_track), 1)
        self.step_slider.end = max(2, total_steps)
        self.step_slider.value = total_steps
        self.step_slider.disabled = total_steps <= 1
        self.rewind_button.disabled = total_steps <= 1
        self.play_button.disabled = total_steps <= 1
        self.next_button.disabled = True
        self.export_button.disabled = False

        self.route_source.data = {
            "x": result.route_track[:, 0].tolist() if result.route_track.size else [],
            "y": result.route_track[:, 1].tolist() if result.route_track.size else [],
        }
        self._render_step(total_steps)
        self._update_diagnostic_sources(result)
        self._update_metrics(result)
        self._update_summary(result)
        self._fit_trajectory_range(result)
        self.trajectory_plot.title.text = f"{result.dataset} · PF / PDR 轨迹对比"
        self.export_source.data = {
            "json": [json.dumps(result.raw, ensure_ascii=False, indent=2, default=_json_default)]
        }

    @staticmethod
    def _track_source(track: np.ndarray, error: np.ndarray, count: int) -> dict[str, list]:
        visible = track[: min(count, len(track))]
        errors = np.full(len(visible), np.nan, dtype=float)
        if error.size:
            errors[: min(len(errors), len(error))] = error[: min(len(errors), len(error))]
        return {
            "x": visible[:, 0].tolist() if visible.size else [],
            "y": visible[:, 1].tolist() if visible.size else [],
            "step": list(range(len(visible))),
            "error": errors.tolist(),
        }

    def _render_step(self, count: int) -> None:
        if self.result is None:
            return
        self.pf_source.data = self._track_source(self.result.pf_track, self.result.pf_error, count)
        self.pdr_source.data = self._track_source(self.result.pdr_track, self.result.pdr_error, count)
        if self.pf_source.data["x"]:
            self.current_source.data = {
                "x": [self.pf_source.data["x"][-1]],
                "y": [self.pf_source.data["y"][-1]],
            }
        else:
            self.current_source.data = {"x": [], "y": []}
        self.next_button.disabled = count >= int(self.step_slider.end)

    def _update_diagnostic_sources(self, result: SimulationViewResult) -> None:
        n = max(len(result.pf_error), len(result.pdr_error), 1)
        pf = np.full(n, np.nan)
        pdr = np.full(n, np.nan)
        if result.pf_error.size:
            pf[: len(result.pf_error)] = result.pf_error
        if result.pdr_error.size:
            pdr[: len(result.pdr_error)] = result.pdr_error
        self.error_source.data = {"step": list(range(n)), "pf": pf.tolist(), "pdr": pdr.tolist()}
        self.particle_source.data = {
            "step": list(range(len(result.particle_counts))),
            "count": result.particle_counts.tolist(),
        }

    def _update_metrics(self, result: SimulationViewResult) -> None:
        stats = result.pf_stats or {}
        frames = "—" if result.frames_used is None else f"{result.frames_used:,}"
        mean = "—" if stats.get("mean") is None else f"{float(stats['mean']):.3f} m"
        p95 = "—" if stats.get("p95") is None else f"{float(stats['p95']):.3f} m"
        self.metrics_div.text = f"""
        <div class="gm-metrics">
          <div class="gm-metric"><span>处理帧数</span><strong>{frames}</strong><small>sensor frames</small></div>
          <div class="gm-metric"><span>检测步态</span><strong>{result.steps_detected:,}</strong><small>detected steps</small></div>
          <div class="gm-metric gm-accent"><span>PF 平均误差</span><strong>{mean}</strong><small>mean error</small></div>
          <div class="gm-metric"><span>PF P95 误差</span><strong>{p95}</strong><small>95th percentile</small></div>
        </div>
        """
        self.metrics_div.stylesheets = [InlineStyleSheet(css=f"""
          :host {{ color:{PALETTE['text']}; }}
          .gm-metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
          .gm-metric {{ background:{PALETTE['panel']}; border:1px solid {PALETTE['border']}; border-radius:14px; padding:14px 16px; min-height:74px; }}
          .gm-metric span {{ color:{PALETTE['muted']}; display:block; font-size:12px; margin-bottom:7px; }}
          .gm-metric strong {{ font-size:21px; letter-spacing:-.02em; display:block; }}
          .gm-metric small {{ color:#607a91; display:block; font-size:10px; margin-top:4px; text-transform:uppercase; letter-spacing:.08em; }}
          .gm-accent {{ border-color:rgba(58,214,199,.48); background:{PALETTE['accent_dark']}; }}
          .gm-accent strong {{ color:{PALETTE['accent']}; }}
          @media (max-width:900px) {{ .gm-metrics {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
        """)]

    def _update_summary(self, result: SimulationViewResult) -> None:
        output_json = result.raw.get("output_json") or "—"
        output_png = result.raw.get("output_png") or "—"
        tuning = result.raw.get("auto_tuning")
        tuning_html = ""
        if isinstance(tuning, dict):
            if tuning.get("status") == "dry_run":
                preview = tuning.get("request_preview") or "—"
                tuning_html = (
                    f'<div style="color:{PALETTE["blue"]};font-size:12px;margin-top:7px">'
                    f'DeepSeek Dry Run · 提示词：{html.escape(str(preview))}</div>'
                )
            else:
                best_trial = tuning.get("best_trial", result.raw.get("tuning_best_trial", "—"))
                best_score = tuning.get("best_score", result.raw.get("tuning_best_score"))
                score_text = "—" if best_score is None else f"{float(best_score):.4f}"
                trial_count = len(tuning.get("trials") or [])
                report = result.raw.get("auto_tuning_report") or "—"
                tuning_html = (
                    f'<div style="color:{PALETTE["accent"]};font-size:12px;margin-top:7px">'
                    f'DeepSeek 调参完成 · {trial_count} 次试验 · 最佳 trial {best_trial} · score {score_text}'
                    f'</div><div style="color:{PALETTE["muted"]};font-size:11px;margin-top:3px;word-break:break-all">'
                    f'调参报告 · {html.escape(str(report))}</div>'
                )
        self.result_summary.text = f"""
        <div style="display:flex;align-items:flex-start;gap:18px;color:{PALETTE['text']}">
          <div style="flex:1">
            <div style="color:{PALETTE['muted']};font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase">最近一次运行</div>
            <div style="font-size:16px;font-weight:750;margin-top:5px">{html.escape(result.dataset)}</div>
            <div style="color:{PALETTE['muted']};font-size:12px;margin-top:4px">分支 {result.branch.upper()} · PF {len(result.pf_track)} 点 · PDR {len(result.pdr_track)} 点 · 参考路线 {len(result.route_track)} 点</div>
            {tuning_html}
          </div>
          <div style="max-width:52%;color:{PALETTE['muted']};font-size:11px;line-height:1.7;word-break:break-all">
            <div>JSON · {html.escape(str(output_json))}</div>
            <div>PNG · {html.escape(str(output_png))}</div>
          </div>
        </div>
        """

    def _fit_trajectory_range(self, result: SimulationViewResult) -> None:
        arrays = [arr for arr in (result.route_track, result.pf_track, result.pdr_track) if arr.size]
        if not arrays:
            return
        all_points = np.vstack(arrays)
        min_x, min_y = np.nanmin(all_points, axis=0)
        max_x, max_y = np.nanmax(all_points, axis=0)
        span = max(max_x - min_x, max_y - min_y, 1.0)
        pad = span * 0.08
        self.trajectory_plot.x_range.start = float(min_x - pad)
        self.trajectory_plot.x_range.end = float(max_x + pad)
        self.trajectory_plot.y_range.start = float(min_y - pad)
        self.trajectory_plot.y_range.end = float(max_y + pad)

    def _on_layer_toggle(self, attr: str, old: list[int], new: list[int]) -> None:
        active = set(new)
        self.pf_renderer.visible = 0 in active
        self.pdr_renderer.visible = 1 in active
        self.route_renderer.visible = 2 in active

    def _on_step_slider(self, attr: str, old: float, new: float) -> None:
        if self.result is not None:
            self._render_step(int(new))

    def _on_rewind(self) -> None:
        if self.result is None:
            return
        self._stop_playback()
        self.step_slider.value = 1

    def _on_next(self) -> None:
        if self.result is None:
            return
        self._stop_playback()
        if self.step_slider.value < self.step_slider.end:
            self.step_slider.value += 1

    def _on_play(self) -> None:
        if self.result is None:
            return
        if self.playback_callback is not None:
            self._stop_playback()
            return
        if self.step_slider.value >= self.step_slider.end:
            self.step_slider.value = 1
        self.play_button.label = "暂停"
        self.playback_callback = self.doc.add_periodic_callback(self._advance_playback, 90)

    def _advance_playback(self) -> None:
        if self.step_slider.value >= self.step_slider.end:
            self._stop_playback()
            return
        self.step_slider.value += 1

    def _stop_playback(self) -> None:
        if self.playback_callback is not None:
            self.doc.remove_periodic_callback(self.playback_callback)
            self.playback_callback = None
        if hasattr(self, "play_button"):
            self.play_button.label = "播放"

    def _on_reset(self) -> None:
        self._stop_playback()
        self._applying_preset = True
        self.branch_toggle.active = 0
        self.own_input.value = "route1_run2"
        self.uji_input.value = "tt02"
        self.outdoor_input.value = "nav1"
        self.outdoor_auto_tune.value = "false"
        self.outdoor_tune_iterations.value = 3
        self.outdoor_tune_dry_run.value = "false"
        self.deepseek_api_key.value = ""
        self.preset_select.value = "full"
        self.window_size.value = 400
        self.max_frames.value = 0
        self.heading_mode.value = "route"
        self.own_initial_heading.value = 0
        self.heading_offset.value = -90
        self.mirror_y.value = "false"
        self.trim_head.value = 0
        self.trim_tail.value = 0
        self.own_map_mode.value = "raw"
        self.own_map_npz.value = ""
        self.own_route.value = ""
        self._applying_preset = False
        self._sync_own_selection()
        self._sync_uji_selection()
        self._sync_outdoor_selection()
        self._apply_branch_state()
        self._apply_heading_state()
        self._reset_result_view()
        self._set_status("ready", "已恢复 Main 默认", "当前数据集和计算参数与 main.py 的默认配置一致。")

    def _reset_result_view(self) -> None:
        self.result = None
        self.pf_source.data = dict(x=[], y=[], step=[], error=[])
        self.pdr_source.data = dict(x=[], y=[], step=[], error=[])
        self.route_source.data = dict(x=[], y=[])
        self.current_source.data = dict(x=[], y=[])
        self.error_source.data = dict(step=[], pf=[], pdr=[])
        self.particle_source.data = dict(step=[], count=[])
        self.step_slider.disabled = True
        self.play_button.disabled = True
        self.next_button.disabled = True
        self.rewind_button.disabled = True
        self.export_button.disabled = True
        self.trajectory_plot.title.text = "等待运行"
        self.metrics_div.text = f"""
        <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;color:{PALETTE['muted']}">
          {''.join(f'<div style="background:{PALETTE["panel"]};border:1px solid {PALETTE["border"]};border-radius:14px;padding:18px"><span style="font-size:12px">{label}</span><strong style="display:block;color:{PALETTE["text"]};font-size:22px;margin-top:8px">—</strong></div>' for label in ['处理帧数','检测步态','PF 平均误差','PF P95 误差'])}
        </div>
        """
        self.result_summary.text = f'<div style="color:{PALETTE["muted"]};font-size:13px">运行完成后，这里会显示输出文件和轨迹摘要。</div>'
        self._set_progress(0, "等待开始", "Main 默认 · 完整序列 · 窗口 400", state="ready")
        self._set_status("ready", "环境就绪", "默认参数与 main.py 一致，不会打开额外的 Matplotlib 窗口。")

    def _set_progress(self, percent: int, phase: str, detail: str = "", state: str = "running") -> None:
        percent = min(max(int(percent), 0), 100)
        colors = {
            "ready": PALETTE["blue"],
            "running": PALETTE["accent"],
            "success": PALETTE["success"],
            "error": PALETTE["danger"],
        }
        color = colors.get(state, PALETTE["accent"])
        self.progress_div.text = f"""
        <div style="margin-top:3px;color:{PALETTE['text']}">
          <div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-bottom:7px">
            <strong style="font-size:12px">{html.escape(phase)}</strong>
            <span style="color:{color};font-size:12px;font-weight:800">{percent}%</span>
          </div>
          <div style="height:9px;background:#081522;border:1px solid {PALETTE['border']};border-radius:999px;overflow:hidden">
            <div style="height:100%;width:{percent}%;background:linear-gradient(90deg,{color},{PALETTE['blue']});border-radius:999px;transition:width .18s ease"></div>
          </div>
          <div style="color:{PALETTE['muted']};font-size:11px;margin-top:6px;min-height:16px">{html.escape(detail)}</div>
        </div>
        """

    def _set_status(self, kind: str, title: str, detail: str) -> None:
        colors = {
            "ready": PALETTE["blue"],
            "running": PALETTE["orange"],
            "success": PALETTE["success"],
            "error": PALETTE["danger"],
        }
        color = colors.get(kind, PALETTE["muted"])
        pulse = "box-shadow:0 0 0 5px rgba(255,179,92,.10);" if kind == "running" else ""
        self.status_div.text = f"""
        <div style="display:flex;gap:11px;align-items:flex-start;color:{PALETTE['text']}">
          <span style="width:9px;height:9px;margin-top:5px;border-radius:50%;background:{color};{pulse}"></span>
          <div><strong style="font-size:13px">{html.escape(title)}</strong>
          <div style="color:{PALETTE['muted']};font-size:12px;line-height:1.55;margin-top:4px">{html.escape(detail)}</div></div>
        </div>
        """


app = GeoMagApp()
curdoc().add_root(app.layout)
curdoc().title = "地磁定位实验台"
curdoc().template = APP_TEMPLATE
