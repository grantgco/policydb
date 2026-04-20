import XCTest
@testable import Coverage

final class PhoneFormatterTests: XCTestCase {

    private struct Case: Decodable {
        let input: String
        let output: String?
        let error: String?
    }

    private struct Fixture: Decodable {
        let format_phone: [Case]
    }

    func testParityWithPythonFixture() throws {
        let fixture: Fixture = try FixtureLoader.load()
        XCTAssertFalse(fixture.format_phone.isEmpty, "fixture empty")
        for c in fixture.format_phone {
            let swift = PhoneFormatter.format(c.input)
            let expected = c.output ?? ""
            XCTAssertEqual(
                swift, expected,
                "Mismatch for \(c.input.debugDescription): swift=\(swift.debugDescription) python=\(expected.debugDescription)"
            )
        }
    }
}
