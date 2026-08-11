import Combine
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var isPlaying = false
    @State private var showFullLog = false
    @State private var showMagneticMapQuality = false

    private let timer = Timer.publish(every: 0.04, on: .main, in: .common).autoconnect()

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 250, ideal: 280, max: 330)
        } detail: {
            detail
        }
        .alert(
            "出现问题",
            isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )
        ) {
            Button("好", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "未知错误")
        }
        .alert(
            "导出完成",
            isPresented: Binding(
                get: { model.exportMessage != nil },
                set: { if !$0 { model.exportMessage = nil } }
            )
        ) {
            Button("好", role: .cancel) {}
        } message: {
            Text(model.exportMessage ?? "")
        }
        .sheet(isPresented: $showFullLog) {
            BackendLogView(
                log: model.backendLog,
                status: model.backendStatus,
                isRunning: model.isBackendRunning,
                copyAction: model.copyBackendLog,
                clearAction: model.clearBackendLog
            )
        }
        .sheet(isPresented: $showMagneticMapQuality) {
            if let map = model.selectedMagneticMap {
                MagneticMapQualitySheet(document: map.document)
            }
        }
        .onReceive(timer) { _ in
            guard isPlaying else { return }
            if model.playbackProgress >= 1.0 {
                isPlaying = false
            } else {
                model.playbackProgress = min(1.0, model.playbackProgress + 0.008)
            }
        }
    }

    private var sidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 5) {
                    Label("GeomagMac Native", systemImage: "location.viewfinder")
                        .font(.title2.weight(.bold))
                    Text("纯 Swift 地磁定位")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                sidebarSection("示例数据") {
                    Picker("示例", selection: $model.selectedSampleID) {
                        ForEach(AppModel.samples) { sample in
                            Text(sample.title).tag(sample.id)
                        }
                    }
                    .labelsHidden()
                    .onChange(of: model.selectedSampleID) { id in
                        isPlaying = false
                        model.loadBundledSample(id: id)
                    }

                    Button(action: model.openResultFile) {
                        Label("打开其他 JSON…", systemImage: "folder")
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                sidebarSection("运行定位算法") {
                    HStack(spacing: 7) {
                        Image(systemName: "swift")
                        Text(model.backendModeTitle)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.green)

                    Picker("数据来源", selection: $model.runInputMode) {
                        ForEach(RunInputMode.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.segmented)
                    .disabled(model.isBackendRunning)
                    .onChange(of: model.runInputMode) { _ in
                        model.selectMagneticMapForBundledDataset()
                    }

                    if model.runInputMode == .bundled {
                        Picker("采集数据", selection: $model.selectedRunDatasetID) {
                            ForEach(AppModel.runnableDatasets) { sample in
                                Text(sample.title).tag(sample.id)
                            }
                        }
                        .labelsHidden()
                        .disabled(model.isBackendRunning)
                        .onChange(of: model.selectedRunDatasetID) { _ in
                            model.selectMagneticMapForBundledDataset()
                        }
                    } else {
                        importedDatasetPanel
                    }

                    Text("先用第一次采集建立坐标地磁图，再用同一区域的第二次或后续采集执行已知起点 PDR + PF。路线形状不受直线或 90° 转弯限制。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    algorithmParameterPanel

                    if model.isBackendRunning || model.backendProgress > 0 {
                        ProgressView(value: model.backendProgress)
                        Text("\(Int(model.backendProgress * 100))% · \(model.backendStatusDisplay)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        Text(model.backendStatusDisplay)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    HStack {
                        Button(action: model.runSelectedDataset) {
                            Label("开始计算", systemImage: "play.circle.fill")
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!model.canRunSelectedDataset)
                        .help(model.runButtonHelp)

                        if model.isBackendRunning {
                            Button("取消", role: .destructive, action: model.cancelBackendRun)
                        }
                    }

                    if !model.backendLog.isEmpty {
                        DisclosureGroup("运行日志") {
                            ScrollView {
                                Text(model.backendLog)
                                    .font(.system(.caption2, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(height: 110)
                            HStack {
                                Button("查看完整日志") { showFullLog = true }
                                Button("复制", action: model.copyBackendLog)
                                Spacer()
                                Button("清空", action: model.clearBackendLog)
                                    .disabled(model.isBackendRunning)
                            }
                            .font(.caption)
                        }
                        .font(.caption)
                    }
                }

                sidebarSection("坐标地磁图") {
                    Picker("地磁图", selection: $model.selectedMagneticMapID) {
                        if model.magneticMaps.isEmpty {
                            Text("尚无地磁图").tag("")
                        }
                        ForEach(model.magneticMaps) { entry in
                            Text(entry.document.name + (entry.isBuiltIn ? " · 内置" : " · 用户"))
                                .tag(entry.id)
                        }
                    }
                    .labelsHidden()
                    .disabled(
                        model.isMagneticMapBuilding
                            || model.isBackendRunning
                            || model.runInputMode == .bundled
                    )

                    if let map = model.selectedMagneticMap {
                        Label(map.document.localizationMode.title, systemImage: "location.viewfinder")
                            .font(.caption)
                            .foregroundStyle(
                                map.document.localizationMode == .roomAreaKnownStart
                                    ? .blue : .orange
                            )
                        Text(map.document.localizationMode.summary)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Label(
                            "参考采集：\(map.document.sourceLabel) · \(map.document.samples.count) 个网格"
                                + (map.document.gridCoverageRatio.map {
                                    " · 覆盖约 \(Int(($0 * 100).rounded()))%"
                                } ?? ""),
                            systemImage: "map.fill"
                        )
                        .font(.caption)
                        .foregroundStyle(.green)

                        if let quality = map.document.qualityReport {
                            Label(
                                "建图质量：\(quality.grade.rawValue) · "
                                    + "缺失 \(quality.expectedCellCount - quality.coveredCellCount) 格",
                                systemImage: quality.grade == .poor
                                    ? "exclamationmark.triangle.fill" : "checkmark.shield.fill"
                            )
                            .font(.caption)
                            .foregroundStyle(quality.grade == .poor ? .red : .secondary)

                            Button {
                                showMagneticMapQuality = true
                            } label: {
                                Label("查看二维覆盖热力图与质量", systemImage: "square.grid.3x3.fill")
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .buttonStyle(.bordered)
                        }
                    }

                    if model.runInputMode == .imported {
                        TextField("新磁图名称，例如 room_a", text: $model.newMagneticMapName)
                            .textFieldStyle(.roundedBorder)
                            .disabled(model.isMagneticMapBuilding || model.isBackendRunning)
                        Button(action: model.buildMagneticMapFromImportedDataset) {
                            Label("用当前采集建立磁图", systemImage: "map.badge.plus")
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .disabled(
                            model.isMagneticMapBuilding
                                || model.isBackendRunning
                                || !model.canBuildMapFromImportedDataset
                        )
                    }

                    if let map = model.selectedMagneticMap, !map.isBuiltIn {
                        Button("移除所选用户磁图", role: .destructive, action: model.removeSelectedMagneticMap)
                            .font(.caption)
                            .disabled(model.isMagneticMapBuilding || model.isBackendRunning)
                    }

                    Button("打开地磁图目录", action: model.openMagneticMapDirectory)
                        .font(.caption)
                        .disabled(model.isMagneticMapBuilding)

                    if model.isMagneticMapBuilding { ProgressView().controlSize(.small) }
                    Text(model.magneticMapStatus)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text("建图采集不能用于自我验证。当前阶段要求已知起点，且定位路线必须位于所选磁图已经覆盖的区域内。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                sidebarSection("结果管理") {
                    HStack {
                        Button(action: model.openRunsDirectory) {
                            Label("打开结果目录", systemImage: "folder")
                        }
                        Spacer()
                        Button(action: model.refreshRunHistory) {
                            Image(systemName: "arrow.clockwise")
                        }
                        .help("刷新历史结果")
                    }

                    if model.recentRuns.isEmpty {
                        Text("还没有算法运行结果")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(Array(model.recentRuns.prefix(5))) { record in
                            HStack(spacing: 7) {
                                Button {
                                    isPlaying = false
                                    model.loadRun(record)
                                } label: {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(record.displayName)
                                            .lineLimit(1)
                                        Text(runDate(record.modifiedAt))
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .buttonStyle(.plain)

                                Button {
                                    model.revealRun(record)
                                } label: {
                                    Image(systemName: "magnifyingglass")
                                }
                                .buttonStyle(.borderless)
                                .help("在 Finder 中显示")
                            }
                        }
                    }
                }

                sidebarSection("显示内容") {
                    routeToggle("真实路线", color: .white, isOn: $model.showTrueRoute)
                    routeToggle(
                        "PDR 路线",
                        color: Color(red: 0.10, green: 0.84, blue: 0.92),
                        isOn: $model.showPDR
                    )
                    routeToggle(
                        "PF 地磁匹配",
                        color: Color(red: 1.0, green: 0.78, blue: 0.16),
                        isOn: $model.showPF
                    )
                    routeToggle(
                        "PF 置信范围",
                        color: .green,
                        isOn: $model.showPFConfidence
                    )
                    .disabled(model.result?.pfConfidenceHistory?.isEmpty != false)
                }

                sidebarSection("路径播放") {
                    Slider(value: $model.playbackProgress, in: 0...1)
                    HStack {
                        Button {
                            if model.playbackProgress >= 1.0 {
                                model.playbackProgress = 0
                            }
                            isPlaying.toggle()
                        } label: {
                            Label(isPlaying ? "暂停" : "播放", systemImage: isPlaying ? "pause.fill" : "play.fill")
                        }
                        .buttonStyle(.borderedProminent)

                        Button("显示全部") {
                            isPlaying = false
                            model.playbackProgress = 1.0
                        }
                    }
                    Text("进度 \(Int(model.playbackProgress * 100))%")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                if let result = model.result {
                    sidebarSection("当前数据") {
                        infoRow("数据集", result.datasetKey)
                        infoRow("路线", result.routeLabel)
                        infoRow("检测步数", value(result.stepsDetected))
                        infoRow("使用帧数", value(result.sensorFramesUsed))
                        if let smoothingMode = result.pfSmoothingMode {
                            infoRow("PF 平滑", smoothingMode)
                        }
                        if let heading = result.headingDiagnostics {
                            infoRow("航向来源", heading.localizedMethod)
                            if heading.deviceMotionAvailable,
                               let calibratedRatio = heading.calibratedRatio {
                                infoRow(
                                    "姿态磁校准",
                                    "\(Int((calibratedRatio * 100).rounded()))%"
                                )
                            }
                            if let reason = heading.localizedRejectionReason {
                                infoRow("姿态回退原因", reason)
                            }
                        }
                        if let stepScale = result.stepLengthScale {
                            infoRow("步长比例", number(stepScale))
                        }
                        if let score = result.overallPFConfidenceScore,
                           let level = result.overallPFConfidenceLevel {
                            infoRow(
                                "PF 全程可靠度",
                                "\(level) · \(Int((score * 100).rounded()))%"
                            )
                        }
                        if let confidence = result.finalPFConfidence {
                            infoRow(
                                "PF 终点可靠度",
                                "\(confidence.localizedLevel) · \(Int((confidence.score * 100).rounded()))%"
                            )
                            infoRow(
                                "核心粒子范围",
                                "r80 \(meters(confidence.coreRadius80M ?? confidence.radius95M))"
                            )
                        }
                        if let mean = result.meanPFGlobalAmbiguity,
                           let maximum = result.maximumPFGlobalAmbiguity {
                            infoRow(
                                "全程位置歧义",
                                "平均 \(Int((mean * 100).rounded()))% · 峰值 \(Int((maximum * 100).rounded()))%"
                            )
                        }
                        if let health = result.finalLocalizationHealth {
                            infoRow("定位状态", health.localizedStatus)
                        }
                        if let events = result.localizationRecoveryEvents, !events.isEmpty {
                            infoRow("自动恢复", "\(events.count) 次")
                        }
                        if let interval = result.activeWalkInterval {
                            infoRow(
                                "有效行走",
                                String(format: "%.1f 秒", interval.duration)
                            )
                        }
                    }
                }

                Spacer(minLength: 10)
            }
            .padding(20)
        }
        .background(.ultraThinMaterial)
    }

    @ViewBuilder
    private var detail: some View {
        if let result = model.result {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(result.displayName)
                            .font(.title2.weight(.semibold))
                        Text(model.loadedFileName)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(action: model.exportCurrentResult) {
                        Label("导出 PNG/CSV…", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(.bordered)
                    .disabled(model.isBackendRunning)

                    Text("\(result.pfTrack.isEmpty ? result.pdrTrack.count : result.pfTrack.count) 个定位点")
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(.quaternary, in: Capsule())
                }

                if let health = result.finalLocalizationHealth,
                   health.status.lowercased() != "healthy" {
                    localizationHealthBanner(health, recoveryEvents: result.localizationRecoveryEvents ?? [])
                } else if let events = result.localizationRecoveryEvents, !events.isEmpty {
                    Label(
                        "本次运行已自动恢复 \(events.count) 次，当前定位状态稳定",
                        systemImage: "checkmark.shield.fill"
                    )
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.green)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .background(.green.opacity(0.09), in: RoundedRectangle(cornerRadius: 10))
                }

                TrajectoryCanvas(
                    result: result,
                    showTrueRoute: model.showTrueRoute,
                    showPDR: model.showPDR,
                    showPF: model.showPF,
                    showPFConfidence: model.showPFConfidence,
                    playbackProgress: model.playbackProgress
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)

                metrics(for: result)
            }
            .padding(18)
            .background(Color(nsColor: .windowBackgroundColor))
        } else {
            VStack(spacing: 12) {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.system(size: 48))
                    .foregroundStyle(.secondary)
                Text("尚未加载路径")
                    .font(.title3.weight(.semibold))
                Button("打开结果 JSON…", action: model.openResultFile)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func metrics(for result: PositioningResult) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: 12)], spacing: 12) {
            MetricCard(
                title: "PF 横向误差",
                value: meters(result.pfCrossTrackErrorStats?.mean),
                detail: "PDR \(meters(result.pdrCrossTrackErrorStats?.mean))",
                color: Color(red: 1.0, green: 0.78, blue: 0.16)
            )
            MetricCard(
                title: "PF 沿程误差",
                value: meters(result.pfAlongTrackErrorStats?.mean),
                detail: "PDR \(meters(result.pdrAlongTrackErrorStats?.mean))",
                color: .purple
            )
            MetricCard(
                title: "PF 转角误差",
                value: degrees(result.pfTurnAngleErrorStats?.mean),
                detail: "PDR \(degrees(result.pdrTurnAngleErrorStats?.mean))",
                color: .orange
            )
            MetricCard(
                title: "PF 终点误差",
                value: meters(result.pfErrorStats?.final),
                detail: "PDR \(meters(result.pdrErrorStats?.final))",
                color: Color(red: 1.0, green: 0.78, blue: 0.16)
            )
            MetricCard(
                title: "PF 路程比例",
                value: ratio(result.pfPathLengthRatio),
                detail: "PDR \(ratio(result.pdrPathLengthRatio))",
                color: Color(red: 0.10, green: 0.84, blue: 0.92)
            )
            MetricCard(
                title: "PF 闭合误差",
                value: meters(result.pfClosureErrorM),
                detail: "PDR \(meters(result.pdrClosureErrorM)) · \(value(result.stepsDetected)) 步",
                color: .green
            )
        }
    }

    private func sidebarSection<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            content()
        }
    }

    private var importedDatasetPanel: some View {
        VStack(alignment: .leading, spacing: 9) {
            Button(action: model.chooseImportedDatasetDirectory) {
                Label(
                    model.importedDataset == nil ? "选择采集文件夹…" : "更换采集文件夹…",
                    systemImage: "folder.badge.plus"
                )
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .disabled(model.isBackendRunning)

            if let dataset = model.importedDataset {
                Label(dataset.directoryURL.lastPathComponent, systemImage: "checkmark.circle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.green)
                Text(dataset.directoryURL.path)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .textSelection(.enabled)

                ForEach(dataset.streams) { stream in
                    HStack {
                        Text(stream.kind.title)
                        Spacer()
                        Text(
                            "\(stream.validRowCount) 行 · "
                                + String(format: "%.1f Hz", stream.sampleRateHz)
                        )
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                    .font(.caption2)
                }

                DisclosureGroup("数据质量 · \(dataset.quality.grade.title)") {
                    VStack(alignment: .leading, spacing: 6) {
                        Label(
                            String(
                                format: "自动行走区间 %.2f–%.2f 秒，保留约 %d/%d 帧",
                                dataset.activeInterval.startTime,
                                dataset.activeInterval.endTime,
                                dataset.activeFrameCount,
                                dataset.estimatedFrameCount
                            ),
                            systemImage: "figure.walk.motion"
                        )
                        Text(
                            String(
                                format: "磁场中位数 %.1f µT · 范围 %.1f–%.1f µT · 突变 %d 次",
                                dataset.quality.magneticMedianUT,
                                dataset.quality.magneticRangeUT.lowerBound,
                                dataset.quality.magneticRangeUT.upperBound,
                                dataset.quality.magneticSpikeCount
                            )
                        )
                        ForEach(dataset.quality.messages, id: \.self) { message in
                            Label(message, systemImage: "info.circle")
                        }
                    }
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
                }
                .font(.caption)
                .foregroundStyle(qualityColor(dataset.quality.grade))

                if !dataset.spatialAnchors.isEmpty {
                    Label(
                        "检测到 \(dataset.spatialAnchors.count) 个坐标锚点 · "
                            + (dataset.coordinateFrame ?? "未命名坐标系"),
                        systemImage: "mappin.and.ellipse"
                    )
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(dataset.spatialAnchors.count >= 2 ? .green : .orange)
                }

                Divider()
                Text("数据集名称")
                    .font(.caption2.weight(.semibold))
                TextField("例如 route3_run1", text: $model.customDatasetName)
                    .textFieldStyle(.roundedBorder)
                    .disabled(model.isBackendRunning)

                Text("真实路线坐标（米）")
                    .font(.caption2.weight(.semibold))
                TextField(
                    "例如 1.44,0.55; 1.44,8.25; 5.28,8.25",
                    text: $model.customRouteText,
                    axis: .vertical
                )
                .lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
                .disabled(model.isBackendRunning)
                Text("白色“真实路线”只会按这里的坐标顺序连线，不是传感器自动还原的轨迹；请确认点位顺序与实际行走一致。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                if let issue = model.routeGeometryIssue {
                    Label(issue, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.red)
                } else if DatasetValidator.normalizedRouteText(model.customRouteText) != nil {
                    Label("路线点序检查通过", systemImage: "checkmark.circle.fill")
                        .font(.caption2)
                        .foregroundStyle(.green)
                }

                Text("初始航向（可选，数学角度）")
                    .font(.caption2.weight(.semibold))
                TextField("留空则由路线前两个点计算", text: $model.customInitialHeadingText)
                    .textFieldStyle(.roundedBorder)
                    .disabled(model.isBackendRunning)

                Text("地磁地图")
                    .font(.caption2.weight(.semibold))
                Text("定位使用侧栏所选坐标地磁图；建图优先使用 SpatialEvents.csv 的已知锚点。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Button("重新校验", action: model.revalidateImportedDataset)
                .font(.caption)
                .disabled(model.isBackendRunning)

                let routeValidation = model.routeMapValidation
                if routeValidation.wasChecked {
                    if routeValidation.isValid, let bounds = routeValidation.bounds {
                        Label(
                            String(
                                format: "路线已通过地图范围检查 · x %.2f–%.2f，y %.2f–%.2f",
                                bounds.minX,
                                bounds.maxX,
                                bounds.minY,
                                bounds.maxY
                            ),
                            systemImage: "checkmark.shield.fill"
                        )
                        .font(.caption2)
                        .foregroundStyle(.green)
                    } else {
                        ForEach(routeValidation.invalidPoints) { point in
                            Label(
                                String(
                                    format: "路线第 %d 点 (%.3f, %.3f) 超出地图范围",
                                    point.index,
                                    point.x,
                                    point.y
                                ),
                                systemImage: "exclamationmark.octagon.fill"
                            )
                            .font(.caption2)
                            .foregroundStyle(.red)
                        }
                    }
                }

                ForEach(dataset.warnings, id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
            }

            Label(
                model.importedDatasetStatus,
                systemImage: model.importedDatasetReady
                    ? "checkmark.seal.fill"
                    : "info.circle.fill"
            )
            .font(.caption2)
            .foregroundStyle(model.importedDatasetReady ? .green : .secondary)
        }
    }

    private var algorithmParameterPanel: some View {
        DisclosureGroup("下次运行的算法参数") {
            VStack(alignment: .leading, spacing: 11) {
                Text("这里只影响下一次计算；加载历史结果不会改动这些设置。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                Picker(
                    "预设",
                    selection: Binding(
                        get: { model.selectedAlgorithmPreset },
                        set: { preset in model.applyAlgorithmPreset(preset) }
                    )
                ) {
                    ForEach(AlgorithmPreset.allCases) { preset in
                        Text(preset.title).tag(preset)
                    }
                }
                .disabled(model.isBackendRunning)

                Text(model.selectedAlgorithmPreset.detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                Divider()

                parameterLabel("PF 轨迹平滑", value: model.algorithmSettings.smoothingMode.title)
                Picker(
                    "PF 轨迹平滑",
                    selection: algorithmBinding(\.smoothingMode)
                ) {
                    ForEach(AlgorithmSettings.SmoothingMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .labelsHidden()
                .disabled(model.isBackendRunning)

                if model.algorithmSettings.smoothingMode != .none {
                    parameterLabel("平滑权重 α", value: number(model.algorithmSettings.smoothingAlpha))
                    Slider(
                        value: algorithmBinding(\.smoothingAlpha),
                        in: 0...0.9,
                        step: 0.05
                    )
                    .disabled(model.isBackendRunning)
                }

                parameterLabel(
                    "航向吸附",
                    value: model.algorithmSettings.headingSnapDegrees > 0
                        ? "\(number(model.algorithmSettings.headingSnapDegrees))°"
                        : "自适应直线"
                )
                Slider(
                    value: algorithmBinding(\.headingSnapDegrees),
                    in: 0...90,
                    step: 5
                )
                .disabled(model.isBackendRunning)

                parameterLabel("步长比例", value: number(model.algorithmSettings.stepLengthScale))
                Slider(
                    value: algorithmBinding(\.stepLengthScale),
                    in: 0.7...1.3,
                    step: 0.01
                )
                .disabled(model.isBackendRunning)

                Toggle(
                    "PF 联合估计步长与航向偏差",
                    isOn: algorithmBinding(\.jointCalibrationEnabled)
                )
                .font(.caption)
                .disabled(model.isBackendRunning)

                Toggle(
                    "实验性三轴矢量地图",
                    isOn: algorithmBinding(\.vectorMapEnabled)
                )
                .font(.caption)
                .disabled(true)
                Text("原生版第一阶段使用稳定的标量地图；三轴矢量地图将在完成原生标定后启用。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                Button("恢复当前优化基线", action: model.resetAlgorithmSettings)
                    .font(.caption)
                    .disabled(model.isBackendRunning)
            }
            .padding(.top, 7)
        }
        .font(.caption)
    }

    private func algorithmBinding<Value>(
        _ keyPath: WritableKeyPath<AlgorithmSettings, Value>
    ) -> Binding<Value> {
        Binding(
            get: { model.algorithmSettings[keyPath: keyPath] },
            set: { model.updateAlgorithmSetting(keyPath, to: $0) }
        )
    }

    private func parameterLabel(_ title: String, value: String) -> some View {
        HStack {
            Text(title)
            Spacer()
            Text(value)
                .monospacedDigit()
                .foregroundStyle(.secondary)
        }
        .font(.caption2)
    }

    private func qualityColor(_ grade: CaptureQualityGrade) -> Color {
        switch grade {
        case .usable: .green
        case .attention: .orange
        case .notRecommended: .red
        }
    }

    private func localizationHealthColor(_ status: String) -> Color {
        switch status.lowercased() {
        case "healthy": .green
        case "degraded": .orange
        case "ambiguous": .orange
        case "recovering": .blue
        default: .red
        }
    }

    private func localizationHealthBanner(
        _ health: LocalizationHealthSample,
        recoveryEvents: [LocalizationHealthSample]
    ) -> some View {
        let color = localizationHealthColor(health.status)
        let title = health.localizedAction ?? "定位\(health.localizedStatus)"
        let detail = "可靠度 \(Int((health.score * 100).rounded()))%"
            + " · 连续低可靠 \(health.lowConfidenceStreak) 步"
            + (recoveryEvents.isEmpty ? "" : " · 已恢复 \(recoveryEvents.count) 次")
        return HStack(alignment: .top, spacing: 10) {
            Image(
                systemName: health.status == "lost"
                    ? "location.slash.fill"
                    : health.status == "degraded"
                        ? "sensor.tag.radiowaves.forward.fill"
                        : "exclamationmark.triangle.fill"
            )
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.caption.weight(.semibold))
                Text(detail).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
        }
        .foregroundStyle(color)
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(color.opacity(0.09), in: RoundedRectangle(cornerRadius: 10))
    }

    private func routeToggle(_ title: String, color: Color, isOn: Binding<Bool>) -> some View {
        Toggle(isOn: isOn) {
            HStack(spacing: 8) {
                Capsule()
                    .fill(color)
                    .frame(width: 22, height: 4)
                    .overlay(Capsule().stroke(.black.opacity(0.18), lineWidth: 0.5))
                Text(title)
            }
        }
        .toggleStyle(.switch)
    }

    private func infoRow(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            Text(value).lineLimit(1)
        }
        .font(.caption)
    }

    private func meters(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.2f m", value)
    }

    private func ratio(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.0f%%", value * 100)
    }

    private func degrees(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.1f°", value)
    }

    private func number(_ value: Double) -> String {
        String(format: value.rounded() == value ? "%.0f" : "%.2f", value)
    }

    private func value(_ value: Int?) -> String {
        value.map(String.init) ?? "—"
    }

    private func runDate(_ date: Date) -> String {
        date.formatted(date: .abbreviated, time: .shortened)
    }
}

private struct MagneticMapQualitySheet: View {
    let document: GenericMagneticMapDocument
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("二维磁图覆盖与质量")
                        .font(.title2.weight(.semibold))
                    Text(document.name + " · " + document.sourceLabel)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }

            if let quality = document.qualityReport {
                HStack(spacing: 10) {
                    qualityMetric("综合质量", quality.grade.rawValue)
                    qualityMetric("有效覆盖", "\(Int((quality.coverageRatio * 100).rounded()))%")
                    qualityMetric("有效/总网格", "\(quality.coveredCellCount)/\(quality.expectedCellCount)")
                    qualityMetric("每格样本中位数", "\(quality.medianObservationCount)")
                }

                MagneticMapHeatmap(report: quality)
                    .frame(minHeight: 300)

                HStack(spacing: 18) {
                    legend("质量良好", .green)
                    legend("单一方向", .blue)
                    legend("样本不足", .orange)
                    legend("高方差", .red)
                    legend("缺失/障碍", .gray.opacity(0.35))
                }
                .font(.caption)

                VStack(alignment: .leading, spacing: 6) {
                    ForEach(quality.messages, id: \.self) { message in
                        Label(message, systemImage: "info.circle")
                    }
                    Text("灰色空洞不会被 PF 当作可定位区域。桌子、沙发等固定障碍内部保留为空白是正确结果；重点补扫障碍物四周仍可行走的区域。")
                        .foregroundStyle(.secondary)
                }
                .font(.caption)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "square.grid.3x3")
                        .font(.system(size: 34))
                        .foregroundStyle(.secondary)
                    Text("没有二维网格质量数据")
                        .font(.headline)
                    Text("路线磁图不生成房间覆盖热力图。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .padding(20)
        .frame(minWidth: 780, minHeight: 600)
    }

    private func qualityMetric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold).monospacedDigit())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }

    private func legend(_ title: String, _ color: Color) -> some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 13, height: 13)
            Text(title)
        }
    }
}

private struct MagneticMapHeatmap: View {
    let report: MagneticMapQualityReport

    var body: some View {
        Canvas { context, size in
            let columns = max(report.maximumGridX - report.minimumGridX + 1, 1)
            let rows = max(report.maximumGridY - report.minimumGridY + 1, 1)
            let padding = 14.0
            let availableWidth = max(size.width - 2 * padding, 1)
            let availableHeight = max(size.height - 2 * padding, 1)
            let cellSize = min(availableWidth / Double(columns), availableHeight / Double(rows))
            let gridWidth = cellSize * Double(columns)
            let gridHeight = cellSize * Double(rows)
            let originX = (size.width - gridWidth) / 2
            let originY = (size.height - gridHeight) / 2

            for cell in report.cells {
                let column = cell.gridX - report.minimumGridX
                let row = report.maximumGridY - cell.gridY
                let rect = CGRect(
                    x: originX + Double(column) * cellSize + 0.7,
                    y: originY + Double(row) * cellSize + 0.7,
                    width: max(cellSize - 1.4, 1),
                    height: max(cellSize - 1.4, 1)
                )
                context.fill(
                    Path(roundedRect: rect, cornerRadius: min(cellSize * 0.15, 3)),
                    with: .color(color(for: cell.status))
                )
            }
        }
        .background(Color.black.opacity(0.82), in: RoundedRectangle(cornerRadius: 12))
        .accessibilityLabel("二维地磁图覆盖热力图")
    }

    private func color(for status: MagneticMapCellStatus) -> Color {
        switch status {
        case .missing: .gray.opacity(0.26)
        case .good: .green.opacity(0.88)
        case .sparse: .orange.opacity(0.92)
        case .singleDirection: .blue.opacity(0.88)
        case .noisy: .red.opacity(0.92)
        }
    }
}

private struct BackendLogView: View {
    let log: String
    let status: String
    let isRunning: Bool
    let copyAction: () -> Void
    let clearAction: () -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("定位算法运行日志")
                        .font(.title2.weight(.semibold))
                    Text(status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if isRunning {
                    ProgressView()
                        .controlSize(.small)
                }
                Button("复制", action: copyAction)
                Button("清空", action: clearAction)
                    .disabled(isRunning)
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }

            ScrollView([.horizontal, .vertical]) {
                Text(log.isEmpty ? "暂无日志" : log)
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
            }
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(20)
        .frame(minWidth: 760, minHeight: 500)
    }
}
