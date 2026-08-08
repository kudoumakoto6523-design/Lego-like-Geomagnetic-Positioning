# GeomagMac Native

独立的纯 Swift macOS 地磁定位应用。CSV 解析、时间对齐、步态检测、Core Motion 相对航向、PDR、坐标地磁图、粒子滤波（PF）、定位健康度以及 PNG/CSV 导出都在 App 进程内完成；运行时不启动 Python、`bash run.sh` 或算法子进程。

当前阶段实现的是：**已知起点、任意形状路线**。下一阶段才会加入全房间未知起点的全局重定位。

## 运行

1. 用 Xcode 打开 `GeomagMac.xcodeproj`。
2. 选择 `GeomagMac` scheme 和 `My Mac`。
3. 按 `Command-R`。

内置验证数据：

- `route_13_1` 建立 Route 13 坐标磁图，`route_13_2/_3` 是独立留出定位采集。
- `route_15_1` 建立 Route 15 坐标磁图，`route_15_2/_3` 是独立留出定位采集。
- 建图数据不会作为自身的定位查询，避免用同一组数据同时标定和验证。

## 使用流程

### 第一次采集：建立地磁图

1. 在“运行定位算法”选择“导入数据”，导入 iPhone 采集包。
2. 填写数据集名称、已知真实路线坐标和初始航向。路线可以包含任意角度和任意数量的折点。
3. 在“坐标地磁图”填写新磁图名称，点击“用当前采集建立磁图”。
4. App 检测步态，把每个步级磁场样本按真实路线累计距离注册到米制坐标并保存。

新版 iPhone 包如果包含 `SpatialEvents.csv` 中的两个或更多坐标锚点，macOS 会优先使用锚点建图：在相邻锚点之间按 PDR 累计距离分配采样位置，并生成默认 `0.4 m` 二维网格。每格保存房间坐标系 `Bx/By/Bz`、模长、方差、样本数、采集方向数和可计算的磁场梯度。

再次使用相同磁图名称、相同坐标系和网格尺寸导入第 2～3 次采集时，App 会按网格中位数自动合并并累计来源；不一致的坐标系会拒绝合并。

### 第二次及后续采集：定位验证

1. 在同一区域再次独立采集，起点坐标应已知。
2. 导入新采集并选择第一次建立的地磁图。
3. 点击“开始计算”。
4. 白线是真实路线，青线是 PDR，黄色虚线是 PF 地磁匹配结果，绿色区域是 PF 粒子置信范围。

所选磁图必须覆盖实际行走区域。单条参考路线只形成其附近的窄带磁图；要支持房间内更多任意路线，需要先用多条已知路线覆盖房间，而不是把一条路线外推到整个房间。

用户磁图保存在：

```text
~/Library/Application Support/GeomagMacNative/MagneticMaps/
```

运行结果保存在：

```text
~/Library/Application Support/GeomagMacNative/Runs/
```

## 原生算法

1. 校验并读取 `Accelerometer.csv`、`Gyroscope.csv`、`Magnetometer.csv`，以加速度时间轴插值对齐。
2. 如果存在 `DeviceMotion.csv`，通过覆盖率、磁校准、连续性和转动一致性门控后采用 Core Motion 相对 yaw；否则回退到陀螺仪积分。
3. 从真正静止帧估计陀螺零偏，动态检测步态，并使用 Weinberg 模型估计步长。
4. PDR 从已知起点传播。算法不再依赖路线名称、固定四段、闭合矩形或 90° 航向网格。
5. PF 使用 2,000 个粒子联合估计位置、航向偏差、步长比例和磁场偏移；标量模长、相邻变化以及二维网格可用时的房间坐标系三轴磁场共同参与似然。
6. 粒子只能存在于坐标磁图支持范围内，并输出 PF 轨迹、95%/80% 粒子范围、有效样本率、磁信息量、歧义度和恢复事件。

## 导入格式

采集目录至少包含：

- `Accelerometer.csv`
- `Gyroscope.csv`
- `Magnetometer.csv`

推荐同时包含 `DeviceMotion.csv`。`geomag_dataset.json` 或 `capture_metadata.json` 可以预填数据集名称、路线和初始航向。

路线格式：

```text
1.44,0.55; 1.44,8.25; 3.20,9.10; 5.28,8.25
```

初始航向采用数学角度：0° 指向 +X，逆时针为正；留空时由路线前两个点推导。

## 当前功能

- 新采集建立、选择、保存和删除通用坐标地磁图。
- 明确阻止建图采集对自身进行定位验证。
- 已知起点的 PDR + 原生地磁 PF，不限制路线形状或转角。
- 白/青/黄/绿路线与置信范围显示、播放、缩放、拖动和复位。
- PF/PDR 平均、P95、中位数和终点误差。
- 定位可靠度、歧义和自动恢复状态。
- 传感器质量检查、有效行走区间检测、运行取消、日志和历史结果。
- 选择文件夹导出 1600×1000 PNG 和长表 CSV。
- Route 13/15 的内置坐标磁图与四组独立留出测试。

已移除界面中的路线专用磁模板、固定四段诊断、90° 航向预设和“受控路线跳过 PF”等功能。历史结果 JSON 中的旧字段仍可解码，避免旧文件无法打开，但新计算不会生成这些诊断。

## 验证

构建：

```bash
xcodebuild -project GeomagMac.xcodeproj -scheme GeomagMac \
  -configuration Debug -derivedDataPath .nativeDerivedData \
  CODE_SIGNING_ALLOWED=NO build
```

XCTest：

```bash
xcodebuild test -project GeomagMac.xcodeproj -scheme GeomagMac \
  -destination 'platform=macOS' \
  SWIFT_STRICT_CONCURRENCY=complete CODE_SIGNING_ALLOWED=NO
```

留出数据冒烟测试会检查黄色 PF 路线和绿色置信历史是否完整：

```bash
xcrun swiftc -O -parse-as-library \
  GeomagMac/Models/AlgorithmSettings.swift \
  GeomagMac/Models/PositioningResult.swift \
  GeomagMac/Models/DatasetImport.swift \
  GeomagMac/Native/DeviceHeadingResolver.swift \
  GeomagMac/Native/ControlledMotionProfile.swift \
  GeomagMac/Native/ControlledMagneticTemplates.swift \
  GeomagMac/Native/ControlledMagneticFusion.swift \
  GeomagMac/Native/MagneticTemplateStore.swift \
  GeomagMac/Native/GenericMagneticMapStore.swift \
  GeomagMac/Native/NativePositioningEngine.swift \
  Tools/NativeEngineSmoke.swift -o /tmp/geomag-native-smoke

GEOMAG_NATIVE_RESOURCE_ROOT="$PWD/GeomagMac/Resources" \
  /tmp/geomag-native-smoke
```

当前确定性留出结果：

| 数据 | 步数 | PDR 平均误差 | PF 平均误差 |
| --- | ---: | ---: | ---: |
| `route_13_2` | 37 | 1.165 m | 0.680 m |
| `route_13_3` | 31 | 1.348 m | 0.847 m |
| `route_15_2` | 78 | 1.956 m | 1.663 m |
| `route_15_3` | 90 | 3.092 m | 1.835 m |

这些结果证明通用 PF 通路已经真实运行并优于当前 PDR，但不是最终精度结论。Route 15 的短边以及长直走廊的磁指纹歧义仍较明显，需要更多空间覆盖和独立测试数据。

## 当前边界与下一阶段

- 当前必须已知起点；PF 在起点附近初始化。
- 一次沿线建图只覆盖该路线附近，尚不能代表整个房间。
- 真实路线只用于建图坐标注册和离线误差评估，在线 PF 不应使用未来真值约束。
- 下一阶段将在多路线房间磁图稳定后增加未知起点的全局粒子初始化、分层粗到细检索和重定位置信门控。
