import MarkdownUI
import SwiftUI
import MyWikiCore

/// Renders document-level Markdown while preserving wiki-local link behavior and
/// Obsidian-style callouts.
struct MarkdownContentView: View {
    let text: String
    let onOpenWiki: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(Self.parseSegments(text).enumerated()), id: \.offset) { _, segment in
                switch segment {
                case .markdown(let content):
                    markdownView(for: content)
                case .callout(let kind, let title, let body):
                    calloutView(kind: kind, title: title, body: body)
                }
            }
        }
        .textSelection(.enabled)
        .environment(\.openURL, OpenURLAction { url in
            if let target = WikilinkParser.decodeLinkURL(url) {
                onOpenWiki(target)
                return .handled
            }
            return .systemAction
        })
    }

    @ViewBuilder
    private func markdownView(for content: String) -> some View {
        let normalized = Self.preprocessMarkdown(content)
        if !normalized.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            Markdown(normalized)
                .markdownTheme(.basic)
                .markdownTextStyle {
                    FontFamily(.system(activeFont.design))
                    ForegroundColor(EditorialPalette.textPrimary)
                    BackgroundColor(nil)
                    FontSize(14)
                }
                .markdownTextStyle(\.link) {
                    FontFamily(.system(activeFont.design))
                    ForegroundColor(EditorialPalette.accent)
                    BackgroundColor(nil)
                }
                .markdownTextStyle(\.code) {
                    FontFamilyVariant(.monospaced)
                    BackgroundColor(EditorialPalette.surface)
                    ForegroundColor(EditorialPalette.textPrimary)
                }
                .textSelection(.enabled)
        }
    }

    private func calloutView(kind: String, title: String, body: String) -> some View {
        let icon: String = switch kind.lowercased() {
        case "note": "info.circle"
        case "tip": "lightbulb"
        case "warning", "caution": "exclamationmark.triangle"
        case "important": "exclamationmark.circle"
        case "example": "list.bullet.rectangle"
        case "question", "faq": "questionmark.circle"
        default: "text.quote"
        }
        let accentColor: Color = switch kind.lowercased() {
        case "warning", "caution", "important": EditorialPalette.warning
        default: EditorialPalette.accent
        }

        return HStack(alignment: .top, spacing: 0) {
            RoundedRectangle(cornerRadius: 1.5)
                .fill(accentColor)
                .frame(width: 3)
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: icon)
                        .font(.system(size: 11))
                        .foregroundStyle(accentColor)
                    if !title.isEmpty {
                        Text(Self.renderInlineMarkdown(title))
                            .font(.system(size: 13, weight: .semibold, design: activeFont.design))
                            .foregroundStyle(accentColor)
                    } else {
                        Text(kind.capitalized)
                            .font(.system(size: 13, weight: .semibold, design: activeFont.design))
                            .foregroundStyle(accentColor)
                    }
                }
                markdownView(for: body)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .background(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(EditorialPalette.surface)
        )
    }

    enum Segment: Equatable {
        case markdown(String)
        case callout(kind: String, title: String, body: String)
    }

    static func parseSegments(_ text: String) -> [Segment] {
        let lines = text.components(separatedBy: "\n")
        var segments: [Segment] = []
        var markdownBuffer: [String] = []
        var index = 0

        func flushMarkdownBuffer() {
            let joined = markdownBuffer.joined(separator: "\n")
            if !joined.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                segments.append(.markdown(joined))
            }
            markdownBuffer = []
        }

        while index < lines.count {
            let trimmed = lines[index].trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("> [!") {
                flushMarkdownBuffer()

                var rawLines: [String] = []
                var quoteLines: [String] = []
                while index < lines.count {
                    let rawLine = lines[index]
                    let candidate = rawLine.trimmingCharacters(in: .whitespaces)
                    guard candidate.hasPrefix(">") else { break }
                    rawLines.append(rawLine)

                    var stripped = String(candidate.dropFirst())
                    if stripped.hasPrefix(" ") {
                        stripped.removeFirst()
                    }
                    quoteLines.append(stripped)
                    index += 1
                }

                if let callout = parseCallout(from: quoteLines) {
                    segments.append(callout)
                } else {
                    segments.append(.markdown(rawLines.joined(separator: "\n")))
                }
                continue
            }

            markdownBuffer.append(lines[index])
            index += 1
        }

        flushMarkdownBuffer()
        return segments
    }

    private static func parseCallout(from lines: [String]) -> Segment? {
        guard let first = lines.first else {
            return nil
        }
        let pattern = #"^\[!([^\]]+)\]([+-]?)\s*(.*)$"#
        let regex = try? NSRegularExpression(pattern: pattern)
        let fullRange = NSRange(first.startIndex..<first.endIndex, in: first)
        guard let match = regex?.firstMatch(in: first, range: fullRange),
              let kindRange = Range(match.range(at: 1), in: first),
              let titleRange = Range(match.range(at: 3), in: first) else {
            return nil
        }
        let kind = String(first[kindRange])
        let title = String(first[titleRange]).trimmingCharacters(in: .whitespaces)
        let body = lines.dropFirst().joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return .callout(kind: kind, title: title, body: body)
    }

    static func preprocessMarkdown(_ text: String) -> String {
        var converted = text

        while let open = converted.range(of: "[[") {
            guard let close = converted.range(of: "]]", range: open.upperBound..<converted.endIndex) else {
                break
            }

            let body = converted[open.upperBound..<close.lowerBound]
            let parts = body.split(separator: "|", maxSplits: 1, omittingEmptySubsequences: false)
            let target = parts.first.map(String.init)?.trimmingCharacters(in: .whitespaces) ?? ""
            let display = parts.count == 2
                ? String(parts[1]).trimmingCharacters(in: .whitespaces)
                : target

            if target.isEmpty {
                converted.replaceSubrange(open.lowerBound..<close.upperBound, with: String(body))
            } else if let url = WikilinkParser.linkURL(for: target) {
                converted.replaceSubrange(
                    open.lowerBound..<close.upperBound,
                    with: "[\(display)](\(url.absoluteString))"
                )
            } else {
                converted.replaceSubrange(open.lowerBound..<close.upperBound, with: display)
            }
        }

        return converted
    }

    static func renderInlineMarkdown(_ text: String) -> AttributedString {
        let converted = preprocessMarkdown(text)
        if let result = try? AttributedString(
            markdown: converted,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return result
        }
        return AttributedString(text)
    }
}
