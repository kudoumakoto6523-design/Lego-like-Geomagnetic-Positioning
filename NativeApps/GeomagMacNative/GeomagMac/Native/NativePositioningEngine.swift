import Foundation

/// Pure-Swift positioning pipeline. It deliberately has no Process, Python,
/// NumPy, or network dependency, so the same computation runs inside the app.
enum NativePositioningEngine {
    struct Request: Sendable {
        let datasetKey: String
        let datasetDirectory: URL
        let route: [XYPoint]
        let initialHeadingDegrees: Double?
        let activeStartTime: Double?
        let activeEndTime: Double?
        let settings: AlgorithmSettings
        let magneticMap: GenericMagneticMapDocument
    }

    enum EngineError: LocalizedError, Equatable {
        case missingResource(String)
        case malformedCSV(String)
        case insufficientSensorOverlap
        case noSteps
        case invalidMap
        case invalidReferenceRoute
        case insufficientMapSamples
        case mapSourceCannotLocateItself

        var errorDescription: String? {
            switch self {
            case let .missingResource(name): "缺少原生算法资源：\(name)"
            case let .malformedCSV(name): "无法解析传感器文件：\(name)"
            case .insufficientSensorOverlap: "三路传感器没有足够的重叠记录。"
            case .noSteps: "没有检测到有效步态，请检查采集数据或有效行走区间。"
            case .invalidMap: "内置地磁地图格式不正确。"
            case .invalidReferenceRoute: "建立磁图至少需要两个不同的真实路线坐标点。"
            case .insufficientMapSamples: "参考采集没有产生足够的有效步级磁场样本。"
            case .mapSourceCannotLocateItself: "建图采集不能用于自我定位验证。"
            }
        }
    }

    private struct Vector3: Sendable {
        let x: Double
        let y: Double
        let z: Double
        var magnitude: Double { sqrt(x * x + y * y + z * z) }
    }

    private struct TimedVector: Sendable {
        let time: Double
        let value: Vector3
    }

    private struct TimedHeading: Sendable {
        let time: Double
        let yawRadians: Double
        let magneticAccuracy: Int
        let alignedMagneticVector: Vector3
    }

    private struct Frame: Sendable {
        let time: Double
        let acceleration: Vector3
        let gyroscope: Vector3
        let magnetometer: Vector3
        let deviceHeading: DeviceHeadingFrame?
        let alignedMagneticVector: Vector3?
    }

    private struct Step: Sendable {
        let frameIndex: Int
        let time: Double
        let length: Double
        let heading: Double
        let sensorHeading: Double
        let magneticMagnitude: Double
        let alignedMagneticVector: Vector3?
        let segmentIndex: Int?
    }

    private struct TurnRegion: Sendable {
        let onsetIndex: Int
        let completionIndex: Int
        let onsetTime: Double
        let completionTime: Double
        let signedAngleDegrees: Double
    }

    private struct Particle {
        var x: Double
        var y: Double
        var headingBias: Double
        var stepScale: Double
        var magneticBias: Double
        var magneticBiasX: Double
        var magneticBiasY: Double
        var magneticBiasZ: Double
        var previousMapValue: Double
        var weight: Double
    }

    private struct GridKey: Hashable {
        let x: Int
        let y: Int
    }

    private struct MagneticMap: Sendable {
        let rows: Int
        let columns: Int
        let values: [Float]
        let minX: Double
        let maxX: Double
        let minY: Double
        let maxY: Double
        let flipY: Bool
        let scatteredSamples: [GenericMagneticMapSample]
        let supportRadiusM: Double

        func sample(x: Double, y: Double) -> Double {
            if !scatteredSamples.isEmpty {
                let nearest = scatteredSamples
                    .map { sample in
                        (sample, hypot(sample.x - x, sample.y - y))
                    }
                    .sorted { $0.1 < $1.1 }
                    .prefix(4)
                var weighted = 0.0
                var totalWeight = 0.0
                for (sample, distance) in nearest {
                    let scale = max(supportRadiusM * 0.45, 0.20)
                    let weight = exp(-0.5 * pow(distance / scale, 2)) + 1e-9
                    weighted += sample.magneticNormUT * weight
                    totalWeight += weight
                }
                return totalWeight > 0
                    ? weighted / totalWeight
                    : scatteredSamples[0].magneticNormUT
            }
            let u = min(max((x - minX) / max(maxX - minX, 1e-9), 0), 1)
            var v = min(max((y - minY) / max(maxY - minY, 1e-9), 0), 1)
            if flipY { v = 1 - v }
            let gx = u * Double(columns - 1)
            let gy = v * Double(rows - 1)
            let x0 = min(max(Int(floor(gx)), 0), columns - 1)
            let y0 = min(max(Int(floor(gy)), 0), rows - 1)
            let x1 = min(x0 + 1, columns - 1)
            let y1 = min(y0 + 1, rows - 1)
            let tx = gx - Double(x0)
            let ty = gy - Double(y0)
            let q00 = Double(values[y0 * columns + x0])
            let q10 = Double(values[y0 * columns + x1])
            let q01 = Double(values[y1 * columns + x0])
            let q11 = Double(values[y1 * columns + x1])
            let top = q00 + (q10 - q00) * tx
            let bottom = q01 + (q11 - q01) * tx
            return top + (bottom - top) * ty
        }

        func sampleVector(x: Double, y: Double) -> Vector3? {
            let nearest = scatteredSamples.compactMap { sample -> (GenericMagneticMapSample, Double)? in
                guard sample.magneticXUT != nil, sample.magneticYUT != nil, sample.magneticZUT != nil else {
                    return nil
                }
                return (sample, hypot(sample.x - x, sample.y - y))
            }.sorted { $0.1 < $1.1 }.prefix(4)
            guard !nearest.isEmpty else { return nil }
            let scale = max(supportRadiusM * 0.45, 0.20)
            var xTotal = 0.0
            var yTotal = 0.0
            var zTotal = 0.0
            var weightTotal = 0.0
            for (sample, distance) in nearest {
                let weight = exp(-0.5 * pow(distance / scale, 2)) + 1e-9
                xTotal += (sample.magneticXUT ?? 0) * weight
                yTotal += (sample.magneticYUT ?? 0) * weight
                zTotal += (sample.magneticZUT ?? 0) * weight
                weightTotal += weight
            }
            guard weightTotal > 0 else { return nil }
            return Vector3(x: xTotal / weightTotal, y: yTotal / weightTotal, z: zTotal / weightTotal)
        }

        func contains(x: Double, y: Double) -> Bool {
            guard minX <= x && x <= maxX && minY <= y && y <= maxY else { return false }
            guard !scatteredSamples.isEmpty else { return true }
            return scatteredSamples.lazy.map {
                hypot($0.x - x, $0.y - y)
            }.min() ?? .infinity <= supportRadiusM
        }
    }

    private struct MapMetadata: Decodable {
        let rows: Int
        let columns: Int
        let minX: Double
        let maxX: Double
        let minY: Double
        let maxY: Double
        let flipY: Bool

        enum CodingKeys: String, CodingKey {
            case rows, columns
            case minX = "min_x_m"
            case maxX = "max_x_m"
            case minY = "min_y_m"
            case maxY = "max_y_m"
            case flipY = "flip_y"
        }
    }

    private struct SeededGenerator: RandomNumberGenerator {
        private var state: UInt64

        init(seed: UInt64) {
            state = seed == 0 ? 0x9E3779B97F4A7C15 : seed
        }

        mutating func next() -> UInt64 {
            state &+= 0x9E3779B97F4A7C15
            var value = state
            value = (value ^ (value >> 30)) &* 0xBF58476D1CE4E5B9
            value = (value ^ (value >> 27)) &* 0x94D049BB133111EB
            return value ^ (value >> 31)
        }

        mutating func uniform() -> Double {
            Double(next() >> 11) / Double(1 << 53)
        }

        mutating func normal(mean: Double = 0, standardDeviation: Double = 1) -> Double {
            let u1 = max(uniform(), 1e-12)
            let u2 = uniform()
            return mean + standardDeviation * sqrt(-2 * log(u1)) * cos(2 * .pi * u2)
        }
    }

    static func bundledDatasetURL(key: String) throws -> URL {
        let url = try nativeResourceRoot()
            .appendingPathComponent("NativeData", isDirectory: true)
            .appendingPathComponent("Datasets", isDirectory: true)
            .appendingPathComponent(key, isDirectory: true)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw EngineError.missingResource("NativeData/Datasets/\(key)")
        }
        return url
    }

    static func buildGenericMagneticMap(
        name: String,
        datasetKey: String,
        datasetDirectory: URL,
        route: [XYPoint],
        initialHeadingDegrees: Double?,
        activeStartTime: Double?,
        activeEndTime: Double?,
        settings: AlgorithmSettings = .optimized,
        progress: @Sendable (Double, String) -> Void
    ) throws -> GenericMagneticMapDocument {
        guard route.count >= 2 else { throw EngineError.invalidReferenceRoute }
        let routeLength = zip(route, route.dropFirst()).reduce(0.0) {
            $0 + hypot($1.1.x - $1.0.x, $1.1.y - $1.0.y)
        }
        guard routeLength > 0.5 else { throw EngineError.invalidReferenceRoute }
        progress(0.05, "读取参考采集")
        let frames = try loadFrames(from: datasetDirectory)
        let startTime = max(activeStartTime ?? frames.first!.time, frames.first!.time)
        let endTime = min(activeEndTime ?? frames.last!.time, frames.last!.time)
        let activeFrames = frames.filter { startTime <= $0.time && $0.time <= endTime }
        guard activeFrames.count >= 20 else { throw EngineError.insufficientSensorOverlap }
        let initialHeading = initialHeadingDegrees.map { $0 * .pi / 180 }
            ?? headingOfFirstSegment(route)
        progress(0.35, "提取步级磁场样本")
        let detection = detectSteps(frames: activeFrames, initialHeading: initialHeading, settings: settings)
        let steps = detection.steps
        guard steps.count >= 8 else { throw EngineError.insufficientMapSamples }
        let totalStepLength = steps.map(\.length).reduce(0, +)
        guard totalStepLength > 0 else { throw EngineError.insufficientMapSamples }
        var cumulative = 0.0
        var samples: [GenericMagneticMapSample] = []
        samples.reserveCapacity(steps.count + 1)
        if let first = steps.first {
            samples.append(GenericMagneticMapSample(
                x: route[0].x,
                y: route[0].y,
                magneticNormUT: first.magneticMagnitude
            ))
        }
        for step in steps {
            cumulative += step.length
            let position = point(on: route, progress: min(max(cumulative / totalStepLength, 0), 1))
            samples.append(GenericMagneticMapSample(
                x: position.x,
                y: position.y,
                magneticNormUT: step.magneticMagnitude
            ))
        }
        let identifier = try GenericMagneticMapStore.identifier(from: name)
        progress(1, "坐标化磁图建立完成")
        return try GenericMagneticMapDocument(
            schemaVersion: GenericMagneticMapDocument.schemaVersion,
            id: identifier,
            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            createdAt: Date(),
            sourceDatasetKeys: [datasetKey],
            referenceRoute: route,
            samples: samples,
            supportRadiusM: 1.0
        ).validated()
    }

    static func buildAnchoredGridMagneticMap(
        name: String,
        datasetKey: String,
        datasetDirectory: URL,
        coordinateFrame: String,
        anchors: [ImportedSpatialAnchor],
        excludedIntervals: [ClosedRange<Double>] = [],
        cellSizeM: Double = 0.4,
        settings: AlgorithmSettings = .optimized,
        progress: @Sendable (Double, String) -> Void
    ) throws -> GenericMagneticMapDocument {
        let anchors = anchors.sorted { $0.time < $1.time }
        guard anchors.count >= 2,
              zip(anchors, anchors.dropFirst()).contains(where: {
                  hypot($1.x - $0.x, $1.y - $0.y) > 0.2
              }),
              cellSizeM.isFinite, 0.2...1.0 ~= cellSizeM else {
            throw EngineError.invalidReferenceRoute
        }
        progress(0.05, "读取锚点建图采集")
        let frames = try loadFrames(from: datasetDirectory)
        let firstHeading = anchors.first?.headingDegrees.map { $0 * .pi / 180 } ?? 0
        let detection = detectSteps(frames: frames, initialHeading: firstHeading, settings: settings)
        let steps = detection.steps
        let referenceYaw = anchors.first?.deviceYawDegrees.map { $0 * .pi / 180 }
            ?? frames.first(where: { $0.deviceHeading != nil })?.deviceHeading?.yawRadians
        let roomRotation = firstHeading - (referenceYaw ?? 0)

        struct Observation {
            let x: Double
            let y: Double
            let vector: Vector3
            let norm: Double
            let directionBin: Int
        }
        var buckets: [GridKey: [Observation]] = [:]
        progress(0.25, "按相邻锚点分配传感器位置")
        for (start, end) in zip(anchors, anchors.dropFirst()) where end.time > start.time {
            if excludedIntervals.contains(where: {
                $0.lowerBound < end.time && start.time < $0.upperBound
            }) { continue }
            let segmentFrames = frames.filter { start.time <= $0.time && $0.time <= end.time }
            guard !segmentFrames.isEmpty else { continue }
            let segmentSteps = steps.filter { start.time <= $0.time && $0.time <= end.time }
            let totalStepDistance = segmentSteps.map(\.length).reduce(0, +)
            for frame in segmentFrames {
                guard let aligned = frame.alignedMagneticVector,
                      10...100 ~= aligned.magnitude else { continue }
                let distanceBefore = segmentSteps.lazy.filter { $0.time <= frame.time }.map(\.length).reduce(0, +)
                let timeProgress = (frame.time - start.time) / (end.time - start.time)
                let fraction = totalStepDistance > 0.1
                    ? distanceBefore / totalStepDistance
                    : timeProgress
                let progress = min(max(fraction, 0), 1)
                let x = start.x + (end.x - start.x) * progress
                let y = start.y + (end.y - start.y) * progress
                let roomVector = Vector3(
                    x: aligned.x * cos(roomRotation) - aligned.y * sin(roomRotation),
                    y: aligned.x * sin(roomRotation) + aligned.y * cos(roomRotation),
                    z: aligned.z
                )
                let roomHeading = frame.deviceHeading.map { wrap($0.yawRadians + roomRotation) }
                    ?? firstHeading
                let rawDirectionBin = Int(floor((roomHeading + .pi) / (2 * .pi) * 8))
                let directionBin = (rawDirectionBin % 8 + 8) % 8
                let key = GridKey(
                    x: Int(floor(x / cellSizeM)),
                    y: Int(floor(y / cellSizeM))
                )
                buckets[key, default: []].append(Observation(
                    x: x, y: y, vector: roomVector,
                    norm: roomVector.magnitude,
                    directionBin: directionBin
                ))
            }
        }
        progress(0.70, "稳健合并网格磁场")
        var samplesByKey: [GridKey: GenericMagneticMapSample] = [:]
        for (key, observations) in buckets where observations.count >= 5 {
            let center = median(observations.map(\.norm))
            let mad = median(observations.map { abs($0.norm - center) })
            let limit = max(3 * 1.4826 * mad, 5.0)
            let accepted = observations.filter { abs($0.norm - center) <= limit }
            guard accepted.count >= 4 else { continue }
            let norm = median(accepted.map(\.norm))
            let variance = accepted.map { pow($0.norm - norm, 2) }.reduce(0, +)
                / Double(accepted.count)
            samplesByKey[key] = GenericMagneticMapSample(
                x: accepted.map(\.x).reduce(0, +) / Double(accepted.count),
                y: accepted.map(\.y).reduce(0, +) / Double(accepted.count),
                magneticNormUT: norm,
                magneticXUT: median(accepted.map { $0.vector.x }),
                magneticYUT: median(accepted.map { $0.vector.y }),
                magneticZUT: median(accepted.map { $0.vector.z }),
                varianceUT2: variance,
                observationCount: accepted.count,
                directionCount: Set(accepted.map(\.directionBin)).count
            )
        }
        guard samplesByKey.count >= 8 else { throw EngineError.insufficientMapSamples }
        for (key, sample) in samplesByKey {
            var updated = sample
            if let left = samplesByKey[GridKey(x: key.x - 1, y: key.y)],
               let right = samplesByKey[GridKey(x: key.x + 1, y: key.y)] {
                updated.gradientXUTPerM = (right.magneticNormUT - left.magneticNormUT) / (2 * cellSizeM)
            }
            if let down = samplesByKey[GridKey(x: key.x, y: key.y - 1)],
               let up = samplesByKey[GridKey(x: key.x, y: key.y + 1)] {
                updated.gradientYUTPerM = (up.magneticNormUT - down.magneticNormUT) / (2 * cellSizeM)
            }
            samplesByKey[key] = updated
        }
        let identifier = try GenericMagneticMapStore.identifier(from: name)
        progress(1, "二维锚点磁图建立完成")
        return try GenericMagneticMapDocument(
            schemaVersion: GenericMagneticMapDocument.schemaVersion,
            id: identifier,
            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            createdAt: Date(),
            sourceDatasetKeys: [datasetKey],
            referenceRoute: anchors.map { XYPoint(x: $0.x, y: $0.y) },
            samples: samplesByKey.values.sorted { lhs, rhs in
                lhs.y == rhs.y ? lhs.x < rhs.x : lhs.y < rhs.y
            },
            supportRadiusM: max(cellSizeM * 1.8, 0.55),
            coordinateFrame: coordinateFrame,
            gridCellSizeM: cellSizeM
        ).validated()
    }

    static func run(
        request: Request,
        progress: @Sendable (Double, String) -> Void
    ) throws -> PositioningResult {
        if request.magneticMap.sourceDatasetKeys.contains(request.datasetKey) {
            throw EngineError.mapSourceCannotLocateItself
        }
        progress(0.02, "读取三路传感器 CSV")
        let frames = try loadFrames(from: request.datasetDirectory)
        try Task.checkCancellation()

        let startTime = max(request.activeStartTime ?? frames.first!.time, frames.first!.time)
        let endTime = min(request.activeEndTime ?? frames.last!.time, frames.last!.time)
        let activeFrames = frames.filter { startTime <= $0.time && $0.time <= endTime }
        guard activeFrames.count >= 20 else { throw EngineError.insufficientSensorOverlap }

        progress(0.20, "选择航向来源并检测步态")
        let initialHeading = request.initialHeadingDegrees.map { $0 * .pi / 180 }
            ?? headingOfFirstSegment(request.route)
        let stepDetection = detectSteps(
            frames: activeFrames,
            initialHeading: initialHeading,
            settings: request.settings
        )
        let steps = stepDetection.steps
        guard !steps.isEmpty else { throw EngineError.noSteps }
        try Task.checkCancellation()

        let pdr = buildPDR(start: request.route[0], steps: steps)
        var pfTrack: [XYPoint] = []
        var pfConfidence: [PFConfidenceSample]?
        var localizationHealth: [LocalizationHealthSample]?
        var recoveryEvents: [LocalizationHealthSample]?
        var mapBounds: [Double]?
        progress(0.30, "加载坐标化地磁图并执行原生粒子滤波")
        let map = magneticMap(from: request.magneticMap)
        let filter = try particleFilter(
            start: request.route[0],
            steps: steps,
            map: map,
            settings: request.settings,
            seed: stableSeed(request.datasetKey),
            progress: progress
        )
        pfTrack = filter.track
        pfConfidence = filter.confidence
        localizationHealth = filter.health
        recoveryEvents = filter.recoveries
        mapBounds = [map.minX, map.maxX, map.minY, map.maxY]

        progress(0.94, "计算误差与定位健康度")
        let pdrErrors = alignedErrors(track: pdr, route: request.route)
        let crossTrack: [Double]? = nil
        let closure: Double? = nil
        let pfErrors = pfTrack.isEmpty ? nil : alignedErrors(track: pfTrack, route: request.route)
        let result = PositioningResult(
            branch: "generic_map_pf_native_swift",
            datasetKey: request.datasetKey,
            routeLabel: routeLabel(for: request.datasetKey),
            routeXY: request.route,
            pdrTrack: pdr,
            pfTrack: pfTrack,
            pdrErrorStats: statistics(pdrErrors),
            pfErrorStats: pfErrors.flatMap(statistics),
            controlledCrossTrackErrorStats: crossTrack.flatMap(statistics),
            closureErrorM: closure,
            stepsDetected: steps.count,
            sensorFramesUsed: activeFrames.count,
            fullSensorFrames: frames.count,
            pfSmoothingMode: request.settings.smoothingMode.rawValue,
            pfSmoothingAlpha: request.settings.smoothingAlpha,
            headingSnapDegrees: request.settings.headingSnapDegrees,
            stepLengthScale: request.settings.stepLengthScale,
            vectorMapEnabled: false,
            pfJointCalibration: request.settings.jointCalibrationEnabled,
            alignmentMode: "active_walk_uniform_speed",
            mapBounds: mapBounds,
            activeWalkInterval: ActiveWalkInterval(
                startTime: startTime,
                endTime: endTime,
                duration: max(endTime - startTime, 0),
                headExcluded: max(startTime - frames.first!.time, 0),
                tailExcluded: max(frames.last!.time - endTime, 0),
                confidence: "native_motion_window"
            ),
            headingDiagnostics: stepDetection.headingDiagnostics,
            controlledMotionDiagnostics: nil,
            magneticFusionDiagnostics: nil,
            pfConfidenceHistory: pfConfidence,
            localizationHealthHistory: localizationHealth,
            localizationRecoveryEvents: recoveryEvents
        )
        progress(1, "原生计算完成")
        return result
    }

    private static func loadFrames(from directory: URL) throws -> [Frame] {
        let acceleration = try loadCSV(directory.appendingPathComponent("Accelerometer.csv"))
        let gyroscope = try loadCSV(directory.appendingPathComponent("Gyroscope.csv"))
        let magnetometer = try loadAlgorithmMagnetometer(from: directory)
        let deviceHeadings = try? loadDeviceHeadings(
            directory.appendingPathComponent("DeviceMotion.csv")
        )
        guard let a0 = acceleration.first, let a1 = acceleration.last,
              let g0 = gyroscope.first, let g1 = gyroscope.last,
              let m0 = magnetometer.first, let m1 = magnetometer.last else {
            throw EngineError.insufficientSensorOverlap
        }
        let start = max(a0.time, g0.time, m0.time)
        let end = min(a1.time, g1.time, m1.time)
        guard end > start else { throw EngineError.insufficientSensorOverlap }
        var gyroCursor = 0
        var magCursor = 0
        var headingCursor = 0
        return acceleration.compactMap { sample in
            guard start <= sample.time && sample.time <= end else { return nil }
            let gyro = interpolate(gyroscope, at: sample.time, cursor: &gyroCursor)
            let mag = interpolate(magnetometer, at: sample.time, cursor: &magCursor)
            return Frame(
                time: sample.time,
                acceleration: sample.value,
                gyroscope: gyro,
                magnetometer: mag,
                deviceHeading: deviceHeadings.flatMap {
                    interpolateHeading($0, at: sample.time, cursor: &headingCursor)
                },
                alignedMagneticVector: deviceHeadings.flatMap {
                    interpolateAlignedMagnetic($0, at: sample.time, cursor: &headingCursor)
                }
            )
        }
    }

    private static func loadAlgorithmMagnetometer(from directory: URL) throws -> [TimedVector] {
        let deviceMotionURL = directory.appendingPathComponent("DeviceMotion.csv")
        if FileManager.default.fileExists(atPath: deviceMotionURL.path),
           let calibrated = try? loadCSV(
               deviceMotionURL,
               valueIndices: (17, 18, 19)
           ),
           calibrated.count >= 20 {
            return calibrated
        }
        return try loadCSV(directory.appendingPathComponent("Magnetometer.csv"))
    }

    private static func loadCSV(
        _ url: URL,
        valueIndices: (Int, Int, Int) = (1, 2, 3)
    ) throws -> [TimedVector] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            throw EngineError.malformedCSV(url.lastPathComponent)
        }
        let lines = text.split(whereSeparator: \.isNewline)
        guard lines.count >= 20 else { throw EngineError.malformedCSV(url.lastPathComponent) }
        let requiredIndex = max(valueIndices.0, valueIndices.1, valueIndices.2)
        var samples: [TimedVector] = []
        samples.reserveCapacity(lines.count - 1)
        for line in lines.dropFirst() {
            let fields = line.split(separator: ",", omittingEmptySubsequences: false)
            guard fields.count > requiredIndex,
                  let time = Double(fields[0].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let x = Double(fields[valueIndices.0].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let y = Double(fields[valueIndices.1].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let z = Double(fields[valueIndices.2].trimmingCharacters(in: .whitespacesAndNewlines)),
                  time.isFinite, x.isFinite, y.isFinite, z.isFinite else { continue }
            samples.append(TimedVector(time: time, value: Vector3(x: x, y: y, z: z)))
        }
        samples.sort { $0.time < $1.time }
        guard samples.count >= 20 else { throw EngineError.malformedCSV(url.lastPathComponent) }
        return samples
    }

    private static func loadDeviceHeadings(_ url: URL) throws -> [TimedHeading] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            throw EngineError.malformedCSV(url.lastPathComponent)
        }
        let lines = text.split(whereSeparator: \.isNewline)
        guard lines.count >= 20 else { throw EngineError.malformedCSV(url.lastPathComponent) }
        var samples: [TimedHeading] = []
        samples.reserveCapacity(lines.count - 1)
        for line in lines.dropFirst() {
            let fields = line.split(separator: ",", omittingEmptySubsequences: false)
            guard fields.count > 20,
                  let time = Double(fields[0].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let quaternionX = Double(fields[1].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let quaternionY = Double(fields[2].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let quaternionZ = Double(fields[3].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let quaternionW = Double(fields[4].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let yaw = Double(fields[7].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let magneticX = Double(fields[17].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let magneticY = Double(fields[18].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let magneticZ = Double(fields[19].trimmingCharacters(in: .whitespacesAndNewlines)),
                  let accuracyValue = Double(
                      fields[20].trimmingCharacters(in: .whitespacesAndNewlines)
                  ),
                  time.isFinite, yaw.isFinite, accuracyValue.isFinite,
                  quaternionX.isFinite, quaternionY.isFinite, quaternionZ.isFinite,
                  quaternionW.isFinite, magneticX.isFinite, magneticY.isFinite,
                  magneticZ.isFinite else { continue }
            let aligned = Vector3(
                x: (1 - 2 * (quaternionY * quaternionY + quaternionZ * quaternionZ)) * magneticX
                    + 2 * (quaternionX * quaternionY - quaternionZ * quaternionW) * magneticY
                    + 2 * (quaternionX * quaternionZ + quaternionY * quaternionW) * magneticZ,
                y: 2 * (quaternionX * quaternionY + quaternionZ * quaternionW) * magneticX
                    + (1 - 2 * (quaternionX * quaternionX + quaternionZ * quaternionZ)) * magneticY
                    + 2 * (quaternionY * quaternionZ - quaternionX * quaternionW) * magneticZ,
                z: 2 * (quaternionX * quaternionZ - quaternionY * quaternionW) * magneticX
                    + 2 * (quaternionY * quaternionZ + quaternionX * quaternionW) * magneticY
                    + (1 - 2 * (quaternionX * quaternionX + quaternionY * quaternionY)) * magneticZ
            )
            samples.append(TimedHeading(
                time: time,
                yawRadians: yaw,
                magneticAccuracy: Int(accuracyValue.rounded()),
                alignedMagneticVector: aligned
            ))
        }
        samples.sort { $0.time < $1.time }
        guard samples.count >= 20 else { throw EngineError.malformedCSV(url.lastPathComponent) }
        return samples
    }

    private static func interpolate(
        _ samples: [TimedVector],
        at time: Double,
        cursor: inout Int
    ) -> Vector3 {
        while cursor + 1 < samples.count && samples[cursor + 1].time < time { cursor += 1 }
        let next = min(cursor + 1, samples.count - 1)
        let a = samples[cursor]
        let b = samples[next]
        let ratio = a.time == b.time ? 0 : min(max((time - a.time) / (b.time - a.time), 0), 1)
        return Vector3(
            x: a.value.x + (b.value.x - a.value.x) * ratio,
            y: a.value.y + (b.value.y - a.value.y) * ratio,
            z: a.value.z + (b.value.z - a.value.z) * ratio
        )
    }

    private static func interpolateHeading(
        _ samples: [TimedHeading],
        at time: Double,
        cursor: inout Int
    ) -> DeviceHeadingFrame? {
        guard let first = samples.first, let last = samples.last,
              first.time <= time, time <= last.time else { return nil }
        while cursor + 1 < samples.count && samples[cursor + 1].time < time { cursor += 1 }
        let next = min(cursor + 1, samples.count - 1)
        let a = samples[cursor]
        let b = samples[next]
        let gap = b.time - a.time
        guard gap <= 0.25 else { return nil }
        let ratio = gap <= 1e-9 ? 0 : min(max((time - a.time) / gap, 0), 1)
        let delta = wrap(b.yawRadians - a.yawRadians)
        return DeviceHeadingFrame(
            yawRadians: wrap(a.yawRadians + delta * ratio),
            magneticAccuracy: min(a.magneticAccuracy, b.magneticAccuracy)
        )
    }

    private static func interpolateAlignedMagnetic(
        _ samples: [TimedHeading],
        at time: Double,
        cursor: inout Int
    ) -> Vector3? {
        guard let first = samples.first, let last = samples.last,
              first.time <= time, time <= last.time else { return nil }
        while cursor + 1 < samples.count && samples[cursor + 1].time < time { cursor += 1 }
        let next = min(cursor + 1, samples.count - 1)
        let a = samples[cursor]
        let b = samples[next]
        let gap = b.time - a.time
        guard gap <= 0.25 else { return nil }
        let ratio = gap <= 1e-9 ? 0 : min(max((time - a.time) / gap, 0), 1)
        return Vector3(
            x: a.alignedMagneticVector.x
                + (b.alignedMagneticVector.x - a.alignedMagneticVector.x) * ratio,
            y: a.alignedMagneticVector.y
                + (b.alignedMagneticVector.y - a.alignedMagneticVector.y) * ratio,
            z: a.alignedMagneticVector.z
                + (b.alignedMagneticVector.z - a.alignedMagneticVector.z) * ratio
        )
    }

    private static func loadBundledMap() throws -> MagneticMap {
        let base = try nativeResourceRoot().appendingPathComponent("NativeData/Map")
        guard let metadataData = try? Data(contentsOf: base.appendingPathComponent("metadata.json")),
              let metadata = try? JSONDecoder().decode(MapMetadata.self, from: metadataData),
              let binary = try? Data(contentsOf: base.appendingPathComponent("magnetic_map.f32")) else {
            throw EngineError.missingResource("NativeData/Map")
        }
        let count = metadata.rows * metadata.columns
        guard binary.count == count * MemoryLayout<Float>.size else { throw EngineError.invalidMap }
        var values = Array(repeating: Float.zero, count: count)
        _ = values.withUnsafeMutableBytes { binary.copyBytes(to: $0) }
        return MagneticMap(
            rows: metadata.rows,
            columns: metadata.columns,
            values: values,
            minX: metadata.minX,
            maxX: metadata.maxX,
            minY: metadata.minY,
            maxY: metadata.maxY,
            flipY: metadata.flipY,
            scatteredSamples: [],
            supportRadiusM: 0
        )
    }

    private static func magneticMap(from document: GenericMagneticMapDocument) -> MagneticMap {
        let bounds = document.bounds ?? CoordinateBounds(
            minX: 0,
            maxX: 1,
            minY: 0,
            maxY: 1
        )
        return MagneticMap(
            rows: 0,
            columns: 0,
            values: [],
            minX: bounds.minX,
            maxX: bounds.maxX,
            minY: bounds.minY,
            maxY: bounds.maxY,
            flipY: false,
            scatteredSamples: document.samples,
            supportRadiusM: document.supportRadiusM
        )
    }

    private static func nativeResourceRoot() throws -> URL {
        if let path = ProcessInfo.processInfo.environment["GEOMAG_NATIVE_RESOURCE_ROOT"],
           !path.isEmpty {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        guard let resourceURL = Bundle.main.resourceURL else {
            throw EngineError.missingResource("NativeData")
        }
        return resourceURL
    }

    private static func detectSteps(
        frames: [Frame],
        initialHeading: Double,
        settings: AlgorithmSettings
    ) -> (steps: [Step], headingDiagnostics: HeadingDiagnostics) {
        let magnitudes = frames.map { $0.acceleration.magnitude }
        var smoothed = magnitudes
        if magnitudes.count >= 3 {
            for index in 1..<(magnitudes.count - 1) {
                smoothed[index] = (magnitudes[index - 1] + magnitudes[index] + magnitudes[index + 1]) / 3
            }
        }
        let stationaryRates = frames.compactMap { frame -> Double? in
            let staticAcceleration = abs(frame.acceleration.magnitude - 9.80665) <= 0.30
            let staticRotation = frame.gyroscope.magnitude <= 0.12
            return staticAcceleration && staticRotation ? frame.gyroscope.z : nil
        }
        let gyroBias = stationaryRates.isEmpty ? 0 : mean(stationaryRates)
        var headings = Array(repeating: initialHeading, count: frames.count)
        if frames.count >= 2 {
            for index in 1..<frames.count {
                let dt = min(max(frames[index].time - frames[index - 1].time, 0), 0.1)
                let rate = ((frames[index - 1].gyroscope.z + frames[index].gyroscope.z) / 2) - gyroBias
                headings[index] = wrap(headings[index - 1] + rate * dt)
            }
        }
        let headingResolution = DeviceHeadingResolver.resolve(
            frameTimes: frames.map(\.time),
            gyroHeadings: headings,
            deviceFrames: frames.map(\.deviceHeading),
            initialHeading: initialHeading
        )
        headings = headingResolution.headings
        let referenceYaw = frames.first(where: { $0.deviceHeading != nil })?.deviceHeading?.yawRadians
        let roomRotation = initialHeading - (referenceYaw ?? initialHeading)
        var steps: [Step] = []
        var previousPeak = 0
        guard frames.count > 17 else { return (steps, headingResolution.diagnostics) }
        for index in 15..<(frames.count - 1) {
            let windowStart = max(0, index - 100)
            let history = Array(smoothed[windowStart...index + 1])
            let candidate = history.count - 2
            let threshold = mean(history) + 0.45 * standardDeviation(history)
            let recentMin = history[max(0, candidate - 8)...candidate].min() ?? history[candidate]
            let isPeak = history[candidate] > history[candidate - 1]
                && history[candidate] >= history[candidate + 1]
                && history[candidate] > threshold
                && history[candidate] - recentMin > 0.30
            guard isPeak else { continue }
            if let last = steps.last, frames[index].time - last.time < 0.40 { continue }
            let accelerationSegment = magnitudes[previousPeak...min(index + 1, magnitudes.count - 1)]
            let delta = max((accelerationSegment.max() ?? 0) - (accelerationSegment.min() ?? 0), 1e-9)
            let length = min(max(0.31 * pow(delta, 0.25) * settings.stepLengthScale, 0.15), 1.20)
            let magneticWindow = frames[previousPeak...min(index + 1, frames.count - 1)]
                .map { $0.magnetometer.magnitude }
                .filter { 10 <= $0 && $0 <= 100 }
            let alignedVector = frames[index].alignedMagneticVector.map { vector in
                Vector3(
                    x: vector.x * cos(roomRotation) - vector.y * sin(roomRotation),
                    y: vector.x * sin(roomRotation) + vector.y * cos(roomRotation),
                    z: vector.z
                )
            }
            steps.append(Step(
                frameIndex: index,
                time: frames[index].time,
                length: length,
                heading: headings[index],
                sensorHeading: headings[index],
                magneticMagnitude: mean(magneticWindow),
                alignedMagneticVector: alignedVector,
                segmentIndex: nil
            ))
            previousPeak = index + 1
        }
        return (steps, headingResolution.diagnostics)
    }

    // Kept temporarily for decoding and comparing historical controlled-route
    // results. The app no longer exposes or calls this route-specific path.
    private static func legacyDetectSteps(
        datasetKey: String,
        frames: [Frame],
        initialHeading: Double,
        settings: AlgorithmSettings,
        route: [XYPoint],
        controlledProfile: ControlledMotionProfile?,
        magneticTemplate: ControlledMagneticTemplate?
    ) -> (
        steps: [Step],
        headingDiagnostics: HeadingDiagnostics,
        controlledMotionDiagnostics: ControlledMotionDiagnostics?,
        magneticFusionDiagnostics: MagneticFusionDiagnostics?,
        magneticTemplateSegments: [ControlledMagneticSegmentTemplate]?
    ) {
        let magnitudes = frames.map { $0.acceleration.magnitude }
        var smoothed = magnitudes
        if magnitudes.count >= 3 {
            for index in 1..<(magnitudes.count - 1) {
                smoothed[index] = (magnitudes[index - 1] + magnitudes[index] + magnitudes[index + 1]) / 3
            }
        }
        // Only truly stationary frames are valid for yaw-bias calibration.
        // Using arbitrary head/tail windows corrupts short captures when the
        // operator starts walking immediately or stops recording at arrival.
        let stationaryRates = frames.compactMap { frame -> Double? in
            let accelerationIsStatic = abs(frame.acceleration.magnitude - 9.80665) <= 0.30
            let gyroscopeIsStatic = frame.gyroscope.magnitude <= 0.12
            return accelerationIsStatic && gyroscopeIsStatic ? frame.gyroscope.z : nil
        }
        let gyroBias = stationaryRates.isEmpty ? 0 : mean(stationaryRates)
        var headings = Array(repeating: initialHeading, count: frames.count)
        if frames.count >= 2 {
            for index in 1..<frames.count {
                let dt = min(max(frames[index].time - frames[index - 1].time, 0), 0.1)
                let rate = ((frames[index - 1].gyroscope.z + frames[index].gyroscope.z) / 2) - gyroBias
                headings[index] = wrap(headings[index - 1] + rate * dt)
            }
        }
        let headingResolution = DeviceHeadingResolver.resolve(
            frameTimes: frames.map(\.time),
            gyroHeadings: headings,
            deviceFrames: frames.map(\.deviceHeading),
            initialHeading: initialHeading
        )
        headings = headingResolution.headings
        let turnRegions = controlledProfile == nil
            ? []
            : detectQuarterTurnRegions(frames: frames)
        let usesControlledCadence = controlledProfile != nil
        var steps: [Step] = []
        var previousPeak = 0
        var bufferStart = 0
        for index in 15..<(frames.count - 1) {
            let windowStart = usesControlledCadence ? bufferStart : max(0, index - 100)
            guard index - windowStart >= 14 else { continue }
            let history: [Double]
            let candidateIndex: Int
            if usesControlledCadence {
                let raw = Array(magnitudes[windowStart...index + 1])
                history = raw.indices.map { localIndex in
                    let previous = localIndex > 0 ? raw[localIndex - 1] : 0
                    let next = localIndex + 1 < raw.count ? raw[localIndex + 1] : 0
                    return (previous + raw[localIndex] + next) / 3
                }
                candidateIndex = history.count - 2
            } else {
                history = Array(smoothed[windowStart...index + 1])
                candidateIndex = history.count - 2
            }
            let threshold = mean(history) + 0.45 * standardDeviation(history)
            let recentMin = history[max(0, candidateIndex - 8)...candidateIndex].min()
                ?? history[candidateIndex]
            let isPeak = history[candidateIndex] > history[candidateIndex - 1]
                && history[candidateIndex] >= history[candidateIndex + 1]
                && history[candidateIndex] > threshold
                && history[candidateIndex] - recentMin > (usesControlledCadence ? 0.40 : 0.30)
            guard isPeak else { continue }
            let minimumInterval = usesControlledCadence ? 0.55 : 0.40
            if let last = steps.last, frames[index].time - last.time < minimumInterval { continue }
            let lengthStart = usesControlledCadence ? bufferStart : previousPeak
            let segment = magnitudes[lengthStart...min(index + 1, magnitudes.count - 1)]
            let delta = max((segment.max() ?? 0) - (segment.min() ?? 0), 1e-9)
            let length = 0.31 * pow(delta, 0.25) * settings.stepLengthScale
            let sensorHeading = headings[index]
            var heading = sensorHeading
            if settings.headingSnapDegrees > 0 {
                let grid = settings.headingSnapDegrees * .pi / 180
                heading = (heading / grid).rounded() * grid
            }
            let magneticWindow = frames[lengthStart...min(index + 1, frames.count - 1)]
                .map { $0.magnetometer.magnitude }
                .filter { 10 <= $0 && $0 <= 100 }
            steps.append(Step(
                frameIndex: index,
                time: frames[index].time,
                length: min(max(length, 0.15), 1.20),
                heading: heading,
                sensorHeading: sensorHeading,
                magneticMagnitude: usesControlledCadence
                    ? frames[index].magnetometer.magnitude
                    : mean(magneticWindow),
                alignedMagneticVector: frames[index].alignedMagneticVector,
                segmentIndex: nil
            ))
            previousPeak = index + 1
            if usesControlledCadence {
                bufferStart = min(index + 2, frames.count - 1)
            }
        }
        guard let profile = controlledProfile else {
            return (steps, headingResolution.diagnostics, nil, nil, nil)
        }
        guard headingResolution.diagnostics.deviceMotionAccepted else {
            return (
                steps,
                headingResolution.diagnostics,
                controlledDiagnostics(
                    profile: profile,
                    turns: turnRegions,
                    removed: 0,
                    postEndpoint: 0,
                    counts: [],
                    lengths: [],
                    priors: [],
                    headings: [],
                    rejectionReason: "core_motion_heading_unavailable"
                ),
                nil,
                nil
            )
        }
        guard turnRegions.count == 4 else {
            return (
                steps,
                headingResolution.diagnostics,
                controlledDiagnostics(
                    profile: profile,
                    turns: turnRegions,
                    removed: 0,
                    postEndpoint: 0,
                    counts: [],
                    lengths: [],
                    priors: [],
                    headings: [],
                    rejectionReason: "quarter_turns_not_detected"
                ),
                nil,
                nil
            )
        }

        var removed = 0
        let endpointStepCount = closedRouteEndpointStepCount(
            steps: steps,
            initialHeading: initialHeading
        )
        let postEndpoint = endpointStepCount.map { steps.count - $0 } ?? 0
        let endpointTrimmedSteps = endpointStepCount.map {
            Array(steps.prefix($0))
        } ?? steps
        var straightSteps: [Step] = []
        for step in endpointTrimmedSteps {
            if turnRegions.prefix(3).contains(where: {
                $0.onsetTime <= step.time && step.time <= $0.completionTime
            }) {
                removed += 1
                continue
            }
            let segmentIndex = turnRegions.prefix(3).reduce(into: 0) { segment, turn in
                if step.time > turn.completionTime { segment += 1 }
            }
            straightSteps.append(Step(
                frameIndex: step.frameIndex,
                time: step.time,
                length: step.length,
                heading: step.heading,
                sensorHeading: step.sensorHeading,
                magneticMagnitude: step.magneticMagnitude,
                alignedMagneticVector: step.alignedMagneticVector,
                segmentIndex: segmentIndex
            ))
        }
        var counts = (0..<4).map { segment in
            straightSteps.count { $0.segmentIndex == segment }
        }
        var partitionMethod = "sample_level"
        if !counts.allSatisfy({ $0 > 0 }),
           let fallback = stepLevelStraightPartition(
               steps: endpointTrimmedSteps,
               initialHeading: initialHeading
           ) {
            straightSteps = fallback.steps
            removed = fallback.removed
            counts = (0..<4).map { segment in
                straightSteps.count { $0.segmentIndex == segment }
            }
            partitionMethod = "step_level_fallback"
        }
        guard counts.allSatisfy({ $0 > 0 }) else {
            return (
                straightSteps,
                headingResolution.diagnostics,
                controlledDiagnostics(
                    profile: profile,
                    turns: turnRegions,
                    removed: removed,
                    postEndpoint: postEndpoint,
                    counts: counts,
                    lengths: [],
                    priors: [],
                    headings: [],
                    partitionMethod: partitionMethod,
                    rejectionReason: "empty_straight_segment"
                ),
                nil,
                nil
            )
        }
        let calibrated = calibrateControlledSteps(
            straightSteps,
            route: route,
            turns: turnRegions,
            profile: profile
        )
        let templateSegments = magneticTemplateSegments(from: straightSteps)
        let magnetic = ControlledMagneticFusion.apply(
            datasetKey: datasetKey,
            group: profile.group,
            calibrationSourceKey: profile.calibrationSourceKey,
            initialHeading: initialHeading,
            steps: calibrated.steps.map { step in
                ControlledMagneticStepInput(
                    length: step.length,
                    sensorHeading: step.sensorHeading,
                    fusedHeading: step.heading,
                    magneticNormUT: step.magneticMagnitude,
                    alignedMagneticVectorUT: step.alignedMagneticVector.map {
                        MagneticVector3(x: $0.x, y: $0.y, z: $0.z)
                    },
                    segmentIndex: step.segmentIndex ?? 0
                )
            },
            templateOverride: magneticTemplate
        )
        var fusedSteps: [Step] = []
        fusedSteps.reserveCapacity(calibrated.steps.count)
        for (index, step) in calibrated.steps.enumerated() {
            let segment = step.segmentIndex ?? 0
            fusedSteps.append(Step(
                frameIndex: step.frameIndex,
                time: step.time,
                length: magnetic.stepLengths[index],
                heading: magnetic.segmentHeadings[segment],
                sensorHeading: step.sensorHeading,
                magneticMagnitude: step.magneticMagnitude,
                alignedMagneticVector: step.alignedMagneticVector,
                segmentIndex: step.segmentIndex
            ))
        }
        return (
            fusedSteps,
            headingResolution.diagnostics,
            controlledDiagnostics(
                profile: profile,
                turns: turnRegions,
                removed: removed,
                postEndpoint: postEndpoint,
                counts: counts,
                lengths: calibrated.segmentLengths,
                priors: calibrated.distancePriors,
                headings: calibrated.segmentHeadingsDegrees,
                partitionMethod: partitionMethod,
                rejectionReason: nil
            ),
            magnetic.diagnostics,
            templateSegments
        )
    }

    private static func magneticTemplateSegments(
        from steps: [Step]
    ) -> [ControlledMagneticSegmentTemplate]? {
        var output: [ControlledMagneticSegmentTemplate] = []
        for segment in 0..<4 {
            let selected = steps.filter { $0.segmentIndex == segment }
            guard !selected.isEmpty,
                  selected.allSatisfy({ $0.alignedMagneticVector != nil }) else { return nil }
            let total = selected.map(\.length).reduce(0, +)
            var cumulative = 0.0
            let progress = selected.map { step in
                cumulative += step.length
                return cumulative / max(total, 1e-9)
            }
            output.append(ControlledMagneticSegmentTemplate(
                progress: progress,
                magneticNormUT: selected.map(\.magneticMagnitude),
                alignedVectorsUT: selected.compactMap { vector in
                    vector.alignedMagneticVector.map {
                        MagneticVector3(x: $0.x, y: $0.y, z: $0.z)
                    }
                }
            ))
        }
        return output
    }

    private static func detectQuarterTurnRegions(frames: [Frame]) -> [TurnRegion] {
        let yaws = frames.map { $0.deviceHeading?.yawRadians }
        guard let firstIndex = yaws.firstIndex(where: { $0 != nil }),
              let firstYaw = yaws[firstIndex] else { return [] }
        var continuous = firstYaw
        var previousRaw = firstYaw
        var unwrapped = Array(repeating: Double.nan, count: frames.count)
        for index in firstIndex..<frames.count {
            guard let raw = yaws[index] else { continue }
            continuous += wrap(raw - previousRaw)
            unwrapped[index] = continuous
            previousRaw = raw
        }
        let validIndices = unwrapped.indices.filter { unwrapped[$0].isFinite }
        guard validIndices.count >= 20,
              validIndices.count == frames.count else { return [] }
        let yawDegrees = unwrapped.map { $0 * 180 / .pi }
        let positiveIntervals = zip(frames, frames.dropFirst())
            .map { $1.time - $0.time }
            .filter { $0 > 0 }
        guard !positiveIntervals.isEmpty else { return [] }
        let sampleRate = 1 / median(positiveIntervals)
        let width = max(1, Int((0.12 * sampleRate).rounded()))
        var rate = Array(repeating: 0.0, count: frames.count)
        for index in frames.indices {
            let left = max(0, index - 1)
            let right = min(frames.count - 1, index + 1)
            let dt = frames[right].time - frames[left].time
            if dt > 1e-9 {
                rate[index] = abs((yawDegrees[right] - yawDegrees[left]) / dt)
            }
        }
        if width > 1 {
            // Match NumPy `convolve(..., mode: "same")`; even kernels are
            // aligned with one additional sample on the forward side.
            let radius = (width - 1) / 2
            rate = rate.indices.map { index in
                var total = 0.0
                for offset in 0..<width {
                    let source = index + offset - radius
                    if rate.indices.contains(source) { total += rate[source] }
                }
                return total / Double(width)
            }
        }
        let quietSamples = max(2, Int((0.18 * sampleRate).rounded()))
        let padding = max(1, Int((0.08 * sampleRate).rounded()))
        var regions: [TurnRegion] = []
        var index = firstIndex
        while index < rate.count {
            if rate[index] < 28 {
                index += 1
                continue
            }
            let onset = index
            var lastActive = index
            var cursor = index + 1
            while cursor < rate.count {
                if rate[cursor] >= 12 { lastActive = cursor }
                if cursor - lastActive >= quietSamples { break }
                cursor += 1
            }
            let paddedOnset = max(firstIndex, onset - padding)
            let completion = min(rate.count - 1, lastActive + padding)
            let signedAngle = yawDegrees[completion] - yawDegrees[paddedOnset]
            if abs(signedAngle) >= 30 {
                regions.append(TurnRegion(
                    onsetIndex: paddedOnset,
                    completionIndex: completion,
                    onsetTime: frames[paddedOnset].time,
                    completionTime: frames[completion].time,
                    signedAngleDegrees: signedAngle
                ))
            }
            index = max(cursor, onset + 1)
        }
        return regions
    }

    private static func closedRouteEndpointStepCount(
        steps: [Step],
        initialHeading: Double
    ) -> Int? {
        let turns = detectedStepTurns(steps: steps, initialHeading: initialHeading)
        guard turns.count >= 4 else { return nil }
        let count = turns[3].startIndex
        return 0 < count && count < steps.count ? count : nil
    }

    private static func detectedStepTurns(
        steps: [Step],
        initialHeading: Double
    ) -> [(stepIndex: Int, startIndex: Int, delta: Double)] {
        guard steps.count >= 2 else { return [] }
        let headings = [initialHeading] + steps.map(\.heading)
        let window = 3
        var turns: [(stepIndex: Int, startIndex: Int, delta: Double)] = []
        for index in 1..<headings.count {
            let start = max(0, index - window)
            let delta = wrap(headings[index] - headings[start])
            guard abs(delta) >= 25 * .pi / 180 else { continue }
            let candidate = (index, start, delta)
            if let last = turns.last,
               index - last.stepIndex <= window {
                if abs(delta) > abs(last.delta) {
                    turns[turns.count - 1] = candidate
                }
            } else {
                turns.append(candidate)
            }
        }
        return turns
    }

    private static func stepLevelStraightPartition(
        steps: [Step],
        initialHeading: Double
    ) -> (steps: [Step], removed: Int)? {
        let turns = detectedStepTurns(steps: steps, initialHeading: initialHeading)
        guard turns.count >= 3 else { return nil }
        var output: [Step] = []
        var removed = 0
        for (index, step) in steps.enumerated() {
            let isTurning = turns.prefix(3).contains { turn in
                turn.startIndex <= index && index < min(turn.stepIndex, steps.count)
            }
            if isTurning {
                removed += 1
                continue
            }
            let segment = turns.prefix(3).reduce(into: 0) { value, turn in
                if index >= min(turn.stepIndex, steps.count) { value += 1 }
            }
            output.append(Step(
                frameIndex: step.frameIndex,
                time: step.time,
                length: step.length,
                heading: step.heading,
                sensorHeading: step.sensorHeading,
                magneticMagnitude: step.magneticMagnitude,
                alignedMagneticVector: step.alignedMagneticVector,
                segmentIndex: segment
            ))
        }
        return (output, removed)
    }

    private static func calibrateControlledSteps(
        _ steps: [Step],
        route: [XYPoint],
        turns: [TurnRegion],
        profile: ControlledMotionProfile
    ) -> (
        steps: [Step],
        segmentLengths: [Double],
        distancePriors: [Double],
        segmentHeadingsDegrees: [Double]
    ) {
        var estimatedLengths: [Double] = []
        var distancePriors: [Double] = []
        var segmentHeadings: [Double] = []
        for segment in 0..<4 {
            let selected = steps.filter { $0.segmentIndex == segment }
            let scaled = selected.map { $0.length * profile.stepScaleBySegment[segment] }
            let sensorLength = scaled.reduce(0, +)
            let countRatio = min(
                Double(selected.count) / Double(max(profile.calibrationStepCounts[segment], 1)),
                Double(profile.calibrationStepCounts[segment]) / Double(max(selected.count, 1))
            )
            let average = mean(scaled)
            let coefficientOfVariation = average > 1e-9
                ? standardDeviation(scaled) / average
                : 0
            let reference = profile.referenceLengthsM[segment]
            let conflict = abs(sensorLength - reference) / max(reference, 0.15)
            let confidence = pow(countRatio, 2)
                * exp(-conflict / 0.35)
                * exp(-pow(coefficientOfVariation / 0.50, 2))
            let prior = 1 - 0.90 * confidence
            estimatedLengths.append(prior * reference + (1 - prior) * sensorLength)
            distancePriors.append(prior)

            let sine = selected.map { sin($0.heading) }.reduce(0, +) / Double(selected.count)
            let cosine = selected.map { cos($0.heading) }.reduce(0, +) / Double(selected.count)
            let measured = atan2(sine, cosine)
            let resultant = max(hypot(sine, cosine), 1e-9)
            let dispersionDegrees = sqrt(max(0, -2 * log(resultant))) * 180 / .pi
            let expected = atan2(
                route[segment + 1].y - route[segment].y,
                route[segment + 1].x - route[segment].x
            )
            let correction = wrap(expected - measured)
            let disagreementDegrees = abs(correction * 180 / .pi)
            let turnErrorDegrees = segment == 0
                ? 0
                : abs(abs(turns[segment - 1].signedAngleDegrees) - 90)
            let headingConfidence = exp(-pow(disagreementDegrees / 10, 2))
                * exp(-pow(dispersionDegrees / 8, 2))
                * exp(-pow(turnErrorDegrees / 20, 2))
            let headingPrior = 1 - 0.90 * headingConfidence
            segmentHeadings.append(wrap(measured + headingPrior * correction))
        }

        var result: [Step] = []
        for segment in 0..<4 {
            let selected = steps.filter { $0.segmentIndex == segment }
            let rawSum = selected.map(\.length).reduce(0, +)
            for step in selected {
                result.append(Step(
                    frameIndex: step.frameIndex,
                    time: step.time,
                    length: rawSum > 1e-9
                        ? step.length / rawSum * estimatedLengths[segment]
                        : 0,
                    heading: segmentHeadings[segment],
                    sensorHeading: step.sensorHeading,
                    magneticMagnitude: step.magneticMagnitude,
                    alignedMagneticVector: step.alignedMagneticVector,
                    segmentIndex: segment
                ))
            }
        }
        return (
            result,
            estimatedLengths,
            distancePriors,
            segmentHeadings.map { $0 * 180 / .pi }
        )
    }

    private static func controlledDiagnostics(
        profile: ControlledMotionProfile,
        turns: [TurnRegion],
        removed: Int,
        postEndpoint: Int,
        counts: [Int],
        lengths: [Double],
        priors: [Double],
        headings: [Double],
        partitionMethod: String? = nil,
        rejectionReason: String?
    ) -> ControlledMotionDiagnostics {
        ControlledMotionDiagnostics(
            releaseID: ControlledMotionProfile.releaseID,
            profile: ControlledMotionProfile.profileName,
            deploymentStatus: ControlledMotionProfile.deploymentStatus,
            calibrationSourceKey: profile.calibrationSourceKey,
            turnRegionsDetected: turns.count,
            turnTranslationStepsRemoved: removed,
            postEndpointPeaksRemoved: postEndpoint,
            segmentStepCounts: counts,
            estimatedSegmentLengthsM: lengths,
            effectiveDistancePrior: priors,
            segmentHeadingsDegrees: headings,
            turnPartitionMethod: partitionMethod,
            legacyParticleFilterExcluded: true,
            rejectionReason: rejectionReason
        )
    }

    private static func buildPDR(start: XYPoint, steps: [Step]) -> [XYPoint] {
        var track = [start]
        track.reserveCapacity(steps.count + 1)
        for step in steps {
            let last = track.last!
            track.append(XYPoint(
                x: last.x + step.length * cos(step.heading),
                y: last.y + step.length * sin(step.heading)
            ))
        }
        return track
    }

    private static func particleFilter(
        start: XYPoint,
        steps: [Step],
        map: MagneticMap,
        settings: AlgorithmSettings,
        seed: UInt64,
        progress: @Sendable (Double, String) -> Void
    ) throws -> (
        track: [XYPoint],
        confidence: [PFConfidenceSample],
        health: [LocalizationHealthSample],
        recoveries: [LocalizationHealthSample]
    ) {
        let count = 2_000
        var rng = SeededGenerator(seed: seed)
        let startMap = map.sample(x: start.x, y: start.y)
        let initialObserved = steps.first?.magneticMagnitude ?? startMap
        let startVector = map.sampleVector(x: start.x, y: start.y)
        let initialObservedVector = steps.first?.alignedMagneticVector
        var particles = (0..<count).map { _ in
            Particle(
                x: start.x + rng.normal(standardDeviation: 0.20),
                y: start.y + rng.normal(standardDeviation: 0.20),
                headingBias: settings.jointCalibrationEnabled
                    ? rng.normal(standardDeviation: 0.06) : 0,
                stepScale: settings.jointCalibrationEnabled
                    ? min(max(1 + rng.normal(standardDeviation: 0.08), 0.70), 1.25) : 1,
                magneticBias: initialObserved - startMap,
                magneticBiasX: (initialObservedVector?.x ?? 0) - (startVector?.x ?? 0),
                magneticBiasY: (initialObservedVector?.y ?? 0) - (startVector?.y ?? 0),
                magneticBiasZ: (initialObservedVector?.z ?? 0) - (startVector?.z ?? 0),
                previousMapValue: startMap,
                weight: 1 / Double(count)
            )
        }
        var track = [start]
        var confidence: [PFConfidenceSample] = []
        var health: [LocalizationHealthSample] = []
        var recoveries: [LocalizationHealthSample] = []
        var previousObserved = initialObserved
        var lowStreak = 0
        var lostStreak = 0

        for (stepIndex, step) in steps.enumerated() {
            try Task.checkCancellation()
            var weightSum = 0.0
            for index in particles.indices {
                var particle = particles[index]
                if settings.jointCalibrationEnabled {
                    particle.stepScale = min(max(
                        particle.stepScale + rng.normal(standardDeviation: 0.004),
                        0.70
                    ), 1.25)
                    particle.headingBias = wrap(
                        particle.headingBias + rng.normal(standardDeviation: 0.003)
                    )
                }
                let headingNoise = rng.normal(standardDeviation: 0.03)
                let stepNoise = rng.normal(standardDeviation: 0.025)
                let distance = max(0.05, step.length * particle.stepScale + stepNoise)
                particle.x += distance * cos(step.heading + particle.headingBias + headingNoise)
                particle.y += distance * sin(step.heading + particle.headingBias + headingNoise)
                let inBounds = map.contains(x: particle.x, y: particle.y)
                let mapValue = map.sample(x: particle.x, y: particle.y)
                let absoluteError = mapValue + particle.magneticBias - step.magneticMagnitude
                let differentialError = (mapValue - particle.previousMapValue)
                    - (step.magneticMagnitude - previousObserved)
                var exponent = -0.5 * (
                    pow(absoluteError / 1.8, 2) + pow(differentialError / 0.65, 2)
                )
                if let mapVector = map.sampleVector(x: particle.x, y: particle.y),
                   let observedVector = step.alignedMagneticVector {
                    let errorX = mapVector.x + particle.magneticBiasX - observedVector.x
                    let errorY = mapVector.y + particle.magneticBiasY - observedVector.y
                    let errorZ = mapVector.z + particle.magneticBiasZ - observedVector.z
                    exponent += -0.5 * 0.45 * (
                        pow(errorX / 6.0, 2) + pow(errorY / 6.0, 2) + pow(errorZ / 6.0, 2)
                    )
                    particle.magneticBiasX += 0.01 * (observedVector.x - mapVector.x - particle.magneticBiasX)
                    particle.magneticBiasY += 0.01 * (observedVector.y - mapVector.y - particle.magneticBiasY)
                    particle.magneticBiasZ += 0.01 * (observedVector.z - mapVector.z - particle.magneticBiasZ)
                }
                let likelihood = max(exp(exponent), 1e-12) * (inBounds ? 1 : 1e-7)
                particle.weight *= likelihood
                particle.magneticBias += 0.02 * (step.magneticMagnitude - mapValue - particle.magneticBias)
                particle.previousMapValue = mapValue
                particles[index] = particle
                weightSum += particle.weight
            }
            if !weightSum.isFinite || weightSum <= 1e-300 {
                let equalWeight = 1 / Double(count)
                for index in particles.indices { particles[index].weight = equalWeight }
            } else {
                for index in particles.indices { particles[index].weight /= weightSum }
            }

            let estimate = weightedMean(particles)
            let rawPoint = XYPoint(x: estimate.x, y: estimate.y)
            let point = smooth(rawPoint, previous: track.last!, settings: settings)
            track.append(point)
            let ess = 1 / particles.reduce(0) { $0 + $1.weight * $1.weight }
            let sample = confidenceSample(particles: particles, essRatio: ess / Double(count))
            confidence.append(sample)
            if sample.score < 0.38 { lowStreak += 1 } else { lowStreak = 0 }
            if sample.score < 0.20 { lostStreak += 1 } else { lostStreak = 0 }
            let status: String
            let action: String
            if lostStreak >= 5 {
                status = "lost"
                action = "reinitialize"
            } else if lowStreak >= 3 {
                status = "ambiguous"
                action = "expand_search"
            } else {
                status = "healthy"
                action = "none"
            }
            let event = LocalizationHealthSample(
                stepIndex: stepIndex + 1,
                status: status,
                score: sample.score,
                lowConfidenceStreak: lowStreak,
                lostStreak: lostStreak,
                action: action,
                reasonCodes: status == "healthy" ? [] : ["low_particle_concentration"],
                recoveryCount: recoveries.count
            )
            health.append(event)
            if lostStreak == 5 {
                recoveries.append(event)
                injectParticles(
                    into: &particles,
                    around: point,
                    map: map,
                    observed: step.magneticMagnitude,
                    rng: &rng,
                    ratio: 0.35
                )
                lowStreak = 0
                lostStreak = 0
            } else if ess < Double(count) * 0.55 {
                particles = systematicResample(particles, rng: &rng)
            }
            previousObserved = step.magneticMagnitude
            progress(
                0.30 + 0.62 * Double(stepIndex + 1) / Double(steps.count),
                "原生 PF：第 \(stepIndex + 1)/\(steps.count) 步"
            )
        }
        return (track, confidence, health, recoveries)
    }

    private static func weightedMean(_ particles: [Particle]) -> (x: Double, y: Double) {
        particles.reduce(into: (x: 0.0, y: 0.0)) { result, particle in
            result.x += particle.x * particle.weight
            result.y += particle.y * particle.weight
        }
    }

    private static func smooth(
        _ current: XYPoint,
        previous: XYPoint,
        settings: AlgorithmSettings
    ) -> XYPoint {
        switch settings.smoothingMode {
        case .none:
            return current
        case .ema:
            return XYPoint(
                x: settings.smoothingAlpha * previous.x + (1 - settings.smoothingAlpha) * current.x,
                y: settings.smoothingAlpha * previous.y + (1 - settings.smoothingAlpha) * current.y
            )
        case .motionAdaptive:
            let displacement = hypot(current.x - previous.x, current.y - previous.y)
            let historyWeight = min(max(settings.smoothingAlpha * exp(-displacement), 0.05), 0.85)
            return XYPoint(
                x: historyWeight * previous.x + (1 - historyWeight) * current.x,
                y: historyWeight * previous.y + (1 - historyWeight) * current.y
            )
        }
    }

    private static func confidenceSample(
        particles: [Particle],
        essRatio: Double
    ) -> PFConfidenceSample {
        let center = weightedMean(particles)
        var xx = 0.0
        var yy = 0.0
        var xy = 0.0
        var distances: [(Double, Double)] = []
        distances.reserveCapacity(particles.count)
        for particle in particles {
            let dx = particle.x - center.x
            let dy = particle.y - center.y
            xx += particle.weight * dx * dx
            yy += particle.weight * dy * dy
            xy += particle.weight * dx * dy
            distances.append((hypot(dx, dy), particle.weight))
        }
        let trace = xx + yy
        let discriminant = sqrt(max(pow(xx - yy, 2) + 4 * xy * xy, 0))
        let major = sqrt(max((trace + discriminant) / 2, 0))
        let minor = sqrt(max((trace - discriminant) / 2, 0))
        distances.sort { $0.0 < $1.0 }
        func radius(_ quantile: Double) -> Double {
            var accumulated = 0.0
            for (distance, weight) in distances {
                accumulated += weight
                if accumulated >= quantile { return distance }
            }
            return distances.last?.0 ?? 0
        }
        let radius80 = radius(0.80)
        let radius95 = radius(0.95)
        let concentration = exp(-radius80 / 1.5)
        let score = min(max(0.65 * concentration + 0.35 * essRatio, 0), 1)
        let level = score >= 0.70 ? "high" : score >= 0.40 ? "medium" : "low"
        return PFConfidenceSample(
            radius95M: radius95,
            coreRadius80M: radius80,
            sigmaMajorM: major,
            sigmaMinorM: minor,
            essRatio: essRatio,
            measurementInformation: concentration,
            globalAmbiguity: 1 - concentration,
            score: score,
            level: level
        )
    }

    private static func systematicResample(
        _ particles: [Particle],
        rng: inout SeededGenerator
    ) -> [Particle] {
        let count = particles.count
        let start = rng.uniform() / Double(count)
        var cumulative = particles[0].weight
        var source = 0
        var result: [Particle] = []
        result.reserveCapacity(count)
        for index in 0..<count {
            let target = start + Double(index) / Double(count)
            while target > cumulative && source + 1 < count {
                source += 1
                cumulative += particles[source].weight
            }
            var particle = particles[source]
            particle.weight = 1 / Double(count)
            result.append(particle)
        }
        return result
    }

    private static func injectParticles(
        into particles: inout [Particle],
        around point: XYPoint,
        map: MagneticMap,
        observed: Double,
        rng: inout SeededGenerator,
        ratio: Double
    ) {
        let count = min(Int(Double(particles.count) * ratio), particles.count)
        for index in 0..<count {
            let x = min(max(point.x + rng.normal(standardDeviation: 1.0), map.minX), map.maxX)
            let y = min(max(point.y + rng.normal(standardDeviation: 1.0), map.minY), map.maxY)
            let value = map.sample(x: x, y: y)
            particles[index] = Particle(
                x: x,
                y: y,
                headingBias: rng.normal(standardDeviation: 0.15),
                stepScale: min(max(1 + rng.normal(standardDeviation: 0.12), 0.70), 1.25),
                magneticBias: observed - value,
                magneticBiasX: 0,
                magneticBiasY: 0,
                magneticBiasZ: 0,
                previousMapValue: value,
                weight: 1 / Double(particles.count)
            )
        }
        let total = particles.reduce(0) { $0 + $1.weight }
        for index in particles.indices { particles[index].weight /= total }
    }

    private static func alignedErrors(track: [XYPoint], route: [XYPoint]) -> [Double] {
        guard track.count > 1 else { return [0] }
        return track.enumerated().map { index, trackPoint in
            let progress = Double(index) / Double(track.count - 1)
            let truth = point(on: route, progress: progress)
            return hypot(trackPoint.x - truth.x, trackPoint.y - truth.y)
        }
    }

    private static func crossTrackErrors(track: [XYPoint], route: [XYPoint]) -> [Double] {
        guard route.count >= 2 else { return track.map { _ in 0 } }
        return track.map { point in
            zip(route, route.dropFirst()).map { start, end in
                let dx = end.x - start.x
                let dy = end.y - start.y
                let squaredLength = dx * dx + dy * dy
                let ratio = squaredLength > 1e-12
                    ? min(max(((point.x - start.x) * dx + (point.y - start.y) * dy) / squaredLength, 0), 1)
                    : 0
                let projectionX = start.x + ratio * dx
                let projectionY = start.y + ratio * dy
                return hypot(point.x - projectionX, point.y - projectionY)
            }.min() ?? 0
        }
    }

    private static func point(on route: [XYPoint], progress: Double) -> XYPoint {
        guard route.count > 1 else { return route.first ?? XYPoint(x: 0, y: 0) }
        let lengths = zip(route, route.dropFirst()).map { hypot($1.x - $0.x, $1.y - $0.y) }
        let total = lengths.reduce(0, +)
        var target = min(max(progress, 0), 1) * total
        for index in lengths.indices {
            if target <= lengths[index] || index == lengths.count - 1 {
                let ratio = lengths[index] > 0 ? target / lengths[index] : 0
                return XYPoint(
                    x: route[index].x + (route[index + 1].x - route[index].x) * ratio,
                    y: route[index].y + (route[index + 1].y - route[index].y) * ratio
                )
            }
            target -= lengths[index]
        }
        return route.last!
    }

    private static func statistics(_ values: [Double]) -> ErrorStatistics? {
        guard !values.isEmpty else { return nil }
        let sorted = values.sorted()
        return ErrorStatistics(
            mean: mean(values),
            median: percentile(sorted, 0.50),
            p95: percentile(sorted, 0.95),
            final: values.last ?? 0
        )
    }

    private static func percentile(_ sorted: [Double], _ quantile: Double) -> Double {
        guard !sorted.isEmpty else { return 0 }
        let position = min(max(quantile, 0), 1) * Double(sorted.count - 1)
        let lower = Int(floor(position))
        let upper = min(lower + 1, sorted.count - 1)
        let fraction = position - Double(lower)
        return sorted[lower] + (sorted[upper] - sorted[lower]) * fraction
    }

    private static func mean(_ values: [Double]) -> Double {
        values.isEmpty ? 0 : values.reduce(0, +) / Double(values.count)
    }

    private static func median(_ values: [Double]) -> Double {
        percentile(values.sorted(), 0.5)
    }

    private static func standardDeviation(_ values: [Double]) -> Double {
        guard values.count > 1 else { return 0 }
        let average = mean(values)
        return sqrt(values.reduce(0) { $0 + pow($1 - average, 2) } / Double(values.count))
    }

    private static func headingOfFirstSegment(_ route: [XYPoint]) -> Double {
        guard route.count >= 2 else { return 0 }
        return atan2(route[1].y - route[0].y, route[1].x - route[0].x)
    }

    private static func wrap(_ angle: Double) -> Double {
        atan2(sin(angle), cos(angle))
    }

    private static func stableSeed(_ value: String) -> UInt64 {
        value.utf8.reduce(0xcbf29ce484222325) { ($0 ^ UInt64($1)) &* 0x100000001b3 }
    }

    private static func routeLabel(for datasetKey: String) -> String {
        let normalized = datasetKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let group = normalized.replacingOccurrences(
            of: #"_\d+$"#,
            with: "",
            options: .regularExpression
        )
        return group.isEmpty ? "custom" : group
    }
}
