import SwiftUI
import MyWikiCore

struct QueryDetailView: View {
    @Bindable var model: AppModel
    @State private var followUpText = ""
    @State private var sidebarVisible = false
    @State private var showSettings = false
    @FocusState private var isInputFocused: Bool

    var body: some View {
        HStack(spacing: 0) {
            if sidebarVisible {
                historySidebar
                    .frame(width: 200)
                    .transition(.move(edge: .leading))
                Divider().overlay(EditorialPalette.border)
            }

            VStack(spacing: 0) {
                toolbar
                Divider().overlay(EditorialPalette.border)
                if showSettings {
                    SettingsView(model: model, onDismiss: { showSettings = false })
                } else {
                    conversationArea
                    bottomPanel
                }
            }
        }
        .background(EditorialPalette.background)
        .id("\(model.theme.rawValue).\(model.font.rawValue)")
        .preferredColorScheme(model.theme.prefersDarkMode ? .dark : .light)
        .environment(\.openURL, OpenURLAction { url in
            if let target = WikilinkParser.decodeLinkURL(url) {
                model.openWikiPage(target: target)
                return .handled
            }
            return .systemAction
        })
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

    // MARK: - Toolbar

    private var toolbar: some View {
        HStack(spacing: 12) {
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    sidebarVisible.toggle()
                }
            }) {
                Image(systemName: "sidebar.left")
                    .font(.system(size: 12))
                    .foregroundStyle(sidebarVisible
                                    ? EditorialPalette.accent
                                    : EditorialPalette.textTertiary)
            }
            .buttonStyle(.plain)
            .help(sidebarVisible ? "Hide history" : "Show history")

            Spacer()

            Text(model.querySession.firstQuestion.isEmpty
                 ? "New Query"
                 : String(model.querySession.firstQuestion.prefix(50)))
                .font(.system(size: 13, weight: .medium, design: activeFont.design))
                .foregroundStyle(EditorialPalette.textSecondary)
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer()

            Button(action: {
                model.startNewQuery()
                followUpText = ""
                isInputFocused = true
            }) {
                Image(systemName: "plus")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(EditorialPalette.textTertiary)
            }
            .buttonStyle(.plain)
            .help("New query")

            Button(action: { showSettings.toggle() }) {
                Image(systemName: "gearshape")
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(showSettings
                                    ? EditorialPalette.accent
                                    : EditorialPalette.textTertiary)
            }
            .buttonStyle(.plain)
            .help(showSettings ? "Back" : "Settings")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(EditorialPalette.backgroundTop)
    }

    // MARK: - Conversation

    private var conversationArea: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    ForEach(model.querySession.turns) { turn in
                        turnView(turn)
                    }

                    if model.querySession.status == .running || model.querySession.status == .failed {
                        activeTurnView
                    }

                    Color.clear.frame(height: 1).id("bottom")
                }
                .frame(maxWidth: 780, alignment: .leading)
                .padding(.horizontal, 28)
                .padding(.vertical, 24)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .background(EditorialPalette.background)
            .onChange(of: model.querySession.turns.count) {
                withAnimation {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
            .onChange(of: model.querySession.assistantText) {
                proxy.scrollTo("bottom", anchor: .bottom)
            }
        }
    }

    private func turnView(_ turn: QueryTurn) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            questionHeader(turn.question)
            MarkdownContentView(text: turn.answer) { target in
                model.openWikiPage(target: target)
            }
            .padding(.leading, 15)
        }
    }

    @ViewBuilder
    private var activeTurnView: some View {
        VStack(alignment: .leading, spacing: 10) {
            questionHeader(model.querySession.question)

            if model.querySession.status == .running && model.querySession.assistantText.isEmpty {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(model.querySession.statusDetail.isEmpty
                         ? "Starting…" : model.querySession.statusDetail)
                        .font(.system(size: 13, design: activeFont.design).italic())
                        .foregroundStyle(EditorialPalette.textTertiary)
                }
                .padding(.leading, 15)
            } else if model.querySession.status == .failed {
                Text(model.querySession.errorMessage ?? "Query failed")
                    .font(.system(size: 13))
                    .foregroundStyle(EditorialPalette.warning)
                    .textSelection(.enabled)
                    .padding(.leading, 15)
            } else if !model.querySession.assistantText.isEmpty {
                MarkdownContentView(text: model.querySession.assistantText) { target in
                    model.openWikiPage(target: target)
                }
                .padding(.leading, 15)
            }
        }
    }

    private func questionHeader(_ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 9) {
            Circle()
                .fill(EditorialPalette.accent)
                .frame(width: 6, height: 6)
            Text(text)
                .font(.system(size: 13, weight: .semibold, design: activeFont.design))
                .foregroundStyle(EditorialPalette.textPrimary)
                .textSelection(.enabled)
        }
    }

    // MARK: - Follow-up input

    private var bottomPanel: some View {
        VStack(spacing: 10) {
            followUpBar
            launchRow
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(EditorialPalette.backgroundTop)
        .overlay(alignment: .top) {
            Rectangle()
                .fill(EditorialPalette.border)
                .frame(height: 1)
        }
    }

    private var followUpBar: some View {
        HStack(spacing: 10) {
            TextField(
                model.querySession.turns.isEmpty ? "Ask the wiki…" : "Ask a follow-up…",
                text: $followUpText,
                axis: .vertical
            )
            .textFieldStyle(.plain)
            .font(.system(size: 13, design: activeFont.design))
            .foregroundStyle(EditorialPalette.textPrimary)
            .focused($isInputFocused)
            .lineLimit(1...4)
            .onSubmit { submitFollowUp() }

            Button(action: submitFollowUp) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 20))
                    .foregroundStyle(followUpText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                    ? EditorialPalette.textTertiary
                                    : EditorialPalette.accent)
            }
            .buttonStyle(.plain)
            .disabled(followUpText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .keyboardShortcut(.return, modifiers: .command)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(EditorialPalette.background)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(EditorialPalette.border, lineWidth: 1)
        )
    }

    private func submitFollowUp() {
        let text = followUpText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        followUpText = ""

        if model.querySession.turns.isEmpty && model.querySession.status == .idle {
            model.sendQuery(text)
        } else {
            model.sendFollowUp(text)
        }
    }

    // MARK: - Launch row

    private var launchRow: some View {
        HStack(spacing: 8) {
            QueryActionButton(
                title: "Terminal",
                action: { model.launchBareClaude() }
            ) {
                Image(systemName: "terminal")
                    .font(.system(size: 12, weight: .regular))
            }
            QueryActionButton(
                title: "Obsidian",
                action: { model.openWorkspaceInObsidian() }
            ) {
                ObsidianMark(size: 13)
            }
            QueryActionButton(
                title: "Graph",
                action: { model.openObsidianGraph() }
            ) {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.system(size: 12, weight: .regular))
            }
            QueryActionButton(
                title: "Files",
                action: { model.chooseFilesForIngest() }
            ) {
                Image(systemName: "doc.badge.plus")
                    .font(.system(size: 12, weight: .regular))
            }
        }
    }

    private var hasAnySessions: Bool {
        model.hasActiveQuerySession || !model.sidebarQueryHistory.isEmpty
    }

    // MARK: - History sidebar

    private var historySidebar: some View {
        VStack(spacing: 0) {
            Text("HISTORY")
                .font(.system(size: 10, weight: .bold))
                .kerning(1.3)
                .foregroundStyle(EditorialPalette.textTertiary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .padding(.top, 14)
                .padding(.bottom, 8)

            if !hasAnySessions {
                Spacer()
                Text("No queries yet")
                    .font(.system(size: 12, design: activeFont.design).italic())
                    .foregroundStyle(EditorialPalette.textTertiary)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        // Active session
                        if model.hasActiveQuerySession {
                            sidebarRow(
                                label: model.querySession.firstQuestion,
                                isActive: true,
                                action: {}
                            )
                        }
                        // Archived history
                        ForEach(model.sidebarQueryHistory) { record in
                            sidebarRow(
                                label: record.firstQuestion,
                                isActive: false,
                                action: {
                                    model.selectHistorySession(record)
                                    followUpText = ""
                                }
                            )
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .background(EditorialPalette.backgroundTop)
    }

    private func sidebarRow(label: String, isActive: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 12, design: activeFont.design))
                .foregroundStyle(isActive
                                 ? EditorialPalette.textPrimary
                                 : EditorialPalette.textSecondary)
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(
                    isActive
                        ? RoundedRectangle(cornerRadius: 5, style: .continuous)
                            .fill(EditorialPalette.surface)
                        : nil
                )
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 4)
    }
}

private struct QueryActionButton<Icon: View>: View {
    let title: String
    let action: () -> Void
    let icon: Icon

    init(
        title: String,
        action: @escaping () -> Void,
        @ViewBuilder icon: () -> Icon
    ) {
        self.title = title
        self.action = action
        self.icon = icon()
    }

    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                icon
                    .foregroundStyle(isHovering ? EditorialPalette.accent : EditorialPalette.textSecondary)
                    .frame(width: 15, height: 15)
                Text(title)
                    .font(.system(size: 12, weight: .medium, design: activeFont.design))
                    .foregroundStyle(EditorialPalette.textSecondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 9)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(isHovering ? EditorialPalette.surface : Color.clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .strokeBorder(isHovering ? EditorialPalette.border : Color.clear, lineWidth: 1)
            )
            .animation(.easeOut(duration: 0.12), value: isHovering)
        }
        .buttonStyle(.plain)
        .onHover { isHovering = $0 }
    }
}
