import SwiftUI

/// Live status and the four buttons the dashboard has: start/stop, save a clip,
/// and add a kill WITNESS missed.
struct ControlBar: View {
    @Environment(AppModel.self) private var model
    @State private var confirmStop = false
    @State private var pulse = false

    var body: some View {
        let s = model.status
        let running = s?.running ?? false
        HStack(spacing: 14) {
            HStack(spacing: 8) {
                Circle()
                    .fill(running ? Theme.danger : Theme.muted)
                    .frame(width: 10, height: 10)
                    .opacity(running && pulse ? 0.35 : 1)
                    .animation(running ? .easeInOut(duration: 0.9).repeatForever() : .default, value: pulse)
                    .onAppear { pulse = true }
                Text(!model.reachable ? "PC OFFLINE" : running ? "RECORDING" : "IDLE")
                    .font(.caption.weight(.heavy))
                    .tracking(1.5)
                    .foregroundStyle(!model.reachable ? Theme.warn : running ? Theme.text : Theme.muted)
            }
            if running {
                Text("\(s?.count ?? 0) kill\(s?.count == 1 ? "" : "s")")
                    .font(.subheadline.weight(.bold).monospacedDigit())
                    .foregroundStyle(Theme.text)
                if let e = s?.elapsed, e > 0 {
                    Text(Format.clock(e))
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(Theme.muted)
                }
                if let label = s?.heat?.label, !label.isEmpty {
                    Text(label)
                        .font(.caption.weight(.heavy))
                        .tracking(1)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .foregroundStyle(Color(css: s?.heat?.color))
                        .background(Color(css: s?.heat?.color).opacity(0.15), in: Capsule())
                }
            } else if let note = s?.recap?.note, !note.isEmpty {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            if running {
                barButton("Clip", "record.circle", tint: Theme.accent) {
                    await model.perform(.clip)
                }
                barButton("+ Kill", "plus.circle", tint: Theme.accent) {
                    await model.perform(.addKill)
                }
                barButton("Stop", "stop.fill", tint: Theme.danger) {
                    confirmStop = true
                }
                // On the button, so the iPad popover points at Stop rather
                // than at the middle of the bar.
                .confirmationDialog("Stop the session?", isPresented: $confirmStop,
                                    titleVisibility: .visible) {
                    Button("Stop and build reels", role: .destructive) {
                        Task { await model.perform(.stop) }
                    }
                } message: {
                    Text("WITNESS stops watching and builds tonight's reels on the PC.")
                }
            } else {
                barButton("Start", "play.fill", tint: Theme.ok) {
                    await model.perform(.start)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { Theme.line.frame(height: 1) }
        .disabled(!model.reachable)
    }

    private func barButton(_ title: String, _ icon: String, tint: Color,
                           action: @escaping () async -> Void) -> some View {
        Button {
            Task { await action() }
        } label: {
            Label(title, systemImage: icon)
                .font(.subheadline.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .tint(tint)
    }
}

/// Red/amber banners for whatever the PC is shouting about right now.
struct AlertStack: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 0) {
            ForEach(model.activeAlerts) { a in
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: a.isCritical ? "exclamationmark.octagon.fill" : "exclamationmark.triangle.fill")
                        .font(.title3)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(a.title).font(.headline)
                        if let m = a.message, !m.isEmpty {
                            Text(m).font(.callout).opacity(0.9)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity)
                .background(a.isCritical ? Theme.danger : Theme.warn)
                .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.snappy, value: model.activeAlerts)
    }
}
