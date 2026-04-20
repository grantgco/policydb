import XCTest
@testable import Coverage

final class UIDMinterTests: XCTestCase {

    private struct UIDShape: Decodable {
        let samples: [String]
        let length: Int
        let charset: String
    }

    private struct Fixture: Decodable {
        let generate_issue_uid_shape: UIDShape
    }

    func testPythonSamplesMatchShape() throws {
        let fixture: Fixture = try FixtureLoader.load()
        let shape = fixture.generate_issue_uid_shape
        XCTAssertEqual(shape.length, UIDMinter.length)
        XCTAssertEqual(Set(shape.charset), UIDMinter.charset)
        for sample in shape.samples {
            XCTAssertEqual(sample.count, UIDMinter.length,
                           "Python sample \(sample) wrong length")
            XCTAssertTrue(sample.allSatisfy { UIDMinter.charset.contains($0) },
                          "Python sample \(sample) has non-hex chars")
        }
    }

    func testSwiftOutputMatchesShape() {
        for _ in 0..<50 {
            let uid = UIDMinter.generateIssueUID()
            XCTAssertEqual(uid.count, UIDMinter.length)
            XCTAssertTrue(uid.allSatisfy { UIDMinter.charset.contains($0) },
                          "\(uid) has non-hex chars")
        }
    }

    func testSwiftOutputHasReasonableEntropy() {
        let batch = (0..<100).map { _ in UIDMinter.generateIssueUID() }
        // With 8 hex chars = 32 bits, collision odds at 100 samples are negligible
        XCTAssertEqual(Set(batch).count, batch.count, "Unexpected collision in 100-sample batch")
    }
}
