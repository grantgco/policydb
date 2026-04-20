import XCTest
@testable import Coverage

final class EmailCleanerTests: XCTestCase {

    private struct Case: Decodable {
        let input: String
        let output: String?
        let error: String?
    }

    private struct Fixture: Decodable {
        let clean_email: [Case]
    }

    func testParityWithPythonFixture() throws {
        let fixture: Fixture = try FixtureLoader.load()
        XCTAssertFalse(fixture.clean_email.isEmpty, "fixture empty")
        for c in fixture.clean_email {
            let swift = EmailCleaner.clean(c.input)
            let expected = c.output ?? ""
            XCTAssertEqual(
                swift, expected,
                "Mismatch for \(c.input.debugDescription): swift=\(swift.debugDescription) python=\(expected.debugDescription)"
            )
        }
    }
}
