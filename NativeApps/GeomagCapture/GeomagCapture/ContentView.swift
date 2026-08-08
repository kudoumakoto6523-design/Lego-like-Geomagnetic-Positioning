import SwiftUI

struct ContentView: View {
    @ObservedObject var recorder: MotionRecorder

    @State private var datasetName = Self.defaultDatasetName()
    @State private var routeText = ""
    @State private var initialHeadingText = ""
    @State private var coordinateFrameText = ""
    @State private var startXText = "0"
    @State private var startYText = "0"
    @State private var eventLabel = ""
    @State private var anchorXText = "0"
    @State private var anchorYText = "0"
    @State private var mappingMode = true
    @State private var roomWidthText = "6"
    @State private var roomHeightText = "8"
    @State private var scanSpacingText = "0.5"
    @State private var isExporting = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    statusCard
                    configurationCard
                    liveMotionCard
                    spatialEventCard
                    controls
                    exportCard
                    guidanceCard
                }
                .padding(16)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Geomag Capture")
            .navigationBarTitleDisplayMode(.inline)
            .alert(
                "出现问题",
                isPresented: Binding(
                    get: { recorder.errorMessage != nil },
                    set: { if !$0 { recorder.errorMessage = nil } }
                )
            ) {
                Button("好", role: .cancel) {}
            } message: {
                Text(recorder.errorMessage ?? "未知错误")
            }
            .fileExporter(
                isPresented: $isExporting,
                document: recorder.completedDocument,
                contentType: .geomagCapture,
                defaultFilename: recorder.suggestedFileName
            ) { result in
                switch result {
                case .success:
                    recorder.markExported()
                case let .failure(error):
                    recorder.reportExportError(error)
                }
            }
        }
    }

    private var statusCard: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(recorder.isRecording ? Color.red.opacity(0.14) : Color.blue.opacity(0.12))
                    .frame(width: 52, height: 52)
                Image(systemName: recorder.isRecording ? "record.circle.fill" : "iphone.gen3.radiowaves.left.and.right")
                    .font(.system(size: 25, weight: .semibold))
                    .foregroundStyle(recorder.isRecording ? .red : .blue)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(recorder.statusMessage)
                    .font(.headline)
                Text(
                    recorder.isRecording
                        ? "\(duration(recorder.elapsedTime)) · \(number(recorder.observedRateHz)) Hz"
                        : "Core Motion · \(recorder.sensorAvailabilityText)"
                )
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .cardStyle()
    }

    private var configurationCard: some View {
        VStack(alignment: .leading, spacing: 13) {
            Label("采集配置", systemImage: "slider.horizontal.3")
                .font(.headline)

            Toggle("房间建图模式", isOn: $mappingMode)
                .font(.subheadline.weight(.semibold))

            if mappingMode {
                HStack(spacing: 8) {
                    labeledField("宽 X（米）", hint: "") {
                        TextField("6", text: $roomWidthText).keyboardType(.decimalPad)
                    }
                    labeledField("高 Y（米）", hint: "") {
                        TextField("8", text: $roomHeightText).keyboardType(.decimalPad)
                    }
                    labeledField("线距（米）", hint: "") {
                        TextField("0.5", text: $scanSpacingText).keyboardType(.decimalPad)
                    }
                }
                Button {
                    generateRoomScanTask()
                } label: {
                    Label("生成横向 + 纵向蛇形扫描任务", systemImage: "point.3.connected.trianglepath.dotted")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                Text("坐标系默认左下角为 (0,0)。到达每个扫描线端点时点击“下个路线点”。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            labeledField("数据集名称", hint: "例如 route3_run1") {
                TextField("route3_run1", text: $datasetName)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }

            labeledField("统一空间坐标系（必填）", hint: "同一楼层所有采集必须完全一致，例如 building-a-floor-1") {
                TextField("building-a-floor-1", text: $coordinateFrameText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }

            HStack(spacing: 10) {
                labeledField("起点 X（米）", hint: "楼层统一坐标") {
                    TextField("0", text: $startXText)
                        .keyboardType(.numbersAndPunctuation)
                }
                labeledField("起点 Y（米）", hint: "楼层统一坐标") {
                    TextField("0", text: $startYText)
                        .keyboardType(.numbersAndPunctuation)
                }
            }

            labeledField("真实路线坐标（可选，米）", hint: "必须使用米：180 cm 请填 1.80；到 Mac 后也可以再填") {
                TextField(
                    "1.44,0.55; 1.44,6.05; 4.32,6.05",
                    text: $routeText,
                    axis: .vertical
                )
                .lineLimit(2...4)
                    .keyboardType(.numbersAndPunctuation)
            }

            labeledField("初始航向（数学角度）", hint: "0°=+X，90°=+Y，-90°=-Y；填写路线时可留空自动计算") {
                TextField("90", text: $initialHeadingText)
                    .keyboardType(.numbersAndPunctuation)
            }
        }
        .textFieldStyle(.roundedBorder)
        .disabled(recorder.isRecording || recorder.isPreparingExport)
        .cardStyle()
    }

    private var spatialEventCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("空间锚点与转角", systemImage: "mappin.and.ellipse")
                    .font(.headline)
                Spacer()
                Text("\(recorder.spatialEventCount) 条")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            Text(recorder.lastSpatialEventMessage)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            if recorder.routeAnchorTotal > 0 {
                ProgressView(value: recorder.routeCoverage)
                Text("扫描锚点 \(recorder.routeAnchorCompleted)/\(recorder.routeAnchorTotal)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            if recorder.anchorReminderNeeded {
                Label("距离上一个锚点已超过 25 秒，请确认是否漏记扫描线端点。", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
            }

            if recorder.isRecording {
                TextField("事件名称（可选）", text: $eventLabel)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)

                HStack(spacing: 10) {
                    TextField("锚点 X（米）", text: $anchorXText)
                        .keyboardType(.numbersAndPunctuation)
                    TextField("锚点 Y（米）", text: $anchorYText)
                        .keyboardType(.numbersAndPunctuation)
                }
                .textFieldStyle(.roundedBorder)

                HStack(spacing: 10) {
                    Button {
                        recorder.markTurn(label: eventLabel)
                        eventLabel = ""
                    } label: {
                        Label("标记转角", systemImage: "arrow.turn.down.right")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)

                    Button {
                        recorder.markAnchor(
                            label: eventLabel,
                            xText: anchorXText,
                            yText: anchorYText
                        )
                        eventLabel = ""
                    } label: {
                        Label("记录坐标锚点", systemImage: "mappin.circle.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                }

                if recorder.hasNextRouteAnchor {
                    Button {
                        recorder.markNextRouteAnchor(label: eventLabel)
                        eventLabel = ""
                    } label: {
                        Label(
                            recorder.nextRouteAnchorDescription,
                            systemImage: "point.topleft.down.to.point.bottomright.curvepath"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.indigo)
                }
            } else {
                Text("开始采集后，可在经过已知坐标或转弯中心时立即标记。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .cardStyle()
    }

    private var liveMotionCard: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack {
                Label("实时传感器", systemImage: "gyroscope")
                    .font(.headline)
                Spacer()
                if recorder.counts.deviceMotion > 0 {
                    Label(
                        recorder.phoneIsFlat ? "姿态平稳" : "手机倾斜",
                        systemImage: recorder.phoneIsFlat ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
                    )
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(recorder.phoneIsFlat ? .green : .orange)
                }
            }

            HStack(spacing: 10) {
                metric("横滚", value: degrees(recorder.rollDegrees))
                metric("俯仰", value: degrees(recorder.pitchDegrees))
                metric("设备 yaw", value: degrees(recorder.yawDegrees))
            }

            HStack(spacing: 10) {
                metric(
                    "全局航向",
                    value: recorder.globalHeadingDegrees.map(degrees) ?? "—"
                )
                Text("全局航向 = 已知起始方向 + Core Motion 相对 yaw")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            HStack(spacing: 10) {
                metric("磁场", value: recorder.counts.magnetometer > 0
                    ? "\(number(recorder.magneticMagnitude)) µT" : "—")
                metric("磁场校准", value: recorder.magneticAccuracy.rawValue)
            }

            Divider()

            VStack(spacing: 8) {
                sampleRow("加速度计", count: recorder.counts.accelerometer)
                sampleRow("陀螺仪", count: recorder.counts.gyroscope)
                sampleRow("磁力计", count: recorder.counts.magnetometer)
                sampleRow("Device Motion", count: recorder.counts.deviceMotion)
            }
        }
        .cardStyle()
    }

    private var controls: some View {
        HStack(spacing: 12) {
            if recorder.isRecording {
                Button {
                    recorder.togglePause()
                } label: {
                    Label(
                        recorder.isPaused ? "恢复建图" : "暂停建图",
                        systemImage: recorder.isPaused ? "play.fill" : "pause.fill"
                    )
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button {
                    Task { await recorder.stopRecording() }
                } label: {
                    Label("停止并保存", systemImage: "stop.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
            } else {
                Button {
                    anchorXText = startXText
                    anchorYText = startYText
                    recorder.startRecording(
                        datasetName: datasetName,
                        routeText: routeText,
                        initialHeadingText: initialHeadingText,
                        coordinateFrameText: coordinateFrameText,
                        startXText: startXText,
                        startYText: startYText
                    )
                } label: {
                    Label("开始采集", systemImage: "record.circle")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(recorder.isPreparingExport)
            }
        }
        .controlSize(.large)
    }

    @ViewBuilder
    private var exportCard: some View {
        if recorder.isPreparingExport {
            HStack(spacing: 12) {
                ProgressView()
                Text("正在生成 CSV 和元数据…")
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .cardStyle()
        } else if recorder.completedDocument != nil {
            VStack(alignment: .leading, spacing: 10) {
                Label("采集包已生成", systemImage: "checkmark.seal.fill")
                    .font(.headline)
                    .foregroundStyle(.green)
                Text("包含传感器 CSV、DeviceMotion.csv、统一空间坐标和锚点/转角事件。")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Button {
                    isExporting = true
                } label: {
                    Label("导出 .geomagcapture…", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
            .cardStyle()
        }
    }

    private var guidanceCard: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label("采集提示", systemImage: "figure.walk")
                .font(.headline)
            guidance("手机正面朝上、前端朝向行走方向。")
            guidance("开始后静止约 3 秒，再匀速行走。")
            guidance("到达终点后保持静止约 3 秒，再点击停止。")
            guidance("经过已知坐标时点“记录坐标锚点”；转弯中心至少点“标记转角”。")
            guidance("房间建图建议横向、纵向各扫描一遍，线距 0.4～0.6 m；同一区域重复 2～3 次。")
            guidance("采集期间保持屏幕点亮，不要切到后台。")
            Text("模拟器没有运动传感器；请在 Xcode 中选择已连接的真实 iPhone。")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.top, 3)
        }
        .cardStyle()
    }

    private func labeledField<Content: View>(
        _ title: String,
        hint: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.subheadline.weight(.medium))
            content()
            Text(hint).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func generateRoomScanTask() {
        guard let width = Double(roomWidthText), let height = Double(roomHeightText),
              let spacing = Double(scanSpacingText),
              width > 0.5, height > 0.5, 0.2...1.0 ~= spacing else {
            recorder.errorMessage = "房间宽高必须大于 0.5 m，扫描线间距应为 0.2～1.0 m。"
            return
        }
        var points: [(Double, Double)] = []
        let horizontalLines = Int(ceil(height / spacing))
        for line in 0...horizontalLines {
            let y = min(Double(line) * spacing, height)
            points.append(line.isMultiple(of: 2) ? (0, y) : (width, y))
            points.append(line.isMultiple(of: 2) ? (width, y) : (0, y))
        }
        let verticalLines = Int(ceil(width / spacing))
        for line in 0...verticalLines {
            let x = min(Double(line) * spacing, width)
            points.append(line.isMultiple(of: 2) ? (x, 0) : (x, height))
            points.append(line.isMultiple(of: 2) ? (x, height) : (x, 0))
        }
        routeText = points.map { String(format: "%.3f,%.3f", $0.0, $0.1) }
            .joined(separator: "; ")
        startXText = "0"
        startYText = "0"
        initialHeadingText = "0"
        anchorXText = "0"
        anchorYText = "0"
    }

    private func metric(_ title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 10))
    }

    private func sampleRow(_ title: String, count: Int) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            Text("\(count) 样本").monospacedDigit()
        }
        .font(.subheadline)
    }

    private func guidance(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "checkmark.circle")
                .foregroundStyle(.blue)
            Text(text).font(.subheadline)
        }
    }

    private func duration(_ seconds: Double) -> String {
        let total = max(Int(seconds.rounded()), 0)
        return String(format: "%02d:%02d", total / 60, total % 60)
    }

    private func number(_ value: Double) -> String {
        String(format: "%.1f", value)
    }

    private func degrees(_ value: Double) -> String {
        recorder.counts.deviceMotion > 0 ? "\(number(value))°" : "—"
    }

    private static func defaultDatasetName() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmm"
        return "route_new_\(formatter.string(from: Date()))"
    }
}

private extension View {
    func cardStyle() -> some View {
        self
            .padding(16)
            .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 16))
    }
}

#Preview {
    ContentView(recorder: MotionRecorder())
}
