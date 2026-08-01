import SwiftUI

@main
struct GeomagMacApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 1_050, minHeight: 700)
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("打开结果…") {
                    model.openResultFile()
                }
                .keyboardShortcut("o")
            }
        }
    }
}
