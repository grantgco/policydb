import Foundation
import GRDB

/// Owns the PolicyDB SQLite connection. Runs as a coexisting reader/writer
/// alongside the Python webapp — Python is the schema owner, so this type
/// never runs migrations or CREATE TABLE.
///
/// Use `reader` for SELECTs and `writer` for INSERT/UPDATE/DELETE. Each write
/// should run as a short transaction — do not hold writes open across UI
/// interactions (see spec §5.5 Concurrent Writer Handling).
struct DatabaseManager {

    /// Minimum schema version this Swift app understands. Bump when we start
    /// depending on columns added by a specific migration.
    static let minimumSupportedSchemaVersion = 163

    /// Maximum schema version this Swift app has been tested against. Bump
    /// after manual verification that newer Python schemas don't break us.
    static let maximumSupportedSchemaVersion = 200

    let dbPool: DatabasePool

    var reader: any DatabaseReader { dbPool }
    var writer: any DatabaseWriter { dbPool }

    init(path: String) throws {
        guard FileManager.default.fileExists(atPath: path) else {
            throw DatabaseManagerError.fileMissing(path)
        }
        var config = Configuration()
        config.busyMode = .timeout(5)
        self.dbPool = try DatabasePool(path: path, configuration: config)

        // Python webapp sets WAL on startup; enforce here so tests and
        // ad-hoc invocations have the same journal mode.
        try dbPool.write { db in
            try db.execute(sql: "PRAGMA journal_mode = WAL")
        }
    }

    /// Opens the user's real PolicyDB at `~/.policydb/policydb.sqlite`.
    static func `default`() throws -> DatabaseManager {
        let path = FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent(".policydb/policydb.sqlite")
            .path
        return try DatabaseManager(path: path)
    }

    func currentSchemaVersion() throws -> Int {
        try dbPool.read { db in
            try Int.fetchOne(db, sql: "SELECT MAX(version) FROM schema_version") ?? 0
        }
    }

    /// Throws if the DB's schema version is outside the supported range.
    func assertCompatibleSchema() throws {
        let current = try currentSchemaVersion()
        if current < Self.minimumSupportedSchemaVersion {
            throw DatabaseManagerError.schemaTooOld(
                current: current,
                required: Self.minimumSupportedSchemaVersion
            )
        }
        if current > Self.maximumSupportedSchemaVersion {
            throw DatabaseManagerError.schemaTooNew(
                current: current,
                supported: Self.maximumSupportedSchemaVersion
            )
        }
    }
}

enum DatabaseManagerError: Error, CustomStringConvertible {
    case fileMissing(String)
    case schemaTooOld(current: Int, required: Int)
    case schemaTooNew(current: Int, supported: Int)

    var description: String {
        switch self {
        case let .fileMissing(path):
            return "SQLite database not found at \(path). Is the Python webapp set up?"
        case let .schemaTooOld(current, required):
            return "Database schema \(current) is older than required (\(required)). " +
                   "Run the Python webapp once to apply migrations."
        case let .schemaTooNew(current, supported):
            return "Database schema \(current) is newer than this app supports " +
                   "(\(supported)). Update the Swift app."
        }
    }
}
