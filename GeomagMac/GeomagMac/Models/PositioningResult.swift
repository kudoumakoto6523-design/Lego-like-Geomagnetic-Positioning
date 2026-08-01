import Foundation

struct XYPoint: Decodable, Hashable {
    let x: Double
    let y: Double

    init(from decoder: Decoder) throws {
        var values = try decoder.unkeyedContainer()
        x = try values.decode(Double.self)
        y = try values.decode(Double.self)
    }
}

struct ErrorStatistics: Decodable {
    let mean: Double
    let median: Double
    let p95: Double
    let final: Double
}

struct PositioningResult: Decodable {
    let branch: String
    let datasetKey: String
    let routeLabel: String
    let routeXY: [XYPoint]
    let pdrTrack: [XYPoint]
    let pfTrack: [XYPoint]
    let pdrErrorStats: ErrorStatistics?
    let pfErrorStats: ErrorStatistics?
    let stepsDetected: Int?
    let sensorFramesUsed: Int?
    let fullSensorFrames: Int?
    let pfSmoothingMode: String?
    let pfSmoothingAlpha: Double?
    let headingSnapDegrees: Double?
    let stepLengthScale: Double?
    let vectorMapEnabled: Bool?
    let pfJointCalibration: Bool?
    let alignmentMode: String?

    enum CodingKeys: String, CodingKey {
        case branch
        case datasetKey = "dataset_key"
        case routeLabel = "route_label"
        case routeXY = "route_xy_m"
        case pdrTrack = "pdr_track"
        case pfTrack = "pf_track"
        case pdrErrorStats = "pdr_error_stats"
        case pfErrorStats = "pf_error_stats"
        case stepsDetected = "steps_detected"
        case sensorFramesUsed = "sensor_frames_used"
        case fullSensorFrames = "full_sensor_frames"
        case pfSmoothingMode = "pf_smoothing_mode"
        case pfSmoothingAlpha = "pf_smoothing_alpha"
        case headingSnapDegrees = "heading_snap_deg"
        case stepLengthScale = "step_length_scale"
        case vectorMapEnabled = "vector_map_enabled"
        case pfJointCalibration = "pf_joint_calibration"
        case alignmentMode = "alignment_mode"
    }
}

extension PositioningResult {
    var displayName: String {
        datasetKey.replacingOccurrences(of: "_", with: " ")
    }
}
