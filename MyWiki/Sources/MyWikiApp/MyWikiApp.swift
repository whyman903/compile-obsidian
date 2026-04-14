import SwiftUI
import MyWikiCore

@main
struct MyWikiApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .frame(minWidth: 760, minHeight: 560)
                .task {
                    await model.bootstrapIfNeeded()
                }
        }
        .commands {
            CommandGroup(after: .newItem) {
                Button("Open Other Workspace") {
                    model.chooseOtherWorkspace()
                }
            }
        }
    }
}
