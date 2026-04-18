import AppKit
import Darwin
import SwiftUI
import WebKit
import cmark_gfm
import cmark_gfm_extensions
import MyWikiCore

struct MarkdownContentView: View {
    let text: String
    let onOpenWiki: (String) -> Void
    @State private var contentHeight: CGFloat = 1

    var body: some View {
        MarkdownWebView(
            text: text,
            contentHeight: $contentHeight,
            onOpenWiki: onOpenWiki
        )
        .frame(maxWidth: .infinity, minHeight: 1, idealHeight: contentHeight, maxHeight: contentHeight)
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

    static func renderHTMLBody(_ text: String) -> String {
        let markdown = preprocessMarkdown(text)
        cmark_gfm_core_extensions_ensure_registered()

        guard let parser = cmark_parser_new(CMARK_OPT_DEFAULT) else {
            return "<pre>\(escapedHTML(markdown))</pre>"
        }
        defer { cmark_parser_free(parser) }

        for extensionName in ["autolink", "strikethrough", "tagfilter", "tasklist", "table"] {
            if let syntaxExtension = cmark_find_syntax_extension(extensionName) {
                cmark_parser_attach_syntax_extension(parser, syntaxExtension)
            }
        }

        markdown.withCString { buffer in
            cmark_parser_feed(parser, buffer, markdown.utf8.count)
        }

        guard let document = cmark_parser_finish(parser) else {
            return "<pre>\(escapedHTML(markdown))</pre>"
        }
        defer { cmark_node_free(document) }

        guard let html = cmark_render_html(document, CMARK_OPT_DEFAULT, nil) else {
            return "<pre>\(escapedHTML(markdown))</pre>"
        }
        defer { free(html) }

        return String(cString: html)
    }

    private static func escapedHTML(_ text: String) -> String {
        text
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\"", with: "&quot;")
    }
}

private final class ScrollForwardingWebView: WKWebView {
    override func scrollWheel(with event: NSEvent) {
        nextResponder?.scrollWheel(with: event)
    }
}

private struct MarkdownWebView: NSViewRepresentable {
    let text: String
    @Binding var contentHeight: CGFloat
    let onOpenWiki: (String) -> Void

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(context.coordinator, name: "contentHeight")

        let webView = ScrollForwardingWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.setValue(false, forKey: "drawsBackground")
        webView.setContentHuggingPriority(.defaultLow, for: .horizontal)
        webView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        updateWebView(webView, context: context)
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.onOpenWiki = onOpenWiki
        context.coordinator.contentHeight = $contentHeight
        updateWebView(webView, context: context)
    }

    static func dismantleNSView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.navigationDelegate = nil
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "contentHeight")
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(contentHeight: $contentHeight, onOpenWiki: onOpenWiki)
    }

    private func updateWebView(_ webView: WKWebView, context: Context) {
        let key = RenderKey(text: text, font: activeFont, theme: activeTheme)
        guard context.coordinator.renderKey != key else {
            return
        }

        context.coordinator.renderKey = key
        webView.loadHTMLString(Self.documentHTML(for: text), baseURL: nil)
    }

    private static func documentHTML(for text: String) -> String {
        """
        <!doctype html>
        <html>
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
        \(stylesheet)
        </style>
        <script>
        function postHeight() {
            const body = document.body;
            const html = document.documentElement;
            const height = Math.max(
                body.scrollHeight,
                body.offsetHeight,
                html.clientHeight,
                html.scrollHeight,
                html.offsetHeight
            );
            window.webkit.messageHandlers.contentHeight.postMessage(height);
        }
        window.addEventListener('load', postHeight);
        window.addEventListener('resize', postHeight);
        if (window.ResizeObserver) {
            new ResizeObserver(postHeight).observe(document.body);
        }
        setTimeout(postHeight, 0);
        setTimeout(postHeight, 100);
        </script>
        </head>
        <body>
        \(MarkdownContentView.renderHTMLBody(text))
        </body>
        </html>
        """
    }

    private static var stylesheet: String {
        let text = cssColor(NSColor(EditorialPalette.textPrimary))
        let secondary = cssColor(NSColor(EditorialPalette.textSecondary))
        let surface = cssColor(NSColor(EditorialPalette.surface))
        let border = cssColor(NSColor(EditorialPalette.border))
        let link = cssColor(NSColor(EditorialPalette.link))
        let accent = cssColor(NSColor(EditorialPalette.accent))

        return """
        :root { color-scheme: light dark; }
        html, body {
            background: transparent;
            color: \(text);
            font-family: \(fontFamily);
            font-size: 14px;
            line-height: 1.45;
            margin: 0;
            overflow: hidden;
            padding: 0;
            user-select: text;
            -webkit-user-select: text;
        }
        * { box-sizing: border-box; }
        body > *:first-child { margin-top: 0; }
        body > *:last-child { margin-bottom: 0; }
        p { margin: 0 0 0.7em; }
        h1, h2, h3, h4, h5, h6 {
            color: \(text);
            font-weight: 650;
            line-height: 1.2;
            margin: 1em 0 0.45em;
        }
        h1 { font-size: 1.45em; }
        h2 { font-size: 1.25em; }
        h3 { font-size: 1.1em; }
        a { color: \(link); text-decoration: underline; }
        code {
            background: \(surface);
            border-radius: 4px;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.92em;
            padding: 0.08em 0.28em;
        }
        pre {
            background: \(surface);
            border: 1px solid \(border);
            border-radius: 6px;
            margin: 0.8em 0;
            overflow-x: auto;
            padding: 10px 12px;
        }
        pre code { background: transparent; padding: 0; }
        blockquote {
            border-left: 3px solid \(accent);
            color: \(secondary);
            margin: 0.8em 0;
            padding: 0.1em 0 0.1em 0.9em;
        }
        ul, ol { margin: 0.4em 0 0.8em 1.4em; padding: 0; }
        li { margin: 0.2em 0; }
        table {
            border-collapse: collapse;
            display: block;
            margin: 0.9em 0;
            max-width: 100%;
            overflow-x: auto;
            width: max-content;
        }
        th, td {
            border: 1px solid \(border);
            padding: 6px 8px;
            text-align: left;
            vertical-align: top;
        }
        th { background: \(surface); color: \(text); font-weight: 650; }
        td { color: \(text); }
        hr {
            border: 0;
            border-top: 1px solid \(border);
            margin: 1em 0;
        }
        """
    }

    private static var fontFamily: String {
        switch activeFont {
        case .mono:
            return "ui-monospace, SFMono-Regular, Menlo, monospace"
        case .serif:
            return "\"New York\", ui-serif, Georgia, serif"
        case .sans:
            return "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", sans-serif"
        }
    }

    private static func cssColor(_ color: NSColor) -> String {
        guard let converted = color.usingColorSpace(.sRGB) else {
            return "rgba(0, 0, 0, 1)"
        }
        let red = Int(round(converted.redComponent * 255))
        let green = Int(round(converted.greenComponent * 255))
        let blue = Int(round(converted.blueComponent * 255))
        return "rgba(\(red), \(green), \(blue), \(converted.alphaComponent))"
    }

    struct RenderKey: Equatable {
        let text: String
        let font: AppFont
        let theme: AppTheme
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        var contentHeight: Binding<CGFloat>
        var onOpenWiki: (String) -> Void
        var renderKey: RenderKey?

        init(contentHeight: Binding<CGFloat>, onOpenWiki: @escaping (String) -> Void) {
            self.contentHeight = contentHeight
            self.onOpenWiki = onOpenWiki
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "contentHeight" else {
                return
            }

            let rawHeight: Double?
            if let number = message.body as? NSNumber {
                rawHeight = number.doubleValue
            } else {
                rawHeight = message.body as? Double
            }

            guard let rawHeight else {
                return
            }

            contentHeight.wrappedValue = max(1, ceil(rawHeight))
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void
        ) {
            guard navigationAction.navigationType == .linkActivated,
                  let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            if let target = WikilinkParser.decodeLinkURL(url) {
                onOpenWiki(target)
                decisionHandler(.cancel)
                return
            }

            if ["http", "https", "mailto"].contains(url.scheme?.lowercased() ?? "") {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }

            decisionHandler(.allow)
        }
    }
}
