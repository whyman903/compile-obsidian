import Foundation
#if canImport(SwiftUI)
import SwiftUI
#endif

public enum WikilinkRun: Equatable, Sendable {
    case text(String)
    case link(target: String, display: String)
}

public enum WikilinkParser {
    /// Split a string into plain text runs and `[[wikilink]]` references. Supports
    /// `[[Target]]` and `[[Target|Alias]]` forms. Adjacent text runs are merged.
    public static func parse(_ text: String) -> [WikilinkRun] {
        guard !text.isEmpty else { return [] }
        var runs: [WikilinkRun] = []
        var pendingText = ""
        var cursor = text.startIndex

        func flushText() {
            if !pendingText.isEmpty {
                runs.append(.text(pendingText))
                pendingText = ""
            }
        }

        while let openRange = text.range(of: "[[", range: cursor..<text.endIndex) {
            let afterOpen = openRange.upperBound
            guard let closeRange = text.range(of: "]]", range: afterOpen..<text.endIndex) else {
                break
            }
            if openRange.lowerBound > cursor {
                pendingText += text[cursor..<openRange.lowerBound]
            }
            let body = text[afterOpen..<closeRange.lowerBound]
            let split = body.split(separator: "|", maxSplits: 1, omittingEmptySubsequences: false)
            let target = split.first.map(String.init)?.trimmingCharacters(in: .whitespaces) ?? ""
            let display: String
            if split.count == 2 {
                display = String(split[1]).trimmingCharacters(in: .whitespaces)
            } else {
                display = target
            }
            if target.isEmpty {
                pendingText += text[openRange.lowerBound..<closeRange.upperBound]
            } else {
                flushText()
                runs.append(.link(target: target, display: display.isEmpty ? target : display))
            }
            cursor = closeRange.upperBound
        }

        if cursor < text.endIndex {
            pendingText += text[cursor..<text.endIndex]
        }
        flushText()
        return runs
    }

    /// Encode a wikilink target into a custom URL the launcher view can intercept.
    public static func linkURL(for target: String) -> URL? {
        var components = URLComponents()
        components.scheme = "mywiki"
        components.host = "page"
        components.queryItems = [URLQueryItem(name: "target", value: target)]
        return components.url
    }

    public static func decodeLinkURL(_ url: URL) -> String? {
        guard url.scheme == "mywiki", url.host == "page" else { return nil }
        return URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first(where: { $0.name == "target" })?
            .value
    }

    #if canImport(SwiftUI)
    /// Produce an AttributedString where wikilinks carry a `mywiki://page?target=...`
    /// URL. Renderers can intercept via `OpenURLAction` to route clicks to Obsidian.
    public static func attributedString(_ text: String) -> AttributedString {
        var result = AttributedString()
        for run in parse(text) {
            switch run {
            case .text(let str):
                result += AttributedString(str)
            case .link(let target, let display):
                var part = AttributedString(display)
                part.foregroundColor = Color(red: 0.4, green: 0.9, blue: 1.0)
                part.underlineStyle = .single
                if let url = linkURL(for: target) {
                    part.link = url
                }
                result += part
            }
        }
        return result
    }
    #endif
}
