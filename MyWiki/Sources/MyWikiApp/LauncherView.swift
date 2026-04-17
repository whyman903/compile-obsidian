import AppKit
import SwiftUI
import UniformTypeIdentifiers
import MyWikiCore

nonisolated(unsafe) var activeTheme: AppTheme = {
    AppTheme(rawValue: UserDefaults.standard.string(forKey: "appTheme") ?? "") ?? .umber
}()

nonisolated(unsafe) var activeFont: AppFont = {
    AppFont(rawValue: UserDefaults.standard.string(forKey: "appFont") ?? "") ?? .serif
}()

extension AppFont {
    var design: Font.Design {
        switch self {
        case .serif: return .serif
        case .sans: return .default
        case .mono: return .monospaced
        }
    }
}

struct ThemeColorSet {
    let background: Color
    let backgroundTop: Color
    let surface: Color
    let surfaceHover: Color
    let border: Color
    let borderHover: Color
    let textPrimary: Color
    let textSecondary: Color
    let textTertiary: Color
    let accent: Color
    let accentHover: Color
    let warning: Color

    static let ivory = ThemeColorSet(
        background:    Color(red: 0.980, green: 0.978, blue: 0.968),
        backgroundTop: Color(red: 0.950, green: 0.942, blue: 0.922),
        surface:       Color(red: 0.930, green: 0.918, blue: 0.894),
        surfaceHover:  Color(red: 0.900, green: 0.886, blue: 0.858),
        border:        Color(red: 0.840, green: 0.824, blue: 0.792),
        borderHover:   Color(red: 0.770, green: 0.750, blue: 0.714),
        textPrimary:   Color(red: 0.100, green: 0.094, blue: 0.082),
        textSecondary: Color(red: 0.360, green: 0.337, blue: 0.314),
        textTertiary:  Color(red: 0.608, green: 0.580, blue: 0.564),
        accent:        Color(red: 0.720, green: 0.475, blue: 0.180),
        accentHover:   Color(red: 0.830, green: 0.537, blue: 0.227),
        warning:       Color(red: 0.770, green: 0.302, blue: 0.227)
    )

    static let obsidian = ThemeColorSet(
        background:    Color(red: 0.040, green: 0.040, blue: 0.040),
        backgroundTop: Color(red: 0.070, green: 0.070, blue: 0.070),
        surface:       Color(red: 0.100, green: 0.100, blue: 0.100),
        surfaceHover:  Color(red: 0.140, green: 0.140, blue: 0.140),
        border:        Color(red: 0.180, green: 0.180, blue: 0.180),
        borderHover:   Color(red: 0.230, green: 0.230, blue: 0.230),
        textPrimary:   Color(red: 0.910, green: 0.910, blue: 0.910),
        textSecondary: Color(red: 0.540, green: 0.540, blue: 0.540),
        textTertiary:  Color(red: 0.350, green: 0.350, blue: 0.350),
        accent:        Color(red: 0.910, green: 0.910, blue: 0.910),
        accentHover:   Color(red: 1.000, green: 1.000, blue: 1.000),
        warning:       Color(red: 0.880, green: 0.314, blue: 0.251)
    )

    static let umber = ThemeColorSet(
        background:    Color(red: 0.0902, green: 0.0706, blue: 0.0549),
        backgroundTop: Color(red: 0.1098, green: 0.0902, blue: 0.0667),
        surface:       Color(red: 0.1216, green: 0.1020, blue: 0.0784),
        surfaceHover:  Color(red: 0.1490, green: 0.1255, blue: 0.0980),
        border:        Color(red: 0.1765, green: 0.1490, blue: 0.1255),
        borderHover:   Color(red: 0.2275, green: 0.1922, blue: 0.1608),
        textPrimary:   Color(red: 0.9608, green: 0.9333, blue: 0.8745),
        textSecondary: Color(red: 0.7647, green: 0.7216, blue: 0.6275),
        textTertiary:  Color(red: 0.4980, green: 0.4471, blue: 0.3765),
        accent:        Color(red: 0.8314, green: 0.6588, blue: 0.3529),
        accentHover:   Color(red: 0.8941, green: 0.7255, blue: 0.4078),
        warning:       Color(red: 0.8500, green: 0.5200, blue: 0.3200)
    )

    static func forTheme(_ theme: AppTheme) -> ThemeColorSet {
        switch theme {
        case .ivory: return .ivory
        case .obsidian: return .obsidian
        case .umber: return .umber
        }
    }
}

enum EditorialPalette {
    private static var colors: ThemeColorSet { .forTheme(activeTheme) }
    static var background: Color    { colors.background }
    static var backgroundTop: Color { colors.backgroundTop }
    static var surface: Color       { colors.surface }
    static var surfaceHover: Color  { colors.surfaceHover }
    static var border: Color        { colors.border }
    static var borderHover: Color   { colors.borderHover }
    static var textPrimary: Color   { colors.textPrimary }
    static var textSecondary: Color { colors.textSecondary }
    static var textTertiary: Color  { colors.textTertiary }
    static var accent: Color        { colors.accent }
    static var accentHover: Color   { colors.accentHover }
    static var link: Color {
        switch activeTheme {
        case .obsidian:
            return Color(red: 0.520, green: 0.740, blue: 1.000)
        case .ivory, .umber:
            return colors.accent
        }
    }
    static var warning: Color       { colors.warning }
}

struct LauncherView: View {
    @Bindable var model: AppModel
    @Environment(\.openWindow) private var openWindow
    @State private var draftText: String = ""
    @State private var stagedFiles: [URL] = []
    @State private var isDropTargeted = false

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(.horizontal, 24)
                .padding(.top, 20)
                .padding(.bottom, 14)

            statusStrip
                .padding(.horizontal, 24)

            Divider().overlay(EditorialPalette.border)

            composer
                .padding(.horizontal, 24)
                .padding(.top, 16)
                .padding(.bottom, 14)

            queryResponseSection

            actionRow
                .padding(.horizontal, 24)
                .padding(.bottom, 20)
        }
        .frame(width: 460)
        .background(backgroundLayer)
        .id("\(model.theme.rawValue).\(model.font.rawValue)")
        .preferredColorScheme(model.theme.prefersDarkMode ? .dark : .light)
        .alert("Install Advanced URI?", isPresented: $model.showGraphPluginInstallPrompt) {
            Button("Install") {
                Task {
                    await model.installGraphPluginForCurrentWorkspace()
                }
            }
            Button("Not Now", role: .cancel) {
                model.dismissGraphPluginInstallPrompt()
            }
        } message: {
            Text("Graph view now uses the Advanced URI plugin for this vault. MyWiki will add the plugin files to .obsidian/plugins and enable them without requesting Accessibility access.")
        }
    }

    // MARK: - Background

    private var backgroundLayer: some View {
        LinearGradient(
            colors: [EditorialPalette.backgroundTop, EditorialPalette.background],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(model.workspace?.topic ?? "MyWiki")
                    .font(.system(size: 22, weight: .medium, design: activeFont.design))
                    .foregroundStyle(EditorialPalette.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Text(subtitleText)
                    .font(.system(size: 11))
                    .foregroundStyle(EditorialPalette.textTertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            Spacer(minLength: 0)

            Menu {
                Button("Reveal in Finder") { model.revealWorkspaceInFinder() }
                Divider()
                if !model.recentWorkspacePaths.isEmpty {
                    Section("Recent Workspaces") {
                        ForEach(model.recentWorkspacePaths.prefix(5), id: \.self) { path in
                            Button(path) { model.selectRecentWorkspace(path) }
                        }
                    }
                    Divider()
                }
                Button("Open Other Workspace…") { model.chooseOtherWorkspace() }
                Divider()
                Button("Quit MyWiki") { NSApplication.shared.terminate(nil) }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(EditorialPalette.textTertiary)
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
    }

    private var subtitleText: String {
        if let workspace = model.workspace {
            return workspace.path.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        }
        return model.statusMessage
    }

    // MARK: - Status strip

    @ViewBuilder
    private var statusStrip: some View {
        if let error = model.lastError {
            statusPill(text: error, tone: .error, onDismiss: { model.lastError = nil })
                .padding(.bottom, 10)
        } else if let toast = model.launcherToast {
            statusPill(text: toast, tone: .success, onDismiss: nil)
                .padding(.bottom, 10)
        }
    }

    private enum Tone { case success, error }

    private func statusPill(text: String, tone: Tone, onDismiss: (() -> Void)?) -> some View {
        HStack(spacing: 10) {
            Circle()
                .fill(tone == .success ? EditorialPalette.accent : EditorialPalette.warning)
                .frame(width: 6, height: 6)
            Text(text)
                .font(.system(size: 12))
                .foregroundStyle(EditorialPalette.textSecondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if let onDismiss {
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(EditorialPalette.textTertiary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(EditorialPalette.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(EditorialPalette.border, lineWidth: 1)
        )
    }

    // MARK: - Composer

    private var composer: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !stagedFiles.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(stagedFiles, id: \.self) { url in
                            FileChip(url: url) {
                                stagedFiles.removeAll { $0 == url }
                            }
                        }
                    }
                    .padding(.bottom, 2)
                }
            }

            ZStack(alignment: .topLeading) {
                if draftText.isEmpty {
                    Text(placeholderText)
                        .font(.system(size: 13).italic())
                        .foregroundStyle(EditorialPalette.textTertiary)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .allowsHitTesting(false)
                }
                InsetlessTextEditor(
                    text: $draftText,
                    font: .systemFont(ofSize: 13),
                    textColor: NSColor(EditorialPalette.textPrimary),
                    autoFocus: true
                )
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .frame(minHeight: 104, maxHeight: 160)
            }
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(EditorialPalette.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(
                        isDropTargeted ? EditorialPalette.accent : EditorialPalette.border,
                        lineWidth: isDropTargeted ? 1.5 : 1.0
                    )
                    .animation(.easeOut(duration: 0.15), value: isDropTargeted)
            )
            .dropDestination(for: URL.self) { items, _ in
                let fresh = items.filter { !stagedFiles.contains($0) }
                guard !fresh.isEmpty else { return false }
                stagedFiles.append(contentsOf: fresh)
                return true
            } isTargeted: { isDropTargeted = $0 }

            HStack(spacing: 10) {
                Button {
                    model.chooseFilesForIngest()
                } label: {
                    Label("Attach", systemImage: "paperclip")
                        .font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(GhostButtonStyle())

                Spacer()

                Button(action: showQueryWindow) {
                    Image(systemName: "arrow.up.forward.app")
                        .font(.system(size: 13, weight: .medium))
                }
                .buttonStyle(GhostButtonStyle())
                .help("Open MyWiki window")

                Button(action: launch) {
                    HStack(spacing: 10) {
                        Text(willRouteInApp ? "Ask the Wiki" : "Send to Claude")
                            .font(.system(size: 13, weight: .semibold))
                        Spacer(minLength: 0)
                        Text("⌘↩")
                            .font(.system(size: 11, weight: .semibold, design: .monospaced))
                            .opacity(0.72)
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(!canLaunch)
                .opacity(canLaunch ? 1 : 0.5)
                .frame(maxWidth: 240)
            }
        }
    }

    private var placeholderText: String {
        if stagedFiles.isEmpty {
            return "What do you want to ask your wiki?"
        }
        return "Optional context for the attached files."
    }

    private var canLaunch: Bool {
        !stagedFiles.isEmpty
            || !draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var plainTextLooksLikeURL: Bool {
        let trimmed = draftText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard trimmed.hasPrefix("http://") || trimmed.hasPrefix("https://") else {
            return false
        }
        return !trimmed.contains(where: { $0.isWhitespace })
    }

    private var willRouteInApp: Bool {
        stagedFiles.isEmpty && !plainTextLooksLikeURL
            && !draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func launch() {
        let trimmed = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        if willRouteInApp {
            model.sendQuery(trimmed)
            draftText = ""
            showQueryWindow()
            return
        }
        let errorBefore = model.lastError
        model.launchDraftSession(files: stagedFiles, text: draftText)
        if model.lastError == errorBefore {
            stagedFiles = []
            draftText = ""
        }
    }

    // MARK: - Query response

    @ViewBuilder
    private var queryResponseSection: some View {
        if model.querySession.status != .idle {
            QueryResponseView(
                session: model.querySession,
                onCancel: { model.cancelQuery() },
                onDismiss: { model.dismissQueryResponse() },
                onOpenWiki: { target in model.openWikiPage(target: target) }
            )
            .padding(.horizontal, 24)
            .padding(.bottom, 14)
        }
    }

    // MARK: - Action row

    private var actionRow: some View {
        HStack(spacing: 10) {
            EditorialLaunchTile(
                title: "Terminal",
                caption: "Blank session",
                action: { model.launchBareClaude() }
            ) {
                Image(systemName: "terminal")
                    .font(.system(size: 14, weight: .regular))
            }
            EditorialLaunchTile(
                title: "Obsidian",
                caption: "Open vault",
                action: { model.openWorkspaceInObsidian() }
            ) {
                ObsidianMark(size: 15)
            }
            EditorialLaunchTile(
                title: "Graph",
                caption: "Network view",
                action: { model.openObsidianGraph() }
            ) {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.system(size: 14, weight: .regular))
            }
        }
    }

    private func showQueryWindow() {
        openWindow(id: "query-window")
        NSApplication.shared.activate(ignoringOtherApps: true)
    }
}

// MARK: - Subviews

private struct InsetlessTextEditor: NSViewRepresentable {
    @Binding var text: String
    let font: NSFont
    let textColor: NSColor
    var autoFocus: Bool = false

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSTextView.scrollableTextView()
        scrollView.drawsBackground = false
        scrollView.borderType = .noBorder
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true

        guard let textView = scrollView.documentView as? NSTextView else {
            return scrollView
        }
        textView.delegate = context.coordinator
        textView.isRichText = false
        textView.allowsUndo = true
        textView.drawsBackground = false
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer?.lineFragmentPadding = 0
        textView.font = font
        textView.textColor = textColor
        textView.insertionPointColor = textColor
        textView.string = text
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.isAutomaticTextReplacementEnabled = false
        textView.isAutomaticSpellingCorrectionEnabled = false
        textView.smartInsertDeleteEnabled = false

        if autoFocus {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                textView.window?.makeFirstResponder(textView)
            }
        }
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        guard let textView = scrollView.documentView as? NSTextView else { return }
        if textView.string != text {
            textView.string = text
        }
        if textView.font != font { textView.font = font }
        if textView.textColor != textColor {
            textView.textColor = textColor
            textView.insertionPointColor = textColor
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    final class Coordinator: NSObject, NSTextViewDelegate {
        @Binding var text: String
        init(text: Binding<String>) { self._text = text }
        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            text = tv.string
        }
    }
}

private struct FileChip: View {
    let url: URL
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: iconName)
                .font(.system(size: 11))
                .foregroundStyle(EditorialPalette.accent)
            Text(url.lastPathComponent)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(EditorialPalette.textPrimary)
                .lineLimit(1)
                .truncationMode(.middle)
            Button(action: onRemove) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(EditorialPalette.textTertiary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Capsule().fill(EditorialPalette.surface))
        .overlay(Capsule().strokeBorder(EditorialPalette.border, lineWidth: 1))
    }

    private var iconName: String {
        switch url.pathExtension.lowercased() {
        case "pdf": return "doc.richtext"
        case "md", "markdown", "txt": return "doc.plaintext"
        case "png", "jpg", "jpeg", "gif", "heic": return "photo"
        default: return "doc"
        }
    }
}

struct EditorialLaunchTile<Icon: View>: View {
    let title: String
    let caption: String
    let action: () -> Void
    let icon: Icon

    init(
        title: String,
        caption: String,
        action: @escaping () -> Void,
        @ViewBuilder icon: () -> Icon
    ) {
        self.title = title
        self.caption = caption
        self.action = action
        self.icon = icon()
    }

    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: 10) {
                icon
                    .foregroundStyle(
                        isHovering ? EditorialPalette.accent : EditorialPalette.textSecondary
                    )
                    .frame(width: 18, height: 18)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.system(size: 14, weight: .semibold, design: activeFont.design))
                        .foregroundStyle(EditorialPalette.textPrimary)
                    Text(caption)
                        .font(.system(size: 10))
                        .foregroundStyle(EditorialPalette.textTertiary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 12)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(isHovering ? EditorialPalette.surfaceHover : EditorialPalette.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(
                        isHovering ? EditorialPalette.borderHover : EditorialPalette.border,
                        lineWidth: 1
                    )
            )
            .animation(.easeOut(duration: 0.15), value: isHovering)
        }
        .buttonStyle(.plain)
        .onHover { isHovering = $0 }
    }
}

private struct EditorialSpinner: View {
    var color: Color = EditorialPalette.accent
    var size: CGFloat = 14
    var lineWidth: CGFloat = 1.8

    @State private var isSpinning = false

    var body: some View {
        Circle()
            .trim(from: 0.08, to: 0.92)
            .stroke(
                color,
                style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
            )
            .frame(width: size, height: size)
            .rotationEffect(.degrees(isSpinning ? 360 : 0))
            .animation(
                .linear(duration: 0.9).repeatForever(autoreverses: false),
                value: isSpinning
            )
            .onAppear { isSpinning = true }
    }
}

struct ObsidianMark: View {
    var size: CGFloat = 15
    var lineWidth: CGFloat = 1.3

    var body: some View {
        let w = size
        let h = size
        ZStack {
            Path { p in
                p.move(to: CGPoint(x: w * 0.5,  y: h * 0.04))
                p.addLine(to: CGPoint(x: w * 0.94, y: h * 0.38))
                p.addLine(to: CGPoint(x: w * 0.66, y: h * 0.96))
                p.addLine(to: CGPoint(x: w * 0.34, y: h * 0.96))
                p.addLine(to: CGPoint(x: w * 0.06, y: h * 0.38))
                p.closeSubpath()
            }
            .stroke(style: StrokeStyle(lineWidth: lineWidth, lineJoin: .round))

            Path { p in
                p.move(to: CGPoint(x: w * 0.5,  y: h * 0.04))
                p.addLine(to: CGPoint(x: w * 0.34, y: h * 0.58))
                p.addLine(to: CGPoint(x: w * 0.66, y: h * 0.96))
            }
            .stroke(style: StrokeStyle(lineWidth: lineWidth * 0.85, lineJoin: .round))
            .opacity(0.65)
        }
        .frame(width: w, height: h)
    }
}

// MARK: - Query response

private struct QueryResponseView: View {
    @Bindable var session: QuerySession
    let onCancel: () -> Void
    let onDismiss: () -> Void
    let onOpenWiki: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            responseBody(for: session)
            footer
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(EditorialPalette.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(EditorialPalette.border, lineWidth: 1)
        )
        .environment(\.openURL, OpenURLAction { url in
            if let target = WikilinkParser.decodeLinkURL(url) {
                onOpenWiki(target)
                return .handled
            }
            return .systemAction
        })
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 10) {
            statusGlyph
                .frame(width: 12, height: 12, alignment: .center)
                .padding(.top, 2)
            Text(session.question)
                .font(.system(size: 13, weight: .semibold, design: activeFont.design))
                .foregroundStyle(EditorialPalette.textPrimary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Spacer(minLength: 8)
            if session.status == .running {
                Button(action: onCancel) {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 11))
                        .foregroundStyle(EditorialPalette.textTertiary)
                }
                .buttonStyle(.plain)
                .help("Cancel query")
            } else {
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(EditorialPalette.textTertiary)
                }
                .buttonStyle(.plain)
                .help("Dismiss")
            }
        }
    }

    @ViewBuilder
    private func responseBody(for session: QuerySession) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if session.status == .running && session.assistantText.isEmpty {
                HStack(spacing: 8) {
                    EditorialSpinner(size: 14)
                    Text(session.statusDetail.isEmpty ? "Starting…" : session.statusDetail)
                        .font(.system(size: 13, design: activeFont.design).italic())
                        .foregroundStyle(EditorialPalette.textTertiary)
                        .animation(.easeInOut(duration: 0.2), value: session.statusDetail)
                }
                .padding(.vertical, 10)
            } else if let error = session.errorMessage, session.status == .failed {
                Text(error)
                    .font(.system(size: 12))
                    .foregroundStyle(EditorialPalette.warning)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else if session.status == .cancelled {
                Text("Query cancelled.")
                    .font(.system(size: 13, design: activeFont.design).italic())
                    .foregroundStyle(EditorialPalette.textTertiary)
            } else if !session.assistantText.isEmpty {
                ScrollView {
                    MarkdownContentView(text: session.assistantText) { target in
                        onOpenWiki(target)
                    }
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(idealHeight: 120, maxHeight: 280)
                .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Waiting for response...")
                    .font(.system(size: 13, design: activeFont.design).italic())
                    .foregroundStyle(EditorialPalette.textTertiary)
            }
        }
        .padding(.vertical, 4)
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if session.status == .running {
                if !session.toolCalls.isEmpty {
                    Text("using \(session.toolCalls.suffix(2).joined(separator: ", "))")
                        .font(.system(size: 10))
                        .foregroundStyle(EditorialPalette.textTertiary)
                }
            }
            if session.status == .completed {
                if let ms = session.durationMs {
                    Text("\(Double(ms) / 1000, specifier: "%.1f")s")
                        .font(.system(size: 10))
                        .foregroundStyle(EditorialPalette.textTertiary)
                }
                if !session.toolCalls.isEmpty {
                    Text("tools: \(session.toolCalls.prefix(3).joined(separator: ", "))")
                        .font(.system(size: 10))
                        .foregroundStyle(EditorialPalette.textTertiary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            Spacer(minLength: 0)
            if !session.permissionDenials.isEmpty {
                Text("\(session.permissionDenials.count) tool denials")
                    .font(.system(size: 10))
                    .foregroundStyle(EditorialPalette.warning)
            }
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var statusGlyph: some View {
        switch session.status {
        case .running:
            EditorialSpinner(size: 12)
        case .completed:
            Circle()
                .fill(EditorialPalette.accent)
                .frame(width: 6, height: 6)
        case .failed:
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(EditorialPalette.warning)
                .font(.system(size: 11))
        case .cancelled:
            Image(systemName: "stop.circle.fill")
                .foregroundStyle(EditorialPalette.textTertiary)
                .font(.system(size: 11))
        case .idle:
            EmptyView()
        }
    }

}

// MARK: - Button styles

private struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .foregroundStyle(EditorialPalette.background)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(
                        configuration.isPressed
                            ? EditorialPalette.accentHover
                            : EditorialPalette.accent
                    )
            )
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}

private struct GhostButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .foregroundStyle(EditorialPalette.textSecondary)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(configuration.isPressed ? EditorialPalette.surfaceHover : Color.clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .strokeBorder(EditorialPalette.border, lineWidth: 1)
            )
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}
