import XCTest
@testable import GeomagMac

final class NativeEngineTests: XCTestCase {
    func testTurnContinuityGuardSuppressesSidewaysModeJumpWithoutMapCorner() {
        let previous = XYPoint(x: 5.8, y: 4.8)
        let raw = XYPoint(x: 5.57, y: 4.77)
        let corrected = NativePositioningEngine.constrainedTurnDeparture(
            raw,
            previous: previous,
            heading: -.pi / 2,
            stepLength: 0.30
        )

        XCTAssertEqual(corrected.x, previous.x, accuracy: 1e-9)
        XCTAssertLessThan(corrected.y, previous.y)
        XCTAssertGreaterThanOrEqual(corrected.y, previous.y - 0.45)
    }

    func testRoute13HeldOutCaptureProducesRealPFAndConfidence() throws {
        let result = try runBundledQuery(key: "route_13_3")

        XCTAssertEqual(result.branch, "generic_map_pf_native_swift")
        XCTAssertEqual(result.pfTrack.count, (result.stepsDetected ?? 0) + 1)
        XCTAssertEqual(result.pfConfidenceHistory?.count, result.stepsDetected)
        XCTAssertFalse(result.pfTrack.isEmpty)
        XCTAssertLessThan(
            result.pfCrossTrackErrorStats?.mean ?? .infinity,
            result.pdrCrossTrackErrorStats?.mean ?? 0
        )
        XCTAssertTrue(0.70...1.30 ~= (result.pfPathLengthRatio ?? 0))
        XCTAssertLessThan(result.pfClosureErrorM ?? .infinity, 0.60)
        XCTAssertNotNil(result.pfAlongTrackErrorStats)
        XCTAssertLessThan(result.pfTurnAngleErrorStats?.mean ?? .infinity, 30)
        XCTAssertNil(result.controlledMotionDiagnostics)
        XCTAssertNil(result.magneticFusionDiagnostics)
    }

    func testRoute15HeldOutCaptureProducesRealPFAndConfidence() throws {
        let result = try runBundledQuery(key: "route_15_3")

        XCTAssertEqual(result.pfTrack.count, (result.stepsDetected ?? 0) + 1)
        XCTAssertEqual(result.pfConfidenceHistory?.count, result.stepsDetected)
        XCTAssertLessThan(
            result.pfCrossTrackErrorStats?.mean ?? .infinity,
            result.pdrCrossTrackErrorStats?.mean ?? 0
        )
        XCTAssertTrue(0.70...1.30 ~= (result.pfPathLengthRatio ?? 0))
        XCTAssertLessThan(result.pfClosureErrorM ?? .infinity, 0.20)
        XCTAssertNotNil(result.pfAlongTrackErrorStats)
        XCTAssertLessThan(result.pfTurnAngleErrorStats?.mean ?? .infinity, 45)
        XCTAssertNotNil(result.finalPFConfidence)
        let map = try loadBundledMap(group: "route_15")
        XCTAssertGreaterThan(map.vectorSampleRatio, 0.95)
        XCTAssertLessThanOrEqual(map.supportRadiusM, 0.25)
        XCTAssertNotNil(map.pdrStepLengthScale)
        XCTAssertEqual(map.samplesFollowPath, true)
    }

    func testReferenceCaptureCannotLocateAgainstItsOwnMap() throws {
        let map = try loadBundledMap(group: "route_13")
        let directory = try NativePositioningEngine.bundledDatasetURL(key: "route_13_2")
        let selfMap = GenericMagneticMapDocument(
            schemaVersion: map.schemaVersion,
            id: map.id,
            name: map.name,
            createdAt: map.createdAt,
            sourceDatasetKeys: ["route_13_2"],
            referenceRoute: map.referenceRoute,
            samples: map.samples,
            supportRadiusM: map.supportRadiusM
        )

        XCTAssertThrowsError(try NativePositioningEngine.run(
            request: .init(
                datasetKey: "route_13_2",
                datasetDirectory: directory,
                route: route(for: "route_13"),
                initialHeadingDegrees: nil,
                activeStartTime: nil,
                activeEndTime: nil,
                settings: .optimized,
                magneticMap: selfMap,
                localizationMode: selfMap.localizationMode
            ),
            progress: { _, _ in }
        )) { error in
            XCTAssertEqual(error as? NativePositioningEngine.EngineError, .mapSourceCannotLocateItself)
        }
    }

    func testMapBuilderAcceptsUserPolylineInsteadOfNamedRoute() throws {
        let directory = try NativePositioningEngine.bundledDatasetURL(key: "route_13_2")
        let inspection = try DatasetValidator.inspect(selectedURL: directory)
        let arbitraryRoute = [
            XYPoint(x: 1, y: 1),
            XYPoint(x: 1, y: 3.4),
            XYPoint(x: 4.2, y: 3.4),
            XYPoint(x: 4.2, y: 1),
            XYPoint(x: 1, y: 1),
        ]
        let map = try NativePositioningEngine.buildGenericMagneticMap(
            name: "arbitrary_curve",
            datasetKey: "first_capture",
            datasetDirectory: directory,
            route: arbitraryRoute,
            initialHeadingDegrees: nil,
            activeStartTime: inspection.activeInterval.startTime,
            activeEndTime: inspection.activeInterval.endTime,
            progress: { _, _ in }
        )

        XCTAssertEqual(map.referenceRoute, arbitraryRoute)
        XCTAssertGreaterThanOrEqual(map.samples.count, 8)
        XCTAssertEqual(map.samplesFollowPath, true)
        XCTAssertEqual(map.samples.first?.x, arbitraryRoute.first?.x)
        XCTAssertEqual(map.samples.first?.y, arbitraryRoute.first?.y)
    }

    func testGenericMapJSONRoundTripPreservesPersistentFormat() throws {
        let expected = try loadBundledMap(group: "route_15")
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        XCTAssertEqual(
            try decoder.decode(GenericMagneticMapDocument.self, from: encoder.encode(expected)),
            expected
        )
    }

    func testAnchorsBuildVectorGridMapWithoutFixedRouteShape() throws {
        let directory = try NativePositioningEngine.bundledDatasetURL(key: "route_13_2")
        let inspection = try DatasetValidator.inspect(selectedURL: directory)
        let anchors = [
            ImportedSpatialAnchor(
                time: inspection.overlapStart, label: "a", x: 0, y: 0,
                headingDegrees: 0, deviceYawDegrees: 0
            ),
            ImportedSpatialAnchor(
                time: inspection.overlapEnd, label: "b", x: 4, y: 1.5,
                headingDegrees: 20, deviceYawDegrees: 20
            ),
        ]
        let map = try NativePositioningEngine.buildAnchoredGridMagneticMap(
            name: "room_grid", datasetKey: "reference_a", datasetDirectory: directory,
            coordinateFrame: "test-room", anchors: anchors, progress: { _, _ in }
        )

        XCTAssertEqual(map.coordinateFrame, "test-room")
        XCTAssertEqual(map.gridCellSizeM, 0.4)
        XCTAssertGreaterThan(map.vectorSampleRatio, 0.95)
        XCTAssertTrue(map.samples.allSatisfy { ($0.observationCount ?? 0) >= 4 })
        XCTAssertEqual(map.samplesFollowPath, false)
        XCTAssertEqual(map.localizationMode, .roomAreaKnownStart)
    }

    func testRoomAreaModeRequiresExplicitHeadingInsteadOfEvaluationRoute() throws {
        let routeMap = try loadBundledMap(group: "route_13")
        let areaMap = GenericMagneticMapDocument(
            schemaVersion: routeMap.schemaVersion,
            id: "room_area_test",
            name: "Room Area Test",
            createdAt: routeMap.createdAt,
            sourceDatasetKeys: routeMap.sourceDatasetKeys,
            referenceRoute: routeMap.referenceRoute,
            samples: routeMap.samples,
            supportRadiusM: 0.55,
            coordinateFrame: "test-room",
            gridCellSizeM: 0.4,
            samplesFollowPath: false,
            pdrStepLengthScale: routeMap.pdrStepLengthScale
        )
        let directory = try NativePositioningEngine.bundledDatasetURL(key: "route_13_3")

        XCTAssertThrowsError(try NativePositioningEngine.run(
            request: .init(
                datasetKey: "route_13_3",
                datasetDirectory: directory,
                route: route(for: "route_13"),
                initialHeadingDegrees: nil,
                activeStartTime: nil,
                activeEndTime: nil,
                settings: .optimized,
                magneticMap: areaMap,
                localizationMode: .roomAreaKnownStart
            ),
            progress: { _, _ in }
        )) { error in
            XCTAssertEqual(error as? NativePositioningEngine.EngineError, .initialHeadingRequired)
        }
    }

    func testLocalizationModeCannotDisagreeWithMapGeometry() throws {
        let map = try loadBundledMap(group: "route_13")
        let directory = try NativePositioningEngine.bundledDatasetURL(key: "route_13_3")

        XCTAssertThrowsError(try NativePositioningEngine.run(
            request: .init(
                datasetKey: "route_13_3",
                datasetDirectory: directory,
                route: route(for: "route_13"),
                initialHeadingDegrees: 90,
                activeStartTime: nil,
                activeEndTime: nil,
                settings: .optimized,
                magneticMap: map,
                localizationMode: .roomAreaKnownStart
            ),
            progress: { _, _ in }
        )) { error in
            XCTAssertEqual(error as? NativePositioningEngine.EngineError, .localizationModeMismatch)
        }
    }

    func testRoomAreaPFDoesNotReadEvaluationRouteShape() throws {
        let routeMap = try loadBundledMap(group: "route_13")
        let areaMap = GenericMagneticMapDocument(
            schemaVersion: routeMap.schemaVersion,
            id: "room_area_independence",
            name: "Room Area Independence",
            createdAt: routeMap.createdAt,
            sourceDatasetKeys: routeMap.sourceDatasetKeys,
            referenceRoute: routeMap.referenceRoute,
            samples: routeMap.samples,
            supportRadiusM: 0.55,
            coordinateFrame: "test-room",
            gridCellSizeM: 0.4,
            samplesFollowPath: false,
            pdrStepLengthScale: routeMap.pdrStepLengthScale
        )
        let directory = try NativePositioningEngine.bundledDatasetURL(key: "route_13_3")
        func run(route: [XYPoint]) throws -> PositioningResult {
            try NativePositioningEngine.run(
                request: .init(
                    datasetKey: "route_13_3",
                    datasetDirectory: directory,
                    route: route,
                    initialHeadingDegrees: 90,
                    activeStartTime: nil,
                    activeEndTime: nil,
                    settings: .optimized,
                    magneticMap: areaMap,
                    localizationMode: .roomAreaKnownStart
                ),
                progress: { _, _ in }
            )
        }
        let actualTruth = route(for: "route_13")
        let deliberatelyDifferentTruth = [
            actualTruth[0],
            XYPoint(x: 4.2, y: 3.8),
            XYPoint(x: 5.1, y: 3.4),
        ]

        XCTAssertEqual(
            try run(route: actualTruth).pfTrack,
            try run(route: deliberatelyDifferentTruth).pfTrack
        )
    }

    func testRepeatedRoomCapturesMergeByGridMedian() throws {
        let first = try loadBundledMap(group: "route_13")
        let compatible = GenericMagneticMapDocument(
            schemaVersion: first.schemaVersion, id: first.id, name: first.name,
            createdAt: first.createdAt, sourceDatasetKeys: ["second_capture"],
            referenceRoute: first.referenceRoute, samples: first.samples,
            supportRadiusM: first.supportRadiusM,
            coordinateFrame: first.coordinateFrame, gridCellSizeM: first.gridCellSizeM
        )
        let merged = try GenericMagneticMapStore.merging(first, with: compatible)

        XCTAssertEqual(Set(merged.sourceDatasetKeys), ["route_13_1", "second_capture"])
        XCTAssertFalse(merged.samples.isEmpty)
    }

    func testRoomMapQualityReportPreservesMissingObstacleCell() throws {
        let samples = (0..<3).flatMap { y in
            (0..<3).compactMap { x -> GenericMagneticMapSample? in
                guard !(x == 1 && y == 1) else { return nil }
                return GenericMagneticMapSample(
                    x: Double(x) * 0.4 + 0.2,
                    y: Double(y) * 0.4 + 0.2,
                    magneticNormUT: 45,
                    varianceUT2: 1,
                    observationCount: 30,
                    directionCount: 2
                )
            }
        }
        let map = try GenericMagneticMapDocument(
            schemaVersion: 1, id: "quality_room", name: "Quality Room",
            createdAt: Date(), sourceDatasetKeys: ["map_1"],
            referenceRoute: [XYPoint(x: 0, y: 0), XYPoint(x: 1.0, y: 1.0)],
            samples: samples, supportRadiusM: 0.55,
            coordinateFrame: "room-a", gridCellSizeM: 0.4,
            samplesFollowPath: false
        ).validated()

        let quality = try XCTUnwrap(map.qualityReport)
        XCTAssertEqual(quality.coveredCellCount, 8)
        XCTAssertEqual(quality.expectedCellCount, 9)
        XCTAssertEqual(quality.cells.count { $0.status == .missing }, 1)
        XCTAssertEqual(quality.grade, .excellent)
    }

    func testMapMergeRejectsDuplicateCapture() throws {
        let first = try loadBundledMap(group: "route_13")

        XCTAssertThrowsError(try GenericMagneticMapStore.merging(first, with: first)) { error in
            guard case GenericMagneticMapStore.StoreError.duplicateSourceDataset("route_13_1") = error else {
                return XCTFail("Unexpected error: \(error)")
            }
        }
    }

    func testCoreMotionRelativeYawIsRegisteredToKnownInitialHeading() {
        let frameTimes = (0..<100).map { Double($0) * 0.01 }
        let initialHeading = 0.7
        let deviceFrames = frameTimes.map { time -> DeviceHeadingFrame? in
            let yaw = 2.8 + 1.2 * time
            return DeviceHeadingFrame(
                yawRadians: atan2(sin(yaw), cos(yaw)),
                magneticAccuracy: 2
            )
        }
        let resolution = DeviceHeadingResolver.resolve(
            frameTimes: frameTimes,
            gyroHeadings: frameTimes.map { initialHeading + 1.2 * $0 },
            deviceFrames: deviceFrames,
            initialHeading: initialHeading
        )

        XCTAssertTrue(resolution.diagnostics.deviceMotionAccepted)
        XCTAssertEqual(resolution.headings[0], initialHeading, accuracy: 1e-12)
    }

    func testCSVAlignsFirstConfidenceWithFirstPropagatedPFPoint() throws {
        let confidence = [confidenceSample(score: 0.25), confidenceSample(score: 0.75)]
        let result = PositioningResult(
            branch: "test", datasetKey: "alignment", routeLabel: "Alignment",
            routeXY: [XYPoint(x: 0, y: 0)], pdrTrack: [],
            pfTrack: [XYPoint(x: 0, y: 0), XYPoint(x: 1, y: 0), XYPoint(x: 2, y: 0)],
            pdrErrorStats: nil, pfErrorStats: nil, controlledCrossTrackErrorStats: nil,
            closureErrorM: nil, pdrCrossTrackErrorStats: nil, pfCrossTrackErrorStats: nil,
            pdrAlongTrackErrorStats: nil, pfAlongTrackErrorStats: nil,
            pdrTurnAngleErrorStats: nil, pfTurnAngleErrorStats: nil,
            pdrPathLengthRatio: nil, pfPathLengthRatio: nil,
            pdrClosureErrorM: nil, pfClosureErrorM: nil,
            stepsDetected: 2, sensorFramesUsed: nil, fullSensorFrames: nil,
            pfSmoothingMode: nil, pfSmoothingAlpha: nil, headingSnapDegrees: nil,
            stepLengthScale: nil, vectorMapEnabled: nil, pfJointCalibration: nil,
            alignmentMode: nil, mapBounds: nil, activeWalkInterval: nil,
            headingDiagnostics: nil, controlledMotionDiagnostics: nil,
            magneticFusionDiagnostics: nil, pfConfidenceHistory: confidence,
            localizationHealthHistory: nil, localizationRecoveryEvents: nil
        )

        let csv = try XCTUnwrap(String(data: ResultExporter.csvData(result: result), encoding: .utf8))
        let rows = csv.components(separatedBy: .newlines).filter { $0.hasPrefix("pf,") }
        XCTAssertEqual(rows.count, 3)
        XCTAssertEqual(rows[0].components(separatedBy: ",")[6], "")
        XCTAssertEqual(rows[1].components(separatedBy: ",")[6], "0.25000000")
        XCTAssertEqual(rows[2].components(separatedBy: ",")[6], "0.75000000")
    }

    private func runBundledQuery(key: String) throws -> PositioningResult {
        let group = key.replacingOccurrences(of: #"_\d+$"#, with: "", options: .regularExpression)
        let directory = try NativePositioningEngine.bundledDatasetURL(key: key)
        let inspection = try DatasetValidator.inspect(selectedURL: directory)
        let interval = inspection.activeInterval
        return try NativePositioningEngine.run(
            request: .init(
                datasetKey: key,
                datasetDirectory: directory,
                route: route(for: group),
                initialHeadingDegrees: nil,
                activeStartTime: interval.confidence == "低" ? nil : interval.startTime,
                activeEndTime: interval.confidence == "低" ? nil : interval.endTime,
                settings: .optimized,
                magneticMap: try loadBundledMap(group: group),
                localizationMode: .routeCorridorValidation
            ),
            progress: { _, _ in }
        )
    }

    private func loadBundledMap(group: String) throws -> GenericMagneticMapDocument {
        let root = try XCTUnwrap(Bundle.main.resourceURL)
        let url = root.appendingPathComponent("MagneticMaps", isDirectory: true)
            .appendingPathComponent("magnetic_map_\(group).json")
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(GenericMagneticMapDocument.self, from: Data(contentsOf: url)).validated()
    }

    private func route(for group: String) -> [XYPoint] {
        if group == "route_13" {
            return [
                XYPoint(x: 4, y: 3), XYPoint(x: 4, y: 4.8),
                XYPoint(x: 5.8, y: 4.8), XYPoint(x: 5.8, y: 3), XYPoint(x: 4, y: 3),
            ]
        }
        return [
            XYPoint(x: 12.5, y: 0.5), XYPoint(x: 0.5, y: 0.5),
            XYPoint(x: 0.5, y: 1.1), XYPoint(x: 12.5, y: 1.1), XYPoint(x: 12.5, y: 0.5),
        ]
    }

    private func confidenceSample(score: Double) -> PFConfidenceSample {
        PFConfidenceSample(
            radius95M: 1, coreRadius80M: 0.5, sigmaMajorM: 0.4, sigmaMinorM: 0.2,
            essRatio: 0.8, measurementInformation: 0.6, globalAmbiguity: 0.2,
            score: score, level: score > 0.5 ? "high" : "low"
        )
    }
}
