import Foundation
import XCTest

/// Loads `python-rule-outputs.json` from the test bundle and decodes it
/// into whatever slice a given test cares about.
enum FixtureLoader {

    static func load<T: Decodable>(
        resource: String = "python-rule-outputs",
        as: T.Type = T.self
    ) throws -> T {
        let bundle = Bundle(for: BundleFinder.self)
        guard let url = bundle.url(forResource: resource, withExtension: "json") else {
            throw FixtureError.missing(resource: resource, bundlePath: bundle.bundlePath)
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(T.self, from: data)
    }

    enum FixtureError: Error, CustomStringConvertible {
        case missing(resource: String, bundlePath: String)
        var description: String {
            switch self {
            case let .missing(r, p):
                return "Fixture '\(r).json' not found in test bundle at \(p). " +
                       "Confirm it's under CoverageTests/Fixtures/ and the test target includes it."
            }
        }
    }

    private final class BundleFinder {}
}
