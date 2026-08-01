import Combine
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var isPlaying = false

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
                    Label("GeomagMac", systemImage: "location.viewfinder")
                        .font(.title2.weight(.bold))
                    Text("地磁定位路径查看器")
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
                        Image(systemName: model.hasBundledBackend ? "shippingbox.fill" : "wrench.and.screwdriver.fill")
                        Text(model.backendModeTitle)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(model.hasBundledBackend ? .green : .orange)

                    Picker("采集数据", selection: $model.selectedRunDatasetID) {
                        ForEach(AppModel.runnableDatasets) { sample in
                            Text(sample.title).tag(sample.id)
                        }
                    }
                    .labelsHidden()
                    .disabled(model.isBackendRunning)

                    if model.hasBundledBackend {
                        Text("使用 App 内置的修改版算法、运行环境与采集数据，无需配置 Python。")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(alignment: .leading, spacing: 5) {
                            Text("PYTHON 项目")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.tertiary)
                            Text(model.projectDirectory)
                                .font(.caption2.monospaced())
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                                .textSelection(.enabled)
                        }

                        Button(action: model.chooseProjectDirectory) {
                            Label("选择项目目录…", systemImage: "folder.badge.gearshape")
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .disabled(model.isBackendRunning)
                    }

                    algorithmParameterPanel

                    if model.isBackendRunning || model.backendProgress > 0 {
                        ProgressView(value: model.backendProgress)
                        Text("\(Int(model.backendProgress * 100))% · \(model.backendStatus)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        Text(model.backendStatus)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    HStack {
                        Button(action: model.runSelectedDataset) {
                            Label("开始计算", systemImage: "play.circle.fill")
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isBackendRunning)

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
                            if !model.lastOutputPath.isEmpty && !model.isBackendRunning {
                                Button("在 Finder 中显示结果", action: model.revealLastOutput)
                                    .font(.caption)
                            }
                        }
                        .font(.caption)
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
                        if let headingSnap = result.headingSnapDegrees {
                            infoRow("航向约束", headingSnap == 0 ? "关闭" : "\(number(headingSnap))°")
                        }
                        if let stepScale = result.stepLengthScale {
                            infoRow("步长比例", number(stepScale))
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
                    Text("\(result.pfTrack.count) 个定位点")
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(.quaternary, in: Capsule())
                }

                TrajectoryCanvas(
                    result: result,
                    showTrueRoute: model.showTrueRoute,
                    showPDR: model.showPDR,
                    showPF: model.showPF,
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
        HStack(spacing: 12) {
            MetricCard(
                title: "PF 平均误差",
                value: meters(result.pfErrorStats?.mean),
                detail: "P95 \(meters(result.pfErrorStats?.p95))",
                color: Color(red: 1.0, green: 0.78, blue: 0.16)
            )
            MetricCard(
                title: "PF 终点误差",
                value: meters(result.pfErrorStats?.final),
                detail: "中位数 \(meters(result.pfErrorStats?.median))",
                color: Color(red: 1.0, green: 0.78, blue: 0.16)
            )
            MetricCard(
                title: "PDR 平均误差",
                value: meters(result.pdrErrorStats?.mean),
                detail: "P95 \(meters(result.pdrErrorStats?.p95))",
                color: Color(red: 0.10, green: 0.84, blue: 0.92)
            )
            MetricCard(
                title: "检测步数",
                value: value(result.stepsDetected),
                detail: "\(result.sensorFramesUsed ?? 0) 个传感器帧",
                color: .green
            )
        }
        .fixedSize(horizontal: false, vertical: true)
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

    private var algorithmParameterPanel: some View {
        DisclosureGroup("算法参数") {
            VStack(alignment: .leading, spacing: 11) {
                Picker(
                    "预设",
                    selection: Binding(
                        get: { model.selectedAlgorithmPreset },
                        set: model.applyAlgorithmPreset
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
                    "航向网格约束",
                    value: model.algorithmSettings.headingSnapDegrees == 0
                        ? "关闭"
                        : "\(number(model.algorithmSettings.headingSnapDegrees))°"
                )
                Picker(
                    "航向网格约束",
                    selection: algorithmBinding(\.headingSnapDegrees)
                ) {
                    Text("关闭（实际场景）").tag(0.0)
                    Text("45° 网格").tag(45.0)
                    Text("90° 网格").tag(90.0)
                }
                .labelsHidden()
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
                .disabled(model.isBackendRunning)

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

    private func number(_ value: Double) -> String {
        String(format: value.rounded() == value ? "%.0f" : "%.2f", value)
    }

    private func value(_ value: Int?) -> String {
        value.map(String.init) ?? "—"
    }
}
