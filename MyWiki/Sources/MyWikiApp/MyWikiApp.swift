import SwiftUI
import MyWikiCore

@main
struct MyWikiApp: App {
    @State private var model: AppModel

    init() {
        let model = AppModel()
        self._model = State(initialValue: model)
        Task { @MainActor in
            await model.bootstrapIfNeeded()
        }
    }

    var body: some Scene {
        Window("MyWiki", id: "query-window") {
            QueryDetailView(model: model)
                .task { await model.bootstrapIfNeeded() }
        }
        .defaultSize(width: 560, height: 580)

        MenuBarExtra {
            LauncherView(model: model)
                .task { await model.bootstrapIfNeeded() }
        } label: {
            Image(systemName: "book.closed")
                .symbolRenderingMode(.hierarchical)
        }
        .menuBarExtraStyle(.window)
    }
}
