import XCTest
@testable import Coverage

final class CurrencyParserTests: XCTestCase {

    private struct Case: Decodable {
        let input: String
        let output: Double?
        let error: String?
    }

    private struct Fixture: Decodable {
        let parse_currency_with_magnitude: [Case]
    }

    func testParityWithPythonFixture() throws {
        let fixture: Fixture = try FixtureLoader.load()
        XCTAssertFalse(fixture.parse_currency_with_magnitude.isEmpty, "fixture empty")
        for c in fixture.parse_currency_with_magnitude {
            let swift = CurrencyParser.parse(c.input)
            let expected = c.output ?? 0.0
            XCTAssertEqual(
                swift, expected, accuracy: 0.001,
                "Mismatch for \(c.input.debugDescription): swift=\(swift) python=\(expected)"
            )
        }
    }
}
