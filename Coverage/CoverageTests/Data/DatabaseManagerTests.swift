import XCTest
import GRDB
@testable import Coverage

final class DatabaseManagerTests: XCTestCase {

    private func makeTempDB(schemaVersion: Int = 163) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("policydb-\(UUID().uuidString).sqlite")
        let dbQueue = try DatabaseQueue(path: url.path)
        try dbQueue.write { db in
            try db.execute(sql: "PRAGMA journal_mode = WAL")
            try db.execute(sql: """
                CREATE TABLE schema_version (
                    version INTEGER NOT NULL PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try db.execute(
                sql: "INSERT INTO schema_version (version) VALUES (?)",
                arguments: [schemaVersion]
            )
        }
        return url
    }

    func testOpensDatabaseSuccessfully() throws {
        let path = try makeTempDB()
        defer { try? FileManager.default.removeItem(at: path) }
        _ = try DatabaseManager(path: path.path)
    }

    func testReadsSchemaVersion() throws {
        let path = try makeTempDB(schemaVersion: 163)
        defer { try? FileManager.default.removeItem(at: path) }
        let manager = try DatabaseManager(path: path.path)
        XCTAssertEqual(try manager.currentSchemaVersion(), 163)
    }

    func testWALEnabled() throws {
        let path = try makeTempDB()
        defer { try? FileManager.default.removeItem(at: path) }
        let manager = try DatabaseManager(path: path.path)
        let mode: String? = try manager.reader.read { db in
            try String.fetchOne(db, sql: "PRAGMA journal_mode")
        }
        XCTAssertEqual(mode?.lowercased(), "wal")
    }

    func testMissingFileThrows() {
        XCTAssertThrowsError(
            try DatabaseManager(path: "/nonexistent/\(UUID().uuidString).sqlite")
        ) { error in
            guard case DatabaseManagerError.fileMissing = error else {
                XCTFail("Expected .fileMissing, got \(error)")
                return
            }
        }
    }

    func testCompatibilitySupportedVersion() throws {
        let path = try makeTempDB(schemaVersion: DatabaseManager.minimumSupportedSchemaVersion)
        defer { try? FileManager.default.removeItem(at: path) }
        let manager = try DatabaseManager(path: path.path)
        XCTAssertNoThrow(try manager.assertCompatibleSchema())
    }

    func testCompatibilityTooOld() throws {
        let path = try makeTempDB(schemaVersion: 100)
        defer { try? FileManager.default.removeItem(at: path) }
        let manager = try DatabaseManager(path: path.path)
        XCTAssertThrowsError(try manager.assertCompatibleSchema()) { error in
            guard case DatabaseManagerError.schemaTooOld = error else {
                XCTFail("Expected .schemaTooOld, got \(error)")
                return
            }
        }
    }

    func testCompatibilityTooNew() throws {
        let path = try makeTempDB(schemaVersion: 999)
        defer { try? FileManager.default.removeItem(at: path) }
        let manager = try DatabaseManager(path: path.path)
        XCTAssertThrowsError(try manager.assertCompatibleSchema()) { error in
            guard case DatabaseManagerError.schemaTooNew = error else {
                XCTFail("Expected .schemaTooNew, got \(error)")
                return
            }
        }
    }
}
