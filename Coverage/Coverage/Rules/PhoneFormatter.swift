import Foundation
import PhoneNumberKit

/// Formats phone numbers. Port of `src/policydb/utils.py::format_phone`.
///
/// Behavior mirrors Python's `phonenumbers`-backed function:
///   - empty / whitespace-only input → `""`
///   - valid NANP (country code 1) → NATIONAL format, e.g. `(650) 253-0000`
///   - valid non-NANP → INTERNATIONAL format, e.g. `+44 20 7946 0958`
///   - parseable but invalid (e.g. 555-exchange) → stripped raw input
///   - unparseable → stripped raw input
///
/// Parity enforced by `PhoneFormatterTests` against `python-rule-outputs.json`.
enum PhoneFormatter {

    private static let utility = PhoneNumberUtility()

    static func format(_ raw: String, defaultRegion: String = "US") -> String {
        let stripped = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !stripped.isEmpty else { return "" }

        // `ignoreType: false` makes parse reject numbers whose NPA-NXX resolves
        // to type `.unknown` (e.g. 555-exchange test numbers), matching Python's
        // `phonenumbers.is_valid_number == False → return raw.strip()`.
        guard utility.isValidPhoneNumber(stripped, withRegion: defaultRegion, ignoreType: false) else {
            return stripped
        }

        do {
            let parsed = try utility.parse(stripped, withRegion: defaultRegion, ignoreType: false)
            if parsed.countryCode == 1 {
                return utility.format(parsed, toType: .national)
            } else {
                return utility.format(parsed, toType: .international)
            }
        } catch {
            return stripped
        }
    }
}
