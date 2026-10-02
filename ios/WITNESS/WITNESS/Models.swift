import Foundation

// Shapes of the JSON the WITNESS PC app serves (webserver.py, API_VERSION = 1).
// Every field is optional-tolerant: the PC and the app update independently, and
// a field the PC doesn't send yet must never make the whole response unreadable.

nonisolated struct ServerInfo: Decodable, Sendable {
    let app: String
    let api: Int
    let version: String?
    let name: String?
}

nonisolated struct WitnessAlert: Decodable, Sendable, Identifiable, Equatable {
    let key: String
    let level: String
    let title: String
    let message: String?
    let since: Double?
    var id: String { key }
    var isCritical: Bool { level == "critical" }
}

nonisolated struct Heat: Decodable, Sendable {
    let streak: Int?
    let label: String?
    let color: String?
}

nonisolated struct Recap: Decodable, Sendable {
    let status: String?
    let note: String?
}

nonisolated struct Status: Decodable, Sendable {
    let running: Bool
    let count: Int
    let elapsed: Int?
    let version: String?
    let update: String?
    let heat: Heat?
    let recap: Recap?
    let alerts: [WitnessAlert]?

    var activeAlerts: [WitnessAlert] { alerts ?? [] }
}

nonisolated struct Media: Decodable, Sendable, Identifiable, Hashable {
    let label: String
    let rel: String
    let kind: String
    let url: String
    var id: String { rel }

    /// Order the reels row: the best-of first, then the whole night, then matches.
    var sortKey: Int {
        switch kind {
        case "potn": return 0
        case "session_reel": return 1
        case "montage": return 2
        case "reel": return 3
        case "announced": return 4
        case "short": return 5
        case "replay": return 6
        default: return 9
        }
    }
    var isVideo: Bool { kind != "report_audio" }
}

nonisolated struct SessionSummary: Decodable, Sendable, Identifiable, Hashable {
    let id: String
    let clips: Int
    let size: Int?
    let size_label: String?
    let failed: Bool?
    let recaps: [String]?
    let has_report: Bool?
    let media: [Media]

    /// "2026-08-08_21-35-33" -> "Aug 8, 9:35 PM"
    var title: String { SessionDate.pretty(id) }
}

nonisolated struct SessionList: Decodable, Sendable {
    let record_dir_known: Bool?
    let sessions: [SessionSummary]
}

nonisolated struct Chip: Decodable, Sendable, Hashable {
    let label: String
    let role: String
}

nonisolated struct ClipEvent: Decodable, Sendable, Hashable {
    let t: Double
    let manual: Bool?
    let label: String
}

nonisolated struct Clip: Decodable, Sendable, Identifiable, Hashable {
    let rel: String
    let name: String
    let tags: [String]?
    let chips: [Chip]?
    let clock: String?
    let victim: String?
    let summary: String?
    let duration: Double?
    let events: [ClipEvent]?
    let downs: Int?
    let human: Bool?
    let url: String
    let thumb: String
    var id: String { rel }

    /// Where playback should start: a few seconds before the first kill, so the
    /// approach is visible but nobody has to scrub through a 30s buffer.
    var startAt: Double {
        guard let first = events?.first?.t else { return 0 }
        return max(0, first - 5)
    }
}

nonisolated struct SessionDetail: Decodable, Sendable {
    let session: String?
    let count: Int?
    let clips: [Clip]?
    let media: [Media]?
    let report: String?
    let error: String?
}

nonisolated enum SessionDate {
    static func pretty(_ id: String) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        guard let d = f.date(from: id) else { return id }
        let out = DateFormatter()
        out.dateFormat = "EEE MMM d, h:mm a"
        return out.string(from: d)
    }
}
