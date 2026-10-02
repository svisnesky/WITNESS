import SwiftUI

/// One session: its reels across the top, then every clip in play order.
struct SessionDetailView: View {
    @Environment(AppModel.self) private var model
    let sessionID: String

    @State private var detail: SessionDetail?
    @State private var loadError: String?
    @State private var playing: PlayRequest?
    @State private var showReport = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                if let detail {
                    content(detail)
                } else if let loadError {
                    ContentUnavailableView("Couldn't load this session",
                                           systemImage: "wifi.exclamationmark",
                                           description: Text(loadError))
                        .padding(.top, 60)
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 80)
                }
            }
            .padding(20)
        }
        .background(Theme.bg)
        .navigationTitle(SessionDate.pretty(sessionID))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if detail?.report?.isEmpty == false {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Report", systemImage: "doc.text") { showReport = true }
                }
            }
        }
        .refreshable { await load() }
        .task { await load() }
        .fullScreenCover(item: $playing) { req in
            PlayerView(request: req)
        }
        .sheet(isPresented: $showReport) {
            ReportView(text: detail?.report ?? "")
        }
    }

    @ViewBuilder
    private func content(_ d: SessionDetail) -> some View {
        let clips = d.clips ?? []
        let media = (d.media ?? []).filter(\.isVideo).sorted { $0.sortKey < $1.sortKey }

        if !media.isEmpty {
            section("REELS") {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 14) {
                        ForEach(media) { m in
                            MediaCard(media: m) {
                                playing = PlayRequest(items: media.map(PlayItem.init(media:)),
                                                      index: media.firstIndex(of: m) ?? 0)
                            }
                        }
                    }
                }
            }
        }

        section(clips.isEmpty ? "CLIPS" : "CLIPS · \(clips.count)") {
            if clips.isEmpty {
                Text("No clips in this session.")
                    .foregroundStyle(Theme.muted)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 180, maximum: 360), spacing: 14, alignment: .top)],
                          spacing: 14) {
                    ForEach(Array(clips.enumerated()), id: \.element.id) { i, clip in
                        ClipCard(clip: clip, thumb: model.mediaURL(clip.thumb)) {
                            playing = PlayRequest(items: clips.map(PlayItem.init(clip:)), index: i)
                        }
                    }
                }
            }
        }
    }

    private func section<C: View>(_ title: String, @ViewBuilder _ body: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.caption.weight(.bold))
                .tracking(1.5)
                .foregroundStyle(Theme.muted)
            body()
        }
    }

    private func load() async {
        do {
            let d = try await model.loadSession(sessionID)
            if let err = d.error, !err.isEmpty {
                loadError = err
            } else {
                detail = d
                loadError = nil
            }
        } catch {
            if detail == nil { loadError = error.localizedDescription }
        }
    }
}

struct MediaCard: View {
    let media: Media
    let action: () -> Void

    private var icon: String {
        switch media.kind {
        case "potn": return "star.fill"
        case "session_reel", "montage": return "film.stack.fill"
        case "short": return "rectangle.portrait.fill"
        default: return "film.fill"
        }
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                Image(systemName: icon)
                    .font(.title2)
                    .foregroundStyle(media.kind == "potn" ? Theme.warn : Theme.accent)
                Spacer(minLength: 0)
                Text(media.label)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)
                Label("Play", systemImage: "play.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.accentLight)
            }
            .frame(width: 168, height: 128, alignment: .leading)
            .panel()
        }
        .buttonStyle(.plain)
    }
}

struct ClipCard: View {
    let clip: Clip
    let thumb: URL?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 0) {
                ZStack(alignment: .bottomTrailing) {
                    Color.black
                    AsyncImage(url: thumb) { phase in
                        switch phase {
                        case .success(let img):
                            img.resizable().scaledToFill()
                        case .failure:
                            Image(systemName: "film").font(.largeTitle).foregroundStyle(Theme.line)
                        default:
                            ProgressView()
                        }
                    }
                    if let d = clip.duration, d > 0 {
                        Text(Format.clock(Int(d.rounded())))
                            .font(.caption2.weight(.bold).monospacedDigit())
                            .padding(.horizontal, 6)
                            .padding(.vertical, 3)
                            .background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 5))
                            .foregroundStyle(.white)
                            .padding(8)
                    }
                }
                .aspectRatio(16 / 9, contentMode: .fit)
                .clipped()

                EventStrip(events: clip.events ?? [], duration: clip.duration ?? 0)

                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline) {
                        // The name is the point of the card: shrink it before
                        // truncating it, and let the clock give way first.
                        Text(clip.victim?.isEmpty == false ? clip.victim! : (clip.summary ?? clip.name))
                            .font(.headline)
                            .foregroundStyle(Theme.text)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                            .layoutPriority(1)
                        Spacer(minLength: 6)
                        if let c = clip.clock {
                            Text(c)
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(Theme.muted)
                                .fixedSize()          // never wraps or shrinks
                        }
                    }
                    if let chips = clip.chips, !chips.isEmpty {
                        ChipRow(chips: chips)
                    }
                    if let s = clip.summary, !s.isEmpty, clip.victim?.isEmpty == false {
                        Text(s)
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                            .lineLimit(2)
                    }
                }
                .padding(12)
            }
            .background(Theme.panel)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line))
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Plays the clip")
    }
}

/// Where in the clip each kill happened, like the web viewer's timeline.
struct EventStrip: View {
    let events: [ClipEvent]
    let duration: Double

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Theme.raised
                if duration > 0 {
                    ForEach(Array(events.enumerated()), id: \.offset) { _, e in
                        Capsule()
                            .fill(e.manual == true ? Theme.ok : Theme.danger)
                            .frame(width: 3)
                            .offset(x: max(0, min(geo.size.width - 3, geo.size.width * e.t / duration)))
                    }
                }
            }
        }
        .frame(height: 5)
        .accessibilityHidden(true)
    }
}

struct ChipRow: View {
    let chips: [Chip]

    var body: some View {
        HStack(spacing: 5) {
            ForEach(Array(chips.prefix(5).enumerated()), id: \.offset) { _, c in
                Text(c.label)
                    .font(.caption2.weight(.heavy))
                    .tracking(0.6)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .foregroundStyle(Theme.chip(c.role))
                    .background(Theme.chip(c.role).opacity(0.14), in: RoundedRectangle(cornerRadius: 5))
            }
            if chips.count > 5 {
                Text("+\(chips.count - 5)").font(.caption2).foregroundStyle(Theme.muted)
            }
        }
    }
}

struct ReportView: View {
    let text: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(text)
                    .font(.system(.callout, design: .monospaced))
                    .foregroundStyle(Theme.text)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(20)
            }
            .background(Theme.bg)
            .navigationTitle("WITNESS Report")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}
