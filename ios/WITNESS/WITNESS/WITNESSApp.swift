import SwiftUI

@main
struct WITNESSApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    init() {
        Notifier.shared.install()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .preferredColorScheme(.dark)
                .tint(Theme.accent)
        }
        .onChange(of: scenePhase) { _, phase in
            // iOS suspends a backgrounded app anyway; stop asking the PC for
            // status nobody can see, and catch up the moment it's back.
            switch phase {
            case .active where model.client != nil:
                model.startPolling()
            case .background:
                model.stopPolling()
            default:
                break
            }
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model
    @State private var triedSaved = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            if model.client != nil {
                MainView()
            } else if !triedSaved && model.savedHost != nil {
                // Reconnect to the last PC before showing the picker.
                ProgressView("Connecting…")
                    .foregroundStyle(Theme.muted)
            } else {
                ConnectView()
            }
        }
        .task {
            if let url = model.savedHost, model.client == nil {
                await model.connect(to: url)
            }
            triedSaved = true
        }
    }
}
