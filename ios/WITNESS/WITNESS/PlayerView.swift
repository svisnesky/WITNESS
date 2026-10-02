import AVKit
import SwiftUI

/// One thing to play: a clip (starts just before its first kill) or a reel.
struct PlayItem: Hashable {
    let title: String
    let subtitle: String
    let path: String        // server path, e.g. /api/play?s=...&f=...
    let startAt: Double

    init(clip: Clip) {
        title = clip.victim?.isEmpty == false ? clip.victim! : (clip.summary ?? clip.name)
        subtitle = [clip.clock, clip.chips?.map(\.label).joined(separator: " · ")]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: "  ")
        path = clip.url
        startAt = clip.startAt
    }

    init(media: Media) {
        title = media.label
        subtitle = ""
        path = media.url
        startAt = 0
    }
}

struct PlayRequest: Identifiable {
    let id = UUID()
    let items: [PlayItem]
    let index: Int
}

struct PlayerView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let request: PlayRequest

    @State private var index: Int
    @State private var player = AVPlayer()
    @State private var failed: String?
    @State private var loading = true

    init(request: PlayRequest) {
        self.request = request
        _index = State(initialValue: request.index)
    }

    private var item: PlayItem { request.items[index] }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VideoPlayer(player: player)
                .ignoresSafeArea()
            if loading && failed == nil {
                VStack(spacing: 10) {
                    ProgressView().controlSize(.large).tint(.white)
                    // The first open of an .mkv clip remuxes it on the PC (~1s).
                    Text("Getting it from the PC…").font(.footnote).foregroundStyle(.white.opacity(0.7))
                }
                .allowsHitTesting(false)
            }
            if let failed {
                ContentUnavailableView("Can't play this one", systemImage: "exclamationmark.triangle",
                                       description: Text(failed))
                    .foregroundStyle(.white)
            }
        }
        .overlay(alignment: .top) { topBar }
        .task(id: index) { await load() }
        .onDisappear { player.pause() }
        .statusBarHidden()
    }

    private var topBar: some View {
        HStack(spacing: 12) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.headline)
                    .frame(width: 40, height: 40)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .accessibilityLabel("Close")
            VStack(alignment: .leading, spacing: 1) {
                Text(item.title).font(.headline).lineLimit(1)
                if !item.subtitle.isEmpty {
                    Text(item.subtitle).font(.caption).opacity(0.75).lineLimit(1)
                }
            }
            Spacer()
            if request.items.count > 1 {
                Text("\(index + 1) / \(request.items.count)")
                    .font(.caption.monospacedDigit())
                    .opacity(0.75)
                Button {
                    index -= 1
                } label: {
                    Image(systemName: "backward.end.fill").frame(width: 40, height: 40)
                        .background(.ultraThinMaterial, in: Circle())
                }
                .disabled(index == 0)
                .accessibilityLabel("Previous")
                Button {
                    index += 1
                } label: {
                    Image(systemName: "forward.end.fill").frame(width: 40, height: 40)
                        .background(.ultraThinMaterial, in: Circle())
                }
                .disabled(index >= request.items.count - 1)
                .accessibilityLabel("Next")
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16)
        .padding(.top, 10)
    }

    private func load() async {
        guard let url = model.mediaURL(item.path) else { return }
        failed = nil
        loading = true
        let av = AVPlayerItem(url: url)
        player.replaceCurrentItem(with: av)
        // Wait for the item to be playable before seeking, so the seek lands.
        // Polled, not observed: `av.publisher(for: \.status).values` delivered
        // only the initial .unknown and never the change to .readyToPlay, even
        // while the player was already drawing frames, so the spinner never
        // cleared. 30s covers the PC remuxing a long montage on first open.
        for _ in 0..<300 {
            if Task.isCancelled { return }
            switch av.status {
            case .readyToPlay:
                if item.startAt > 0 {
                    await player.seek(to: CMTime(seconds: item.startAt, preferredTimescale: 600),
                                      toleranceBefore: .zero, toleranceAfter: .zero)
                }
                if Task.isCancelled { return }
                loading = false
                player.play()
                return
            case .failed:
                loading = false
                failed = av.error?.localizedDescription
                    ?? "The PC couldn't turn this clip into something an iPad plays."
                return
            default:
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
        loading = false
        failed = "The PC took too long to send this clip."
    }
}
