import XCTest
@testable import Coverage

final class ConfigReaderTests: XCTestCase {

    private func writeTempConfig(_ yaml: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("policydb-config-\(UUID().uuidString).yaml")
        try yaml.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    func testReadsRenewalStatusLists() throws {
        let yaml = """
        renewal_statuses:
          - Open
          - In Progress
          - Bound
          - Lost
        renewal_statuses_excluded:
          - Lost
          - Archived
        policy_types:
          - GL
          - Auto
        carriers:
          - Travelers
          - Chubb
        activity_types:
          - Note
          - Call
        log_retention_days: 730
        """
        let url = try writeTempConfig(yaml)
        defer { try? FileManager.default.removeItem(at: url) }

        let config = try ConfigReader.load(from: url)
        XCTAssertEqual(config.renewalStatuses, ["Open", "In Progress", "Bound", "Lost"])
        XCTAssertEqual(config.renewalStatusesExcluded, ["Lost", "Archived"])
        XCTAssertEqual(config.policyTypes, ["GL", "Auto"])
        XCTAssertEqual(config.carriers, ["Travelers", "Chubb"])
        XCTAssertEqual(config.activityTypes, ["Note", "Call"])
        XCTAssertEqual(config.logRetentionDays, 730)
    }

    func testMissingKeysDefaultToEmpty() throws {
        let yaml = "log_retention_days: 365"
        let url = try writeTempConfig(yaml)
        defer { try? FileManager.default.removeItem(at: url) }

        let config = try ConfigReader.load(from: url)
        XCTAssertEqual(config.renewalStatuses, [])
        XCTAssertEqual(config.renewalStatusesExcluded, [])
        XCTAssertEqual(config.policyTypes, [])
        XCTAssertEqual(config.carriers, [])
        XCTAssertEqual(config.activityTypes, [])
        XCTAssertEqual(config.logRetentionDays, 365)
    }

    func testMissingFileThrows() {
        let bogus = URL(fileURLWithPath: "/nonexistent/policydb-config-\(UUID().uuidString).yaml")
        XCTAssertThrowsError(try ConfigReader.load(from: bogus))
    }

    /// The real config file on disk should parse without blowing up.
    /// Skipped cleanly when the file is absent so this doesn't fail in CI.
    func testLoadsRealConfigIfPresent() throws {
        let url = ConfigReader.defaultURL
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw XCTSkip("No config.yaml at \(url.path)")
        }
        let config = try ConfigReader.load(from: url)
        XCTAssertFalse(config.renewalStatuses.isEmpty,
                       "Real config should have at least one renewal status")
    }
}
