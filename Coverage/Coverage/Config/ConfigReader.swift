import Foundation
import Yams

/// Typed view over `~/.policydb/config.yaml`. Read-only from Swift in v1 —
/// mutations live in the Python Settings UI. Missing keys default to empty
/// lists / 0 so a partial config never crashes the app.
struct PolicyDBConfig: Equatable {
    var renewalStatuses: [String]
    var renewalStatusesExcluded: [String]
    var policyTypes: [String]
    var carriers: [String]
    var activityTypes: [String]
    var logRetentionDays: Int

    static let empty = PolicyDBConfig(
        renewalStatuses: [],
        renewalStatusesExcluded: [],
        policyTypes: [],
        carriers: [],
        activityTypes: [],
        logRetentionDays: 0
    )
}

enum ConfigReader {

    static var defaultURL: URL {
        FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent(".policydb/config.yaml")
    }

    static func load(from url: URL = defaultURL) throws -> PolicyDBConfig {
        let text = try String(contentsOf: url, encoding: .utf8)
        guard let raw = try Yams.load(yaml: text) as? [String: Any] else {
            return .empty
        }
        return PolicyDBConfig(
            renewalStatuses: stringList(raw["renewal_statuses"]),
            renewalStatusesExcluded: stringList(raw["renewal_statuses_excluded"]),
            policyTypes: stringList(raw["policy_types"]),
            carriers: stringList(raw["carriers"]),
            activityTypes: stringList(raw["activity_types"]),
            logRetentionDays: raw["log_retention_days"] as? Int ?? 0
        )
    }

    /// YAML lists can decode as `[String]`, `[Any]`, or a single string — tolerate all three.
    private static func stringList(_ value: Any?) -> [String] {
        switch value {
        case let strings as [String]:
            return strings
        case let anys as [Any]:
            return anys.compactMap { $0 as? String ?? ($0 as? CustomStringConvertible).map { String(describing: $0) } }
        case let single as String:
            return [single]
        default:
            return []
        }
    }
}
