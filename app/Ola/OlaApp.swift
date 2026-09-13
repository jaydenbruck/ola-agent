import SwiftUI

@main
struct OlaApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            ChatView().environmentObject(model).tint(Palette.ink).preferredColorScheme(.light)
                .task { model.start() }
                .onChange(of: phase) { _, value in
                    if value == .active { model.start() } else if value == .background { model.stop() }
                }
        }
    }
}
