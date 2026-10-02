import SwiftUI

/// Sessions down the side, the selected session's reels and clips on the right,
/// and the live status + controls pinned on top of both.
struct MainView: View {
    @Environment(AppModel.self) private var model
    @State private var selection: String?
    @State private var columns = NavigationSplitViewVisibility.all

    var body: some View {
        // The bar sits ABOVE the split view, not in a safeAreaInset over it:
        // as an inset it covered both columns' navigation bars (the title and
        // the PC menu were unreachable), and when an alert grew it, rows
        // already on screen slid underneath.
        VStack(spacing: 0) {
            VStack(spacing: 0) {
                AlertStack()
                ControlBar()
            }
            .background(Theme.panel.ignoresSafeArea(edges: .top))
            split
        }
        .overlay(alignment: .bottom) { ToastView() }
        .onChange(of: model.sessions.map(\.id)) { _, ids in
            // Open the newest session by default, and follow a new one when it appears.
            if selection == nil || !(ids.contains(selection ?? "")) { selection = ids.first }
        }
        .onAppear { if selection == nil { selection = model.sessions.first?.id } }
    }

    private var split: some View {
        NavigationSplitView(columnVisibility: $columns) {
            SessionsSidebar(selection: $selection)
                .navigationTitle("Sessions")
                .toolbar { ToolbarItem(placement: .topBarTrailing) { ServerMenu() } }
        } detail: {
            NavigationStack {
                if let id = selection {
                    SessionDetailView(sessionID: id)
                        .id(id)
                } else {
                    ContentUnavailableView(
                        model.sessionsLoaded && model.sessions.isEmpty
                            ? "No sessions yet" : "Pick a session",
                        systemImage: "film.stack",
                        description: Text(model.sessionsLoaded && model.sessions.isEmpty
                            ? "Play a session with WITNESS running and it shows up here."
                            : "Its reels and clips open here."))
                }
            }
        }
        .navigationSplitViewStyle(.balanced)
    }
}

struct SessionsSidebar: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: String?

    var body: some View {
        List(selection: $selection) {
            if !model.recordDirKnown {
                Label("The PC doesn't know where OBS saves clips yet. Start one session "
                      + "with OBS open and it will.", systemImage: "folder.badge.questionmark")
                    .font(.footnote)
                    .foregroundStyle(Theme.warn)
                    .listRowBackground(Theme.panel)
            }
            ForEach(model.sessions) { s in
                SessionRow(session: s)
                    .tag(s.id)
                    .listRowBackground(selection == s.id ? Theme.raised : Theme.panel)
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .refreshable { await model.refreshSessions() }
        .overlay {
            if !model.sessionsLoaded {
                ProgressView()
            }
        }
    }
}

struct SessionRow: View {
    let session: SessionSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(session.title)
                .font(.headline)
                .foregroundStyle(Theme.text)
            HStack(spacing: 8) {
                Text("\(session.clips) clip\(session.clips == 1 ? "" : "s")")
                if let size = session.size_label, (session.size ?? 0) > 0 {
                    Text("·")
                    Text(size)
                }
                if session.media.contains(where: { $0.kind == "potn" }) {
                    Image(systemName: "star.fill")
                        .foregroundStyle(Theme.warn)
                        .accessibilityLabel("Has a play of the night")
                }
                if session.failed == true {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(Theme.danger)
                        .accessibilityLabel("Reels failed to build")
                }
            }
            .font(.caption)
            .foregroundStyle(Theme.muted)
        }
        .padding(.vertical, 4)
    }
}

/// Which PC, and the way out.
struct ServerMenu: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Menu {
            Section(model.serverName + (model.serverVersion.isEmpty ? "" : " · \(model.serverVersion)")) {
                Toggle("Keep iPad awake while recording", isOn: $model.keepAwake)
                Button("Switch PC", systemImage: "arrow.left.arrow.right") {
                    model.disconnect(forget: true)
                }
            }
            if let update = model.status?.update, !update.isEmpty {
                Section("Update") { Text(update) }
            }
        } label: {
            Image(systemName: model.reachable ? "desktopcomputer" : "desktopcomputer.trianglebadge.exclamationmark")
                .foregroundStyle(model.reachable ? Theme.accent : Theme.warn)
        }
        .accessibilityLabel("PC: \(model.serverName)")
    }
}

struct ToastView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let t = model.toast {
            Text(t)
                .font(.callout.weight(.semibold))
                .foregroundStyle(Theme.text)
                .padding(.horizontal, 18)
                .padding(.vertical, 10)
                .background(Theme.raised, in: Capsule())
                .overlay(Capsule().strokeBorder(Theme.line))
                .padding(.bottom, 24)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .animation(.snappy, value: model.toast)
        }
    }
}
