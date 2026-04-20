import XCTest
@testable import Coverage

final class RefTagBuilderTests: XCTestCase {

    /// Python kwargs mirror — optional JSON fields map to Swift defaults.
    private struct Input: Decodable {
        let cn_number: String?
        let client_id: Int?
        let policy_uid: String?
        let project_id: Int?
        let activity_id: Int?
        let thread_id: Int?
        let rfi_uid: String?
        let issue_uid: String?
        let program_uid: String?
    }

    private struct Case: Decodable {
        let input: Input
        let output: String?
        let error: String?
    }

    private struct Fixture: Decodable {
        let build_ref_tag: [Case]
    }

    func testParityWithPythonFixture() throws {
        let fixture: Fixture = try FixtureLoader.load()
        XCTAssertFalse(fixture.build_ref_tag.isEmpty, "fixture empty")
        for c in fixture.build_ref_tag {
            let swift = RefTagBuilder.build(
                cnNumber: c.input.cn_number,
                clientId: c.input.client_id ?? 0,
                policyUID: c.input.policy_uid ?? "",
                projectId: c.input.project_id ?? 0,
                activityId: c.input.activity_id ?? 0,
                threadId: c.input.thread_id ?? 0,
                rfiUID: c.input.rfi_uid ?? "",
                issueUID: c.input.issue_uid ?? "",
                programUID: c.input.program_uid ?? ""
            )
            let expected = c.output ?? ""
            XCTAssertEqual(
                swift, expected,
                "Mismatch for \(c.input): swift=\(swift) python=\(expected)"
            )
        }
    }
}
