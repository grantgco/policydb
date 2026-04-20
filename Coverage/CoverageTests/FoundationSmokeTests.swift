import XCTest
@testable import Coverage

/// End-to-end smoke test: exercises the exact path `FoundationSmokeTestView.run()`
/// takes at app launch. Uses the real `~/.policydb/policydb.sqlite` + `config.yaml`
/// when present; XCTSkips when either is absent so CI stays green.
final class FoundationSmokeTests: XCTestCase {

    func testRealFoundationPath() async throws {
        let dbURL = FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent(".policydb/policydb.sqlite")
        guard FileManager.default.fileExists(atPath: dbURL.path) else {
            throw XCTSkip("No real PolicyDB at \(dbURL.path)")
        }

        let report = await FoundationSmokeTestView.run()

        guard case let .ok(schema, carriers, statuses) = report else {
            XCTFail("Expected .ok, got \(report)")
            return
        }

        XCTAssertGreaterThanOrEqual(
            schema, DatabaseManager.minimumSupportedSchemaVersion,
            "Real DB schema \(schema) below minimum \(DatabaseManager.minimumSupportedSchemaVersion)"
        )
        XCTAssertLessThanOrEqual(
            schema, DatabaseManager.maximumSupportedSchemaVersion,
            "Real DB schema \(schema) exceeds supported \(DatabaseManager.maximumSupportedSchemaVersion) — bump the max"
        )
        XCTAssertGreaterThan(carriers, 0, "Real config should have carriers configured")
        XCTAssertGreaterThan(statuses, 0, "Real config should have renewal statuses configured")
    }
}
