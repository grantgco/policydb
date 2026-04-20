import Foundation

/// Mints issue UIDs. Port of `src/policydb/db.py::generate_issue_uid` —
/// first 8 chars of a UUID4 hex, uppercased.
///
/// Randomness can't be fixture-tested; `UIDMinterTests` validates the
/// shape (length, charset, uniqueness across a sample batch) and
/// confirms Python's stored samples fit the same shape.
enum UIDMinter {

    static let length = 8
    static let charset: Set<Character> = Set("0123456789ABCDEF")

    static func generateIssueUID() -> String {
        let hex = UUID().uuidString
            .replacingOccurrences(of: "-", with: "")
        return String(hex.prefix(length)).uppercased()
    }
}
