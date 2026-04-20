import Foundation

/// Parses currency strings. Byte-for-byte port of
/// `src/policydb/utils.py::parse_currency_with_magnitude`.
///
/// Accepts shorthand magnitude suffixes (`K`/`M`/`B`, case-insensitive),
/// dollar signs, commas, surrounding whitespace. Returns `0.0` for empty,
/// `nil`-adjacent, or unparseable input — never throws. Parity is enforced
/// by `CurrencyParserTests` replaying `python-rule-outputs.json`.
enum CurrencyParser {

    static func parse(_ raw: String?) -> Double {
        guard let raw else { return 0.0 }
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "$", with: "")
            .replacingOccurrences(of: ",", with: "")
        if s.isEmpty { return 0.0 }

        var multiplier: Double = 1
        if let last = s.last {
            switch String(last).uppercased() {
            case "K":
                multiplier = 1_000
                s.removeLast()
            case "M":
                multiplier = 1_000_000
                s.removeLast()
            case "B":
                multiplier = 1_000_000_000
                s.removeLast()
            default:
                break
            }
        }

        return (Double(s) ?? 0.0) * multiplier
    }
}
