import Foundation

/// Builds hierarchical email reference tags. Port of
/// `src/policydb/utils.py::build_ref_tag`.
///
/// Hierarchy: Client → Location → Policy → Activity/Correspondence/RFI/Issue.
/// Priority: rfi_uid > issue_uid > thread_id > activity_id.
///
/// Examples:
///   - `cn_number="123456789"`                                  → `CN123456789`
///   - `cn_number="CN123456789"`                                → `CN123456789`
///   - `cn_number="123", project_id=5`                          → `CN123-L5`
///   - `cn_number="123", project_id=5, policy_uid="POL-042"`    → `CN123-L5-POL042`
///   - `cn_number="123", rfi_uid="CN123-RFI01"`                 → `CN123-RFI01`
///   - `cn_number="", client_id=7`                              → `C7`
///
/// Parity enforced by `RefTagBuilderTests`.
enum RefTagBuilder {

    /// Matches `CN`/`cn` prefix at start of string.
    private static let cnPrefix = try! NSRegularExpression(pattern: "^[Cc][Nn]")

    /// Matches `RFI\d+` anywhere, case-insensitive.
    private static let rfiSuffix = try! NSRegularExpression(
        pattern: "(RFI\\d+)", options: [.caseInsensitive]
    )

    static func build(
        cnNumber: String? = nil,
        clientId: Int = 0,
        policyUID: String = "",
        projectId: Int = 0,
        activityId: Int = 0,
        threadId: Int = 0,
        rfiUID: String = "",
        issueUID: String = "",
        programUID: String = ""
    ) -> String {
        // Normalize cn_number — treat None/"None"/"none"/"" as empty
        let cnRaw: String
        switch cnNumber {
        case nil, "None", "none", "":
            cnRaw = ""
        case let s?:
            cnRaw = s
        }

        let cnClean: String
        if cnRaw.isEmpty {
            cnClean = ""
        } else {
            let range = NSRange(cnRaw.startIndex..., in: cnRaw)
            cnClean = cnPrefix.stringByReplacingMatches(
                in: cnRaw, range: range, withTemplate: ""
            )
        }

        var tag: String = cnClean.isEmpty ? "C\(clientId)" : "CN\(cnClean)"

        if projectId != 0 {
            tag += "-L\(projectId)"
        }
        if !programUID.isEmpty {
            tag += "-\(programUID.replacingOccurrences(of: "-", with: ""))"
        }
        if !policyUID.isEmpty {
            tag += "-\(policyUID.replacingOccurrences(of: "-", with: ""))"
        }

        // rfi_uid > issue_uid > thread_id > activity_id
        if !rfiUID.isEmpty {
            let range = NSRange(rfiUID.startIndex..., in: rfiUID)
            if let match = rfiSuffix.firstMatch(in: rfiUID, range: range),
               let r = Range(match.range(at: 1), in: rfiUID) {
                tag += "-\(String(rfiUID[r]).uppercased())"
            }
        } else if !issueUID.isEmpty {
            tag += "-\(issueUID)"
        } else if threadId != 0 {
            tag += "-COR\(threadId)"
        } else if activityId != 0 {
            tag += "-A\(activityId)"
        }

        return tag
    }
}
