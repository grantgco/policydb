import Foundation

/// Extracts a clean email address from pasted/messy input. Port of
/// `src/policydb/utils.py::clean_email`.
///
/// Handles `"Jane Doe <jane@example.com>"`, `mailto:` prefixes, trailing
/// punctuation, quoting. Returns the normalized lowercase email, or an
/// empty string for unrecognizable input. Never throws.
///
/// Parity enforced by `EmailCleanerTests`.
enum EmailCleaner {

    private static let maxInputLength = 512

    private static let localChars: Set<Character> = Set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-"
    )

    private static let hostChars: Set<Character> = Set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
    )

    /// Mirrors Python's default `s.strip()` (no args) —
    /// trims unicode whitespace per `str.isspace()`.
    private static let defaultTrim = CharacterSet.whitespacesAndNewlines

    /// Outer strip plus the extra chars Python's
    /// `s.strip(' \t\n\r"\';,<>()')` removes.
    private static let bracketTrim = CharacterSet(charactersIn: " \t\n\r\"';,<>()")

    static func clean(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: defaultTrim)
        guard !trimmed.isEmpty else { return "" }

        var s = trimmed

        // Cap pathological input while keeping the region around the last '@'.
        if s.count > maxInputLength {
            if let atIdx = s.lastIndex(of: "@") {
                let half = 256
                let startOffset = max(0, s.distance(from: s.startIndex, to: atIdx) - half)
                let endOffset = min(s.count, s.distance(from: s.startIndex, to: atIdx) + half)
                let start = s.index(s.startIndex, offsetBy: startOffset)
                let end = s.index(s.startIndex, offsetBy: endOffset)
                s = String(s[start..<end])
            } else {
                let start = s.index(s.endIndex, offsetBy: -maxInputLength)
                s = String(s[start...])
            }
        }

        // Strip `mailto:` prefix
        if s.lowercased().hasPrefix("mailto:") {
            s = String(s.dropFirst("mailto:".count))
        }

        // Extract email from angle brackets: "Name <email>"
        if let bracketed = firstBracketedEmail(in: s) {
            s = bracketed
        }

        // Scan for a bare email
        if let bare = scanBareEmail(s) {
            return bare.lowercased()
        }

        // Strip outer quotes/punctuation
        s = s.trimmingCharacters(in: bracketTrim)

        // Final sanity: has @ and a dot in the host
        if let atIdx = s.lastIndex(of: "@") {
            let host = s[s.index(after: atIdx)...]
            if host.contains(".") {
                return s.lowercased()
            }
        }

        // Fallback — normalize
        return s.trimmingCharacters(in: defaultTrim).lowercased()
    }

    /// Returns the content inside the first `<...>` pair that looks like an email.
    private static func firstBracketedEmail(in s: String) -> String? {
        guard let open = s.firstIndex(of: "<") else { return nil }
        let afterOpen = s.index(after: open)
        guard let close = s[afterOpen...].firstIndex(of: ">") else { return nil }
        let inner = s[afterOpen..<close]
        // Must contain a single @ and no nested angle brackets
        guard inner.contains("@"),
              !inner.contains("<"),
              !inner.contains(">") else { return nil }
        let atCount = inner.filter { $0 == "@" }.count
        guard atCount == 1 else { return nil }
        return String(inner)
    }

    /// Linear scan — mirrors Python `_scan_bare_email`.
    private static func scanBareEmail(_ s: String) -> String? {
        let chars = Array(s)
        var idx = 0
        while idx < chars.count {
            // Find next '@'
            guard let atPos = (idx..<chars.count).first(where: { chars[$0] == "@" }) else {
                return nil
            }
            if atPos <= 0 || atPos >= chars.count - 1 {
                return nil
            }
            // Walk back for local
            var start = atPos
            while start > 0, localChars.contains(chars[start - 1]) {
                start -= 1
            }
            // Walk forward for host
            var end = atPos + 1
            while end < chars.count, hostChars.contains(chars[end]) {
                end += 1
            }
            var local = String(chars[start..<atPos])
            var host = String(chars[(atPos + 1)..<end])
            local = local.trimmingCharacters(in: CharacterSet(charactersIn: "."))
            host = host.trimmingCharacters(in: CharacterSet(charactersIn: "."))
            if !local.isEmpty, host.contains("."), !host.contains("..") {
                return "\(local)@\(host)"
            }
            idx = atPos + 1
        }
        return nil
    }
}
