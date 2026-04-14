import SwiftUI
import UniformTypeIdentifiers
import MyWikiCore

struct ContentView: View {
    @Bindable var model: AppModel
    @State private var isDropTargeted = false

    var body: some View {
        VStack(spacing: 16) {
            header
            HSplitView {
                VStack(spacing: 16) {
                    dropZone
                    urlEntry
                    feedList
                }
                .frame(minWidth: 380)

                VStack(spacing: 12) {
                    chatHeader
                    chatMessages
                    chatEntry
                }
                .frame(minWidth: 320)
            }
        }
        .padding(20)
        .toolbar {
            ToolbarItemGroup(placement: .automatic) {
                Menu {
                    if let workspace = model.workspace {
                        Button(workspace.path) {}
                            .disabled(true)
                    }
                    Divider()
                    ForEach(model.recentWorkspacePaths, id: \.self) { path in
                        Button(path) {
                            model.selectRecentWorkspace(path)
                        }
                    }
                    Divider()
                    Button("Open Other Workspace") {
                        model.chooseOtherWorkspace()
                    }
                } label: {
                    Label(model.workspace?.topic ?? "Workspace", systemImage: "folder")
                }

                Button {
                    model.openWorkspaceInTerminal()
                } label: {
                    Label("Open in Terminal", systemImage: "terminal")
                }
                .disabled(model.workspace == nil)
                .help("Open Terminal at the workspace root and start Claude Code")

                Button {
                    model.openWorkspaceInObsidian()
                } label: {
                    Label("Open Obsidian", systemImage: "doc.text.magnifyingglass")
                }
                .disabled(model.workspace == nil)
                .help("Open this vault in Obsidian")
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("MyWiki")
                .font(.largeTitle)
                .fontWeight(.semibold)
            Text(model.workspace?.path ?? model.statusMessage)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            if let lastError = model.lastError {
                Text(lastError)
                    .font(.subheadline)
                    .foregroundStyle(.red)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var dropZone: some View {
        VStack(spacing: 8) {
            Image(systemName: "tray.and.arrow.down")
                .font(.system(size: 36))
            Text("Drop files to ingest via Claude")
                .font(.headline)
            Text("Each file is copied into raw/ and handed to a Claude session in Terminal.")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Choose Files") {
                model.chooseFilesForIngest()
            }
            .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 150)
        .background(isDropTargeted ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(isDropTargeted ? Color.accentColor : Color.secondary.opacity(0.25), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .contentShape(Rectangle())
        .dropDestination(for: URL.self) { items, _ in
            model.enqueueFiles(items)
            return true
        } isTargeted: { targeted in
            isDropTargeted = targeted
        }
        .onTapGesture {
            model.chooseFilesForIngest()
        }
    }

    private var urlEntry: some View {
        HStack(spacing: 10) {
            TextField("Paste a URL", text: $model.urlInput)
                .textFieldStyle(.roundedBorder)
                .onSubmit {
                    model.enqueueCurrentURL()
                }
            Button("Ingest") {
                model.enqueueCurrentURL()
            }
            .disabled(model.urlInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }

    private var feedList: some View {
        List {
            ForEach(model.feedStore.items.reversed()) { item in
                FeedRowView(item: item) {
                    model.openFeedItem(item)
                }
            }
        }
        .listStyle(.inset)
    }

    private var chatHeader: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Ask MyWiki")
                .font(.title3)
                .fontWeight(.semibold)
            Text("Questions open Claude in Terminal from the wiki home. Local search is available as a quick index lookup.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var chatMessages: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                ForEach(model.chatMessages) { message in
                    ChatMessageView(message: message)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(12)
        .background(Color.secondary.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var chatEntry: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Ask about your wiki", text: $model.chatInput, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...4)
                .onSubmit {
                    model.sendChatToClaude()
                }
            Button("Ask Claude") {
                model.sendChatToClaude()
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .help("Open Terminal and run claude \"/query <your question>\"")
            Button("Search Index") {
                model.sendChat()
            }
            .disabled(model.chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }
}

private struct FeedRowView: View {
    let item: FeedItem
    let openAction: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: iconName)
                    .foregroundStyle(iconColor)
                    .frame(width: 20)
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.source)
                        .font(.headline)
                    Text(item.stage)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    if let prompt = item.prompt, !prompt.isEmpty {
                        Text(prompt)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                }
                Spacer()
                if item.stagedRelativePath != nil, item.status != .failed {
                    Button("Reveal File") {
                        openAction()
                    }
                    .buttonStyle(.borderless)
                }
            }

            if item.status == .failed, let message = item.errorMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
        .padding(.vertical, 6)
    }

    private var iconName: String {
        switch item.status {
        case .queued:
            return "clock"
        case .staging:
            return "arrow.down.doc"
        case .launching:
            return "terminal"
        case .launched:
            return "checkmark.circle.fill"
        case .failed:
            return "xmark.octagon.fill"
        }
    }

    private var iconColor: Color {
        switch item.status {
        case .queued, .staging:
            return .secondary
        case .launching:
            return .blue
        case .launched:
            return .green
        case .failed:
            return .red
        }
    }
}

private struct ChatMessageView: View {
    let message: ChatMessage

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(message.role == .user ? "You" : "MyWiki")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(message.text)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            if !message.references.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(message.references.prefix(3), id: \.relativePath) { hit in
                        Text("\(hit.title) • \(hit.pageType)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(10)
        .background(message.role == .user ? Color.accentColor.opacity(0.12) : Color.white.opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
