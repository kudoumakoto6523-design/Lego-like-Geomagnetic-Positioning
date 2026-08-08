# Geomag Capture for iPhone

这是与 `GeomagMac Native` 配套的最小原生 iPhone 采集端，工程位于 `GeomagCapture.xcodeproj`。App 使用 Core Motion 直接采集原始三轴传感器和系统融合后的设备姿态，不经过 Python、网页或 `bash run.sh`。

## 当前功能

- 以 100 Hz 请求频率采集原始加速度、角速度和磁场，同时保存 Core Motion 校准磁场。
- 同时采集 Core Motion 四元数、横滚/俯仰/航向、重力、去重力加速度和旋转率。
- 实时显示姿态、磁场强度、磁场校准精度、实际采样率和各数据流样本数。
- 同时显示 Core Motion 原始设备 yaw，以及由“已知初始方向 + 相对 yaw”得到的全局航向；
  后者与空间事件一起写入采集包，不把手机 yaw 误当作楼层绝对方向。
- 检测手机是否大致平放；采集期间保持屏幕常亮。
- 可选填写真实路线和初始航向，随采集包一起保存。
- 必填统一楼层坐标系、起点 `(x, y)` 和初始航向，自动记录起点锚点。
- 采集中可一键标记转角，或记录带全局 `(x, y)` 的空间锚点和精确时间戳。
- 预先填写真实路线后，可用“下个路线点”按钮依次记录坐标，无需边走边输入。
- “房间建图模式”可按房间宽、高和 0.2～1.0 m 线距生成横向、纵向蛇形扫描任务。
- 显示已完成锚点覆盖率；超过 25 秒未记录下个锚点时提示可能漏记。
- 支持暂停/恢复建图，并把事件和时间戳写入 `SpatialEvents.csv`。
- App 进入后台时自动停止并保存，避免静默丢失数据。
- 采集过程中各传感器数据持续写入 Application Support；App 意外退出后会自动恢复最近一次未导出的采集。
- 停止时先排空传感器队列，再在后台生成并原子保存完整采集包，避免尾部样本遗漏和界面卡顿。
- 停止后导出 `.geomagcapture` 采集包。

## 在真机运行

1. 用 Xcode 打开 `GeomagCapture.xcodeproj`。
2. 在 `GeomagCapture` Target 的 Signing & Capabilities 中选择自己的 Team。
3. 如果 Bundle Identifier 冲突，将 `com.xuminglei.GeomagCapture` 改为自己的唯一标识。
4. 连接并解锁 iPhone，在设备上启用开发者模式并信任这台 Mac。
5. 在 Xcode 顶部设备列表选择该 iPhone，按 `Command-R`。
6. 首次开始采集时允许运动与健身/运动传感器访问。

模拟器没有真实 Core Motion 传感器，只能检查界面。点击“开始采集”会明确提示需要真实 iPhone。

## 推荐采集流程

1. 手机正面朝上、前端朝向行走方向。
2. 先确定整层楼统一使用的坐标系名称，例如 `building-a-floor-1`；同一张磁图的所有
   采集必须逐字一致，并统一约定 +X、+Y 方向。
3. 填写一个不重复的数据集名称、起点全局坐标和初始航向。建议提前填写全局路线
   坐标，路线第一个点必须与起点一致。
4. 点击“开始采集”后原地静止约 3 秒；App 会自动记录起点锚点。
5. 保持正常、尽量稳定的速度行走。经过预填路线点时点击“下个路线点”；没有预填
   坐标时，可输入当前已知坐标并点击“记录坐标锚点”。
6. 遇到没有已知坐标的转弯，也应在转弯中心点击“标记转角”。
7. 到达终点后记录终点锚点，继续静止约 3 秒。
8. 点击“停止并保存”，然后导出 `.geomagcapture`。

房间建图建议把左下角设为 `(0,0)`：

1. 开启“房间建图模式”，输入房间宽、高和 `0.4～0.6 m` 扫描线距并生成任务。
2. 先完成横向蛇形扫描，再完成纵向蛇形扫描；到达每个线端点时点击“下个路线点”。
3. 换扫描线或需要绕开人员时可暂停，到位后恢复。
4. 同一区域独立采集 2～3 次，使用不同数据集名称，但使用完全相同的坐标系和磁图名称。
5. 至少另留一组采集只用于定位测试，不参与建图。

路线坐标单位固定为米：例如 180 cm 必须填成 `1.80`。初始航向使用数学角度，
`0° = +X`、`90° = +Y`、`-90° = -Y`。已填路线时建议将初始航向留空，由首段坐标自动推导。

开始和结束静止段用于估计陀螺仪零偏及识别有效行走区间，不应省略。

二维地磁图至少需要三个不共线的全局坐标锚点。多个采集可以共同提供这些锚点，
但坐标系名称、单位和坐标轴方向必须一致。只标记“转角”能改善运动分段，却不能
代替带 `(x, y)` 的地图锚点。

## 采集包内容

`.geomagcapture` 是一个目录型文档，包含：

| 文件 | 内容 |
| --- | --- |
| `Accelerometer.csv` | SI 单位原始加速度，表头与 GeomagMac Native 兼容 |
| `Gyroscope.csv` | rad/s 原始角速度 |
| `Magnetometer.csv` | µT Core Motion 校准磁场，供定位算法使用 |
| `MagnetometerRaw.csv` | µT 未校准硬件磁场，仅供诊断 |
| `DeviceMotion.csv` | 四元数、姿态、重力、去重力加速度、旋转率、融合磁场 |
| `SpatialEvents.csv` | 起点、转角、已知坐标锚点、停止事件及同步航向 |
| `geomag_dataset.json` | 数据集名称、路线、初始航向 |
| `capture_metadata.json` | v3 空间参考、时长、请求/实际采样率和样本数 |
| `README.txt` | 数据格式简述 |

三路原始 CSV 使用相同的 Core Motion 系统运行时间作为时钟，再统一减去采集开始时间，因此彼此可以按时间插值对齐。

`DeviceMotion.csv` 的 `Magnetic Accuracy` 对应 Core Motion 校准级别：`-1` 未校准、`0` 低、`1` 中、`2` 高。格式版本 2 起，`Magnetometer.csv` 与 `DeviceMotion.csv` 中的校准磁场同源；这避免将 iPhone 原始磁力计的大幅硬铁偏置误送入地磁匹配。

格式版本 3 新增 `spatial_reference` 和 `SpatialEvents.csv`。旧 v2 采集仍可用于惯导
与路线内验证，但由于没有统一空间锚点，不能直接合并成二维地磁地图。

GeomagMac Native 的“导入采集文件夹”已允许把 `.geomagcapture` 当作目录打开；传到 Mac 后可以直接选择该采集包，不需要手动拆分 CSV。

## 构建验证

模拟器：

```bash
xcodebuild -project GeomagCapture.xcodeproj \
  -scheme GeomagCapture \
  -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -derivedDataPath .derivedData \
  CODE_SIGNING_ALLOWED=NO build
```

真机架构：

```bash
xcodebuild -project GeomagCapture.xcodeproj \
  -scheme GeomagCapture \
  -configuration Debug \
  -destination 'generic/platform=iOS' \
  -derivedDataPath .deviceDerivedData \
  CODE_SIGNING_ALLOWED=NO build
```

采集包与中断恢复回归测试：

```bash
xcodebuild test -project GeomagCapture.xcodeproj \
  -scheme GeomagCapture \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  SWIFT_STRICT_CONCURRENCY=complete CODE_SIGNING_ALLOWED=NO
```

当前测试覆盖 Core Motion 校准磁场字段、采集中断恢复，以及停止后的原子持久化。

## 下一步

- 在真机采集结果上比较原始陀螺积分与 Core Motion 四元数航向。
- 增加 iPhone 到 Mac 的局域网/点对点实时传输。
- 在真机扫描中检查锚点是否漏记，并根据实际步速调整 25 秒提醒阈值。
