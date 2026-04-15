import AppKit
import SwiftUI
import UniformTypeIdentifiers
import MyWikiCore

private enum EditorialPalette {
    static let background    = Color(red: 0.0902, green: 0.0706, blue: 0.0549)
    static let backgroundTop = Color(red: 0.1098, green: 0.0902, blue: 0.0667)
    static let surface       = Color(red: 0.1216, green: 0.1020, blue: 0.0784)
    static let surfaceHover  = Color(red: 0.1490, green: 0.1255, blue: 0.0980)
    static let border        = Color(red: 0.1765, green: 0.1490, blue: 0.1255)
    static let borderHover   = Color(red: 0.2275, green: 0.1922, blue: 0.1608)
    static let textPrimary   = Color(red: 0.9608, green: 0.9333, blue: 0.8745)
    static let textSecondary = Color(red: 0.7647, green: 0.7216, blue: 0.6275)
    static let textTertiary  = Color(red: 0.4980, green: 0.4471, blue: 0.3765)
    static let accent        = Color(red: 0.8314, green: 0.6588, blue: 0.3529)
    static let accentHover   = Color(red: 0.8941, green: 0.7255, blue: 0.4078)
    static let warning       = Color(red: 0.8500, green: 0.5200, blue: 0.3200)
}

struct LauncherView: View {
    @Bindable var model: AppModel
    @State private var draftText: String = ""
    @State private var stagedFiles: [URL] = []
    @State private var isDropTargeted = false
    @FocusState private var isDraftFocused: Bool

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
                .padding(.bottom, 16)

            Divider().overlay(EditorialPalette.border)

            recentSection
                .padding(.horizontal, 24)
                .padding(.top, 16)
                .padding(.bottom, 20)
        }
        .frame(width: 460)
        .background(backgroundLayer)
        .preferredColorScheme(.dark)
        .onAppear {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                isDraftFocused = true
            }
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
                    .font(.system(size: 22, weight: .medium, design: .serif))
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
                        .font(.system(size: 15, design: .serif).italic())
                        .foregroundStyle(EditorialPalette.textTertiary)
                        .padding(.horizontal, 16)
                        .padding(.top, 14)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $draftText)
                    .focused($isDraftFocused)
                    .scrollContentBackground(.hidden)
                    .font(.system(size: 13))
                    .foregroundStyle(EditorialPalette.textPrimary)
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

    // MARK: - Recent section

    private var recentSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("RECENT")
                    .font(.system(size: 10, weight: .bold))
                    .kerning(1.3)
                    .foregroundStyle(EditorialPalette.textTertiary)
                Spacer()
                if !model.feedStore.items.isEmpty {
                    Text("\(model.feedStore.items.count) total")
                        .font(.system(size: 10))
                        .foregroundStyle(EditorialPalette.textTertiary.opacity(0.75))
                }
            }

            let recent = Array(model.feedStore.items.suffix(4).reversed())
            if recent.isEmpty {
                Text("Nothing dispatched yet.")
                    .font(.system(size: 13, design: .serif).italic())
                    .foregroundStyle(EditorialPalette.textTertiary)
                    .padding(.vertical, 6)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(recent.enumerated()), id: \.element.id) { offset, item in
                        RecentRow(item: item) { model.openFeedItem(item) }
                        if offset < recent.count - 1 {
                            Divider()
                                .overlay(EditorialPalette.border)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Subviews

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

private struct EditorialLaunchTile<Icon: View>: View {
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
                        .font(.system(size: 14, weight: .semibold, design: .serif))
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

private struct ObsidianMark: View {
    var color: Color = EditorialPalette.textSecondary
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
            .stroke(
                color,
                style: StrokeStyle(lineWidth: lineWidth, lineJoin: .round)
            )

            Path { p in
                p.move(to: CGPoint(x: w * 0.5,  y: h * 0.04))
                p.addLine(to: CGPoint(x: w * 0.34, y: h * 0.58))
                p.addLine(to: CGPoint(x: w * 0.66, y: h * 0.96))
            }
            .stroke(
                color.opacity(0.65),
                style: StrokeStyle(lineWidth: lineWidth * 0.85, lineJoin: .round)
            )
        }
        .frame(width: w, height: h)
    }
}

private struct RecentRow: View {
    let item: FeedItem
    let action: () -> Void

    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(primaryLine)
                        .font(.system(size: 13, design: .serif))
                        .foregroundStyle(EditorialPalette.textPrimary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(secondaryLine)
                        .font(.system(size: 10))
                        .foregroundStyle(secondaryColor)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                Spacer(minLength: 8)
                Text(relativeTime)
                    .font(.system(size: 10))
                    .foregroundStyle(EditorialPalette.textTertiary)
            }
            .padding(.horizontal, 4)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(isHovering ? EditorialPalette.surfaceHover.opacity(0.5) : .clear)
            )
        }
        .buttonStyle(.plain)
        .onHover { isHovering = $0 }
    }

    private var primaryLine: String {
        item.prompt ?? item.source
    }

    private var secondaryLine: String {
        if item.status == .failed, let error = item.errorMessage {
            return error
        }
        return item.source
    }

    private var secondaryColor: Color {
        item.status == .failed ? EditorialPalette.warning : EditorialPalette.textTertiary
    }

    private var relativeTime: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: item.createdAt, relativeTo: Date())
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
                .font(.system(size: 13, weight: .semibold, design: .serif))
                .foregroundStyle(EditorialPalette.textPrimary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
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
                        .font(.system(size: 13, design: .serif).italic())
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
                    .font(.system(size: 13, design: .serif).italic())
                    .foregroundStyle(EditorialPalette.textTertiary)
            } else if !session.assistantText.isEmpty {
                ScrollView {
                    Text(WikilinkParser.attributedString(session.assistantText))
                        .font(.system(size: 14, design: .serif))
                        .foregroundStyle(EditorialPalette.textPrimary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .lineSpacing(2)
                }
                .frame(idealHeight: 120, maxHeight: 280)
                .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Waiting for response...")
                    .font(.system(size: 13, design: .serif).italic())
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
