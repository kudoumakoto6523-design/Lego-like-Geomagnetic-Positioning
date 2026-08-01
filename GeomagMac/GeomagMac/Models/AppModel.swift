import AppKit
import Foundation

@MainActor
final class AppModel: ObservableObject {
    struct Sample: Identifiable, Hashable {
        let id: String
        let title: String
    }

    static let samples = [
        Sample(id: "v7_final_route1_run2", title: "Route 1 · Run 2"),
        Sample(id: "v7_final_route2_run1", title: "Route 2 · Run 1"),
        Sample(id: "v7_final_route2_run2", title: "Route 2 · Run 2"),
    ]

    static let runnableDatasets = [
        Sample(id: "route1_run2", title: "Route 1 · Run 2"),
        Sample(id: "route2_run1", title: "Route 2 · Run 1"),
        Sample(id: "route2_run2", title: "Route 2 · Run 2"),
    ]

    @Published var result: PositioningResult?
    @Published var selectedSampleID = samples[0].id
    @Published var loadedFileName = ""
    @Published var errorMessage: String?
    @Published var playbackProgress = 1.0
    @Published var showTrueRoute = true
    @Published var showPDR = true
    @Published var showPF = true
    @Published var selectedRunDatasetID = runnableDatasets[0].id
    @Published var projectDirectory: String
    @Published var isBackendRunning = false
    @Published var backendProgress = 0.0
    @Published var backendLog = ""
    @Published var backendStatus = "准备就绪"
    @Published var lastOutputPath = ""
    @Published private(set) var selectedAlgorithmPreset: AlgorithmPreset
    @Published private(set) var algorithmSettings: AlgorithmSettings

    private var backendProcess: Process?
    private var backendPipe: Pipe?
    private var backendWasCancelled = false

    private struct BackendLaunch {
        let executableURL: URL
        let currentDirectoryURL: URL
        let arguments: [String]
        let logHeader: String
    }

    var hasBundledBackend: Bool {
        bundledBackendExecutableURL != nil
    }

    var backendModeTitle: String {
        hasBundledBackend ? "内置算法后端" : "外部 Python（开发模式）"
    }

    init() {
        projectDirectory = UserDefaults.standard.string(forKey: "pythonProjectDirectory")
            ?? Self.defaultProjectDirectory()
        let restored = Self.restoreAlgorithmConfiguration()
        selectedAlgorithmPreset = restored.preset
        algorithmSettings = restored.settings
        loadBundledSample(id: selectedSampleID)
    }

    deinit {
        backendProcess?.terminate()
    }

    func loadBundledSample(id: String) {
        selectedSampleID = id
        guard let url = Bundle.main.url(
            forResource: id,
            withExtension: "json",
            subdirectory: "Samples"
        ) ?? Bundle.main.url(forResource: id, withExtension: "json") else {
            errorMessage = "找不到内置示例：\(id).json"
            return
        }
        load(url: url)
    }

    func openResultFile() {
        let panel = NSOpenPanel()
        panel.title = "打开地磁定位结果"
        panel.prompt = "打开"
        panel.allowedContentTypes = [.json]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false

        guard panel.runModal() == .OK, let url = panel.url else { return }
        load(url: url)
    }

    func chooseProjectDirectory() {
        let panel = NSOpenPanel()
        panel.title = "选择 Python 地磁定位项目"
        panel.prompt = "选择项目"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = false
        panel.allowsMultipleSelection = false
        if FileManager.default.fileExists(atPath: projectDirectory) {
            panel.directoryURL = URL(fileURLWithPath: projectDirectory)
        }

        guard panel.runModal() == .OK, let url = panel.url else { return }
        let mainFile = url.appendingPathComponent("main.py")
        guard FileManager.default.fileExists(atPath: mainFile.path) else {
            errorMessage = "所选目录中没有 main.py，请选择 Lego-like-Geomagnetic-Positioning 项目根目录。"
            return
        }
        projectDirectory = url.path
        UserDefaults.standard.set(projectDirectory, forKey: "pythonProjectDirectory")
        backendStatus = "Python 项目已连接"
    }

    func applyAlgorithmPreset(_ preset: AlgorithmPreset) {
        selectedAlgorithmPreset = preset
        if let settings = preset.settings {
            algorithmSettings = settings
        }
        persistAlgorithmConfiguration()
    }

    func updateAlgorithmSetting<Value>(
        _ keyPath: WritableKeyPath<AlgorithmSettings, Value>,
        to value: Value
    ) {
        algorithmSettings[keyPath: keyPath] = value
        selectedAlgorithmPreset = .custom
        persistAlgorithmConfiguration()
    }

    func resetAlgorithmSettings() {
        applyAlgorithmPreset(.optimized)
    }

    func runSelectedDataset() {
        guard !isBackendRunning else { return }

        do {
            let runDirectory = try createRunDirectory()
            let stamp = Self.fileTimestamp()
            let baseName = "\(selectedRunDatasetID)-\(stamp)"
            let jsonURL = runDirectory.appendingPathComponent(baseName).appendingPathExtension("json")
            let pngURL = runDirectory.appendingPathComponent(baseName).appendingPathExtension("png")

            guard let launch = resolveBackendLaunch(jsonURL: jsonURL, pngURL: pngURL) else {
                return
            }

            let process = Process()
            let pipe = Pipe()
            process.executableURL = launch.executableURL
            process.currentDirectoryURL = launch.currentDirectoryURL
            process.arguments = launch.arguments
            process.standardOutput = pipe
            process.standardError = pipe

            var environment = ProcessInfo.processInfo.environment
            let standardPath = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            environment["PATH"] = standardPath + ":" + (environment["PATH"] ?? "")
            environment["MPLBACKEND"] = "Agg"
            environment["PYTHONUNBUFFERED"] = "1"
            process.environment = environment

            backendProcess = process
            backendPipe = pipe
            backendWasCancelled = false
            backendLog = launch.logHeader
                + "数据: \(selectedRunDatasetID)\n"
                + "预设: \(selectedAlgorithmPreset.title)\n"
                + "参数: \(algorithmSettings.logSummary)\n\n"
            backendProgress = 0
            backendStatus = "正在计算 \(selectedRunDatasetID)…"
            isBackendRunning = true

            pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
                DispatchQueue.main.async {
                    self?.consumeBackendOutput(text)
                }
            }

            process.terminationHandler = { [weak self] finishedProcess in
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.backendPipe?.fileHandleForReading.readabilityHandler = nil
                    self.isBackendRunning = false
                    self.backendProcess = nil
                    self.backendPipe = nil

                    if self.backendWasCancelled {
                        self.backendStatus = "计算已取消"
                    } else if finishedProcess.terminationStatus == 0,
                              FileManager.default.fileExists(atPath: jsonURL.path) {
                        self.backendProgress = 1
                        self.backendStatus = "计算完成，结果已加载"
                        self.lastOutputPath = jsonURL.path
                        self.load(url: jsonURL)
                    } else {
                        self.backendStatus = "计算失败（退出码 \(finishedProcess.terminationStatus)）"
                        self.errorMessage = "Python 计算失败，请查看运行日志。"
                    }
                }
            }

            try process.run()
        } catch {
            backendProcess = nil
            backendPipe = nil
            isBackendRunning = false
            backendStatus = "无法启动算法后端"
            errorMessage = "无法启动算法后端：\(error.localizedDescription)"
        }
    }

    func cancelBackendRun() {
        guard let backendProcess, backendProcess.isRunning else { return }
        backendWasCancelled = true
        backendStatus = "正在取消…"
        backendProcess.terminate()
    }

    func revealLastOutput() {
        guard !lastOutputPath.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: lastOutputPath)])
    }

    func load(url: URL) {
        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder().decode(PositioningResult.self, from: data)
            result = decoded
            loadedFileName = url.lastPathComponent
            playbackProgress = 1.0
            errorMessage = nil
        } catch {
            errorMessage = "无法读取 \(url.lastPathComponent)：\(error.localizedDescription)"
        }
    }

    private func consumeBackendOutput(_ text: String) {
        let normalized = text.replacingOccurrences(of: "\r", with: "\n")
        backendLog += normalized
        if backendLog.count > 40_000 {
            backendLog = String(backendLog.suffix(40_000))
        }

        let pattern = #"\(\s*([0-9]+(?:\.[0-9]+)?)%\)"#
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return }
        let range = NSRange(normalized.startIndex..., in: normalized)
        let matches = expression.matches(in: normalized, range: range)
        guard let match = matches.last,
              let percentRange = Range(match.range(at: 1), in: normalized),
              let percent = Double(normalized[percentRange]) else { return }
        backendProgress = min(max(percent / 100, 0), 1)
    }

    private func resolvePythonExecutable(projectURL: URL) -> URL? {
        var candidates = [
            projectURL.appendingPathComponent(".venv/bin/python").path,
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]

        if let path = ProcessInfo.processInfo.environment["PATH"] {
            candidates += path.split(separator: ":").map {
                String($0) + "/python3"
            }
        }

        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            return URL(fileURLWithPath: candidate)
        }
        return nil
    }

    private var bundledBackendExecutableURL: URL? {
        guard let resourceURL = Bundle.main.resourceURL else { return nil }
        let executableURL = resourceURL
            .appendingPathComponent("GeomagBackend", isDirectory: true)
            .appendingPathComponent("GeomagBackend", isDirectory: false)
        guard FileManager.default.isExecutableFile(atPath: executableURL.path) else { return nil }
        return executableURL
    }

    private func resolveBackendLaunch(jsonURL: URL, pngURL: URL) -> BackendLaunch? {
        let algorithmArguments = [
            "--branch", "own",
            "--own", selectedRunDatasetID,
            "--no-show",
            "--output-json", jsonURL.path,
            "--output-png", pngURL.path,
        ] + algorithmSettings.commandLineArguments

        if let executableURL = bundledBackendExecutableURL {
            return BackendLaunch(
                executableURL: executableURL,
                currentDirectoryURL: executableURL.deletingLastPathComponent(),
                arguments: algorithmArguments,
                logHeader: "后端: 内置 GeomagBackend\n算法: xuml-v7-optimization 修改版\n"
            )
        }

        let projectURL = URL(fileURLWithPath: projectDirectory, isDirectory: true)
        let mainURL = projectURL.appendingPathComponent("main.py")
        guard FileManager.default.fileExists(atPath: mainURL.path) else {
            errorMessage = "App 中没有内置算法后端，同时找不到 \(mainURL.path)。请先构建内置后端，或重新选择 Python 项目目录。"
            return nil
        }
        guard let pythonURL = resolvePythonExecutable(projectURL: projectURL) else {
            errorMessage = "App 中没有内置算法后端，也找不到可用的 Python 3。请先构建内置后端，或在原项目中创建 .venv。"
            return nil
        }

        return BackendLaunch(
            executableURL: pythonURL,
            currentDirectoryURL: projectURL,
            arguments: ["-u", mainURL.path] + algorithmArguments,
            logHeader: "后端: 外部 Python（开发模式）\nPython: \(pythonURL.path)\n项目: \(projectURL.path)\n"
        )
    }

    private func createRunDirectory() throws -> URL {
        let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        let directory = applicationSupport
            .appendingPathComponent("GeomagMac", isDirectory: true)
            .appendingPathComponent("Runs", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        return directory
    }

    private static func defaultProjectDirectory() -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home
            .appendingPathComponent("dachuang", isDirectory: true)
            .appendingPathComponent("Lego-like-Geomagnetic-Positioning", isDirectory: true)
            .path
    }

    private func persistAlgorithmConfiguration() {
        let encoder = JSONEncoder()
        if let data = try? encoder.encode(algorithmSettings) {
            UserDefaults.standard.set(data, forKey: "algorithmSettings")
        }
        UserDefaults.standard.set(selectedAlgorithmPreset.rawValue, forKey: "algorithmPreset")
    }

    private static func restoreAlgorithmConfiguration() -> (
        preset: AlgorithmPreset,
        settings: AlgorithmSettings
    ) {
        let defaults = UserDefaults.standard
        let preset = defaults.string(forKey: "algorithmPreset")
            .flatMap(AlgorithmPreset.init(rawValue:)) ?? .optimized
        if preset != .custom, let settings = preset.settings {
            return (preset, settings)
        }
        if let data = defaults.data(forKey: "algorithmSettings"),
           let settings = try? JSONDecoder().decode(AlgorithmSettings.self, from: data) {
            return (preset, settings)
        }
        return (.optimized, .optimized)
    }

    private static func fileTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter.string(from: Date())
    }
}
