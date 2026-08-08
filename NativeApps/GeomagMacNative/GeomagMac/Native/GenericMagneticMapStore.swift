import Foundation

struct GenericMagneticMapSample: Codable, Hashable, Sendable {
    let x: Double
    let y: Double
    let magneticNormUT: Double
    var magneticXUT: Double? = nil
    var magneticYUT: Double? = nil
    var magneticZUT: Double? = nil
    var varianceUT2: Double? = nil
    var observationCount: Int? = nil
    var directionCount: Int? = nil
    var gradientXUTPerM: Double? = nil
    var gradientYUTPerM: Double? = nil
}

struct GenericMagneticMapDocument: Codable, Hashable, Identifiable, Sendable {
    static let schemaVersion = 1

    let schemaVersion: Int
    let id: String
    let name: String
    let createdAt: Date
    let sourceDatasetKeys: [String]
    let referenceRoute: [XYPoint]
    let samples: [GenericMagneticMapSample]
    let supportRadiusM: Double
    var coordinateFrame: String? = nil
    var gridCellSizeM: Double? = nil

    var sourceLabel: String { sourceDatasetKeys.joined(separator: ", ") }

    var vectorSampleRatio: Double {
        guard !samples.isEmpty else { return 0 }
        return Double(samples.count { $0.magneticXUT != nil && $0.magneticYUT != nil && $0.magneticZUT != nil })
            / Double(samples.count)
    }

    var gridCoverageRatio: Double? {
        guard let cell = gridCellSizeM, let bounds else { return nil }
        let columns = max(Int(ceil((bounds.maxX - bounds.minX) / cell)), 1)
        let rows = max(Int(ceil((bounds.maxY - bounds.minY) / cell)), 1)
        return min(Double(samples.count) / Double(columns * rows), 1)
    }

    var bounds: CoordinateBounds? {
        guard let first = samples.first else { return nil }
        let minX = samples.dropFirst().reduce(first.x) { min($0, $1.x) } - supportRadiusM
        let maxX = samples.dropFirst().reduce(first.x) { max($0, $1.x) } + supportRadiusM
        let minY = samples.dropFirst().reduce(first.y) { min($0, $1.y) } - supportRadiusM
        let maxY = samples.dropFirst().reduce(first.y) { max($0, $1.y) } + supportRadiusM
        return CoordinateBounds(minX: minX, maxX: maxX, minY: minY, maxY: maxY)
    }

    func validated() throws -> GenericMagneticMapDocument {
        guard schemaVersion == Self.schemaVersion,
              !id.isEmpty,
              !name.isEmpty,
              !sourceDatasetKeys.isEmpty,
              referenceRoute.count >= 2,
              samples.count >= 8,
              supportRadiusM.isFinite,
              supportRadiusM > 0,
              gridCellSizeM.map({ $0.isFinite && $0 > 0 }) ?? true,
              samples.allSatisfy({
                  $0.x.isFinite && $0.y.isFinite
                      && $0.magneticNormUT.isFinite
                      && 10 <= $0.magneticNormUT
                      && $0.magneticNormUT <= 100
                      && [$0.magneticXUT, $0.magneticYUT, $0.magneticZUT]
                          .compactMap { $0 }.allSatisfy(\.isFinite)
                      && ($0.varianceUT2.map { $0.isFinite && $0 >= 0 } ?? true)
                      && [$0.gradientXUTPerM, $0.gradientYUTPerM]
                          .compactMap { $0 }.allSatisfy(\.isFinite)
              }) else {
            throw GenericMagneticMapStore.StoreError.malformedMap(name)
        }
        return self
    }
}

enum GenericMagneticMapStore {
    enum StoreError: LocalizedError {
        case malformedMap(String)
        case invalidName
        case duplicateMap(String)
        case builtInMapCannotBeRemoved
        case incompatibleMaps

        var errorDescription: String? {
            switch self {
            case let .malformedMap(name): "磁图 \(name) 的格式或样本不完整。"
            case .invalidName: "请填写有效的磁图名称。"
            case let .duplicateMap(name): "已经存在名为 \(name) 的磁图，请先删除或改名。"
            case .builtInMapCannotBeRemoved: "内置示例磁图不能删除。"
            case .incompatibleMaps: "两次采集的磁图名称、坐标系或网格尺寸不一致，不能合并。"
            }
        }
    }

    struct Entry: Identifiable, Hashable, Sendable {
        let document: GenericMagneticMapDocument
        let isBuiltIn: Bool

        var id: String { document.id }
    }

    static func loadAll() throws -> [Entry] {
        var entries = try loadBuiltIn().map { Entry(document: $0, isBuiltIn: true) }
        let builtInIDs = Set(entries.map(\.id))
        entries.append(contentsOf: try loadUser().filter { !builtInIDs.contains($0.id) }.map {
            Entry(document: $0, isBuiltIn: false)
        })
        return entries.sorted {
            if $0.isBuiltIn != $1.isBuiltIn { return $0.isBuiltIn }
            return $0.document.name.localizedStandardCompare($1.document.name) == .orderedAscending
        }
    }

    static func save(_ document: GenericMagneticMapDocument, replacing: Bool = false) throws -> URL {
        let checked = try document.validated()
        let directory = try directoryURL(createIfNeeded: true)
        let url = directory.appendingPathComponent(checked.id).appendingPathExtension("json")
        if !replacing, FileManager.default.fileExists(atPath: url.path) {
            throw StoreError.duplicateMap(checked.name)
        }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(checked).write(to: url, options: .atomic)
        return url
    }

    static func remove(_ entry: Entry) throws {
        guard !entry.isBuiltIn else { throw StoreError.builtInMapCannotBeRemoved }
        let url = try directoryURL(createIfNeeded: true)
            .appendingPathComponent(entry.id)
            .appendingPathExtension("json")
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        try FileManager.default.removeItem(at: url)
    }

    static func merging(
        _ existing: GenericMagneticMapDocument,
        with addition: GenericMagneticMapDocument
    ) throws -> GenericMagneticMapDocument {
        let lhs = try existing.validated()
        let rhs = try addition.validated()
        guard lhs.id == rhs.id,
              lhs.coordinateFrame == rhs.coordinateFrame,
              abs((lhs.gridCellSizeM ?? 0.4) - (rhs.gridCellSizeM ?? 0.4)) < 1e-6 else {
            throw StoreError.incompatibleMaps
        }
        let cell = lhs.gridCellSizeM ?? rhs.gridCellSizeM ?? 0.4
        struct Key: Hashable { let x: Int; let y: Int }
        var grouped: [Key: [GenericMagneticMapSample]] = [:]
        for sample in lhs.samples + rhs.samples {
            grouped[Key(x: Int(floor(sample.x / cell)), y: Int(floor(sample.y / cell))), default: []]
                .append(sample)
        }
        var mergedSamples: [GenericMagneticMapSample] = []
        for values in grouped.values {
            func median(_ numbers: [Double]) -> Double {
                let sorted = numbers.sorted()
                let middle = sorted.count / 2
                return sorted.count.isMultiple(of: 2)
                    ? (sorted[middle - 1] + sorted[middle]) / 2
                    : sorted[middle]
            }
            func optionalMedian(_ values: [Double?]) -> Double? {
                let numbers = values.compactMap { $0 }
                return numbers.isEmpty ? nil : median(numbers)
            }
            let x = median(values.map(\.x))
            let y = median(values.map(\.y))
            let norm = median(values.map(\.magneticNormUT))
            let bx = optionalMedian(values.map(\.magneticXUT))
            let by = optionalMedian(values.map(\.magneticYUT))
            let bz = optionalMedian(values.map(\.magneticZUT))
            let variance = optionalMedian(values.map(\.varianceUT2))
            let observations = values.compactMap(\.observationCount).reduce(0, +)
            let directions = min(values.compactMap(\.directionCount).reduce(0, +), 8)
            let gradientX = optionalMedian(values.map(\.gradientXUTPerM))
            let gradientY = optionalMedian(values.map(\.gradientYUTPerM))
            var sample = GenericMagneticMapSample(x: x, y: y, magneticNormUT: norm)
            sample.magneticXUT = bx
            sample.magneticYUT = by
            sample.magneticZUT = bz
            sample.varianceUT2 = variance
            sample.observationCount = observations
            sample.directionCount = directions
            sample.gradientXUTPerM = gradientX
            sample.gradientYUTPerM = gradientY
            mergedSamples.append(sample)
        }
        mergedSamples.sort { lhs, rhs in
            lhs.y == rhs.y ? lhs.x < rhs.x : lhs.y < rhs.y
        }
        return try GenericMagneticMapDocument(
            schemaVersion: GenericMagneticMapDocument.schemaVersion,
            id: lhs.id,
            name: lhs.name,
            createdAt: Date(),
            sourceDatasetKeys: Array(Set(lhs.sourceDatasetKeys + rhs.sourceDatasetKeys)).sorted(),
            referenceRoute: lhs.referenceRoute + rhs.referenceRoute,
            samples: mergedSamples,
            supportRadiusM: max(lhs.supportRadiusM, rhs.supportRadiusM),
            coordinateFrame: lhs.coordinateFrame,
            gridCellSizeM: cell
        ).validated()
    }

    static func directoryURL(createIfNeeded: Bool) throws -> URL {
        let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        let directory = applicationSupport
            .appendingPathComponent("GeomagMacNative", isDirectory: true)
            .appendingPathComponent("MagneticMaps", isDirectory: true)
        if createIfNeeded {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true
            )
        }
        return directory
    }

    static func identifier(from name: String) throws -> String {
        let normalized = name.lowercased().unicodeScalars.map { scalar -> Character in
            if CharacterSet.alphanumerics.contains(scalar) { return Character(String(scalar)) }
            return "_"
        }
        let value = String(normalized)
            .split(separator: "_", omittingEmptySubsequences: true)
            .joined(separator: "_")
        guard !value.isEmpty else { throw StoreError.invalidName }
        return value
    }

    private static func loadUser() throws -> [GenericMagneticMapDocument] {
        let directory = try directoryURL(createIfNeeded: true)
        return try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )
        .filter { $0.pathExtension.lowercased() == "json" }
        .compactMap { try? decode($0) }
    }

    private static func loadBuiltIn() throws -> [GenericMagneticMapDocument] {
        guard let root = Bundle.main.resourceURL else { return [] }
        let candidates = [
            root.appendingPathComponent("MagneticMaps", isDirectory: true),
            root,
        ]
        var urls: [URL] = []
        for directory in candidates where FileManager.default.fileExists(atPath: directory.path) {
            let found = try FileManager.default.contentsOfDirectory(
                at: directory,
                includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            ).filter { $0.lastPathComponent.hasPrefix("magnetic_map_") && $0.pathExtension == "json" }
            urls.append(contentsOf: found)
        }
        return try Array(Set(urls)).map(decode)
    }

    private static func decode(_ url: URL) throws -> GenericMagneticMapDocument {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(
            GenericMagneticMapDocument.self,
            from: Data(contentsOf: url)
        ).validated()
    }
}
