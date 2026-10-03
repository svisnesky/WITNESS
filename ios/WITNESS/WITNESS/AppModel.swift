import Foundation
import Observation
import UIKit
import UserNotifications

/// Everything the app knows about the PC it's connected to.
@Observable
final class AppModel {
    private(set) var client: WitnessClient?
    private(set) var serverName = ""
    private(set) var serverVersion = ""
    private(set) var status: Status?
    private(set) var sessions: [SessionSummary] = []
    private(set) var sessionsLoaded = false
    private(set) var recordDirKnown = true
    /// False after two polls in a row fail. The UI dims the controls and says
    /// so, instead of pretending the last status is current.
    private(set) var reachable = true
    private(set) var connecting = false
    var connectError: String?
    /// One-line result of the last button press ("Clip saved"), cleared shortly after.
    private(set) var toast: String?
    /// Keep the iPad awake while a session is recording (it sits on a stand).
    var keepAwake: Bool = UserDefaults.standard.object(forKey: Keys.keepAwake) as? Bool ?? true {
        didSet {
            UserDefaults.standard.set(keepAwake, forKey: Keys.keepAwake)
            applyIdleTimer()
        }
    }

    private var pollTask: Task<Void, Never>?
    private var misses = 0
    /// key + since of every alert already announced, so a refresh of the
    /// same alert never notifies twice (the PC keeps `since` stable).
    private var announced = Set<String>()
    private var toastTask: Task<Void, Never>?
    private var lastSessionRefresh = Date.distantPast

    private enum Keys {
        static let host = "witness.host"
        static let keepAwake = "witness.keepAwake"
    }

    var savedHost: URL? {
        UserDefaults.standard.string(forKey: Keys.host).flatMap(URL.init(string:))
    }

    var activeAlerts: [WitnessAlert] { status?.activeAlerts ?? [] }
    var isRunning: Bool { status?.running ?? false }

    // MARK: Connecting

    /// Proves the address is WITNESS (/api/info) before keeping it.
    @discardableResult
    func connect(to url: URL) async -> Bool {
        connecting = true
        connectError = nil
        defer { connecting = false }
        let c = WitnessClient(base: url)
        do {
            let info = try await c.info()
            client = c
            serverName = (info.name?.isEmpty == false ? info.name! : url.host()) ?? "PC"
            serverVersion = info.version ?? ""
            reachable = true
            misses = 0
            UserDefaults.standard.set(url.absoluteString, forKey: Keys.host)
            Notifier.shared.requestPermission()
            startPolling()
            await refreshSessions()
            return true
        } catch {
            connectError = Self.describe(error, url: url)
            return false
        }
    }

    func disconnect(forget: Bool) {
        stopPolling()
        client = nil
        status = nil
        sessions = []
        sessionsLoaded = false
        announced = []
        if forget { UserDefaults.standard.removeObject(forKey: Keys.host) }
        applyIdleTimer()
    }

    static func describe(_ error: Error, url: URL) -> String {
        if let e = error as? WitnessClient.ClientError { return e.errorDescription ?? "\(e)" }
        let ns = error as NSError
        if ns.domain == NSURLErrorDomain {
            switch ns.code {
            case NSURLErrorTimedOut, NSURLErrorCannotConnectToHost, NSURLErrorNetworkConnectionLost:
                return "Nothing answered at \(url.host() ?? "that address"):\(url.port ?? 8000). "
                    + "Is WITNESS running on the PC, with web_lan: true?"
            case NSURLErrorNotConnectedToInternet:
                return "This iPad isn't on Wi-Fi."
            default:
                break
            }
        }
        if error is DecodingError { return "That address answered, but it isn't WITNESS." }
        return error.localizedDescription
    }

    // MARK: Polling

    func startPolling() {
        stopPolling()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pollOnce()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    private func pollOnce() async {
        guard let client else { return }
        do {
            let s = try await client.get("/api/status", as: Status.self)
            let wasRunning = status?.running ?? false
            status = s
            misses = 0
            reachable = true
            announce(s.activeAlerts)
            applyIdleTimer()
            // A session just ended, or it's been a while: new reels may exist.
            if (wasRunning && !s.running) || Date().timeIntervalSince(lastSessionRefresh) > 30 {
                await refreshSessions()
            }
        } catch {
            misses += 1
            if misses >= 2 { reachable = false }
        }
    }

    func refreshSessions() async {
        guard let client else { return }
        lastSessionRefresh = Date()
        if let list = try? await client.get("/api/sessions", as: SessionList.self) {
            sessions = list.sessions
            recordDirKnown = list.record_dir_known ?? true
            sessionsLoaded = true
        }
    }

    func loadSession(_ id: String) async throws -> SessionDetail {
        guard let client else { throw WitnessClient.ClientError.badAddress }
        let q = id.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? id
        return try await client.get("/api/session?s=\(q)", as: SessionDetail.self)
    }

    func mediaURL(_ path: String) -> URL? { client?.absolute(path) }

    // MARK: Alerts

    private func announce(_ alerts: [WitnessAlert]) {
        let live = Set(alerts.map { "\($0.key)@\($0.since ?? 0)" })
        for a in alerts where !announced.contains("\(a.key)@\(a.since ?? 0)") {
            Notifier.shared.post(a)
            UINotificationFeedbackGenerator().notificationOccurred(a.isCritical ? .error : .warning)
        }
        // Forget cleared alerts, so the same problem coming back later notifies again.
        announced = live
    }

    // MARK: Controls

    enum Action: String {
        case clip = "/clip", addKill = "/addkill", start = "/start", stop = "/stop"

        var done: String {
            switch self {
            case .clip: return "Clip requested"
            case .addKill: return "Kill added"
            case .start: return "Starting session…"
            case .stop: return "Stopping — reels will build on the PC"
            }
        }
    }

    func perform(_ action: Action) async {
        guard let client else { return }
        do {
            try await client.post(action.rawValue)
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            show(action.done)
            await pollOnce()
        } catch WitnessClient.ClientError.refused(let why) {
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
            show(why == "not running" ? "Not recording - press START first" : "The PC said no: \(why)")
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            show("The PC didn't respond")
        }
    }

    private func show(_ message: String) {
        toast = message
        toastTask?.cancel()
        toastTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(2.5))
            if !Task.isCancelled { self?.toast = nil }
        }
    }

    private func applyIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = keepAwake && client != nil && isRunning
    }
}

/// Local notifications for PC alerts. They appear as a banner even while the
/// app is open (the usual case: the iPad on a stand next to the monitor).
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    static let shared = Notifier()

    func install() {
        UNUserNotificationCenter.current().delegate = self
    }

    func requestPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func post(_ alert: WitnessAlert) {
        let content = UNMutableNotificationContent()
        content.title = alert.title
        if let m = alert.message, !m.isEmpty { content.body = m }
        // .defaultCritical needs Apple's Critical Alerts entitlement, which a
        // free Personal Team can't get. Time Sensitive degrades to a normal
        // banner without its capability, so it's safe to ask for.
        content.sound = .default
        content.interruptionLevel = alert.isCritical ? .timeSensitive : .active
        let req = UNNotificationRequest(identifier: "witness.\(alert.key)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }
}
