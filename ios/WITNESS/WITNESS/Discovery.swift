import Foundation
import Network
import Observation

/// Browses the Wi-Fi for WITNESS PCs. The PC side is discovery.py, which
/// advertises `_witness._tcp` with its LAN ip and port in the TXT record.
@Observable
final class PCBrowser {
    struct Found: Identifiable, Hashable {
        let id: String          // the service instance name, e.g. "WITNESS on GAMING-PC"
        let name: String
        let url: URL?           // straight from the TXT record when present
        let version: String
        let endpoint: NWEndpoint
    }

    enum State: Equatable {
        case idle
        case browsing
        /// The user said no to Local Network access (or hasn't been asked yet
        /// on this install). Browsing can't succeed until they allow it.
        case denied
        case failed(String)
    }

    private(set) var found: [Found] = []
    private(set) var state: State = .idle
    private var browser: NWBrowser?

    func start() {
        stop()
        let params = NWParameters()
        params.includePeerToPeer = false
        let b = NWBrowser(for: .bonjourWithTXTRecord(type: "_witness._tcp", domain: nil),
                          using: params)
        // Delivered on .main, so it's safe to touch MainActor state directly.
        b.stateUpdateHandler = { [weak self] st in
            MainActor.assumeIsolated { self?.handle(st) }
        }
        b.browseResultsChangedHandler = { [weak self] results, _ in
            MainActor.assumeIsolated { self?.update(results) }
        }
        state = .browsing
        b.start(queue: .main)
        browser = b
    }

    func stop() {
        browser?.cancel()
        browser = nil
        state = .idle
    }

    private func handle(_ st: NWBrowser.State) {
        switch st {
        case .ready, .setup:
            state = .browsing
        case .waiting(let err), .failed(let err):
            // kDNSServiceErr_PolicyDenied: Local Network permission is off.
            if case .dns(let code) = err, code == -65570 {
                state = .denied
            } else if case .failed = st {
                state = .failed(err.localizedDescription)
            }
        case .cancelled:
            break
        @unknown default:
            break
        }
    }

    private func update(_ results: Set<NWBrowser.Result>) {
        found = results.compactMap(Self.found(from:)).sorted { $0.name < $1.name }
    }

    private static func found(from r: NWBrowser.Result) -> Found? {
        guard case .service(let name, _, _, _) = r.endpoint else { return nil }
        var txt: [String: String] = [:]
        if case .bonjour(let record) = r.metadata {
            txt = record.dictionary
        }
        var url: URL?
        if let ip = txt["ip"], let port = txt["port"], !ip.isEmpty {
            url = URL(string: "http://\(ip):\(port)")
        }
        return Found(id: name, name: name, url: url, version: txt["v"] ?? "",
                     endpoint: r.endpoint)
    }

    /// When a TXT record has no address (an older PC build), resolve the
    /// service the slow way: open a connection and read where it landed.
    /// IPv4 only, because a link-local IPv6 "%en0" address makes a URL that
    /// URLSession rejects.
    nonisolated static func resolve(_ endpoint: NWEndpoint) async -> URL? {
        let params = NWParameters.tcp
        if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
            ip.version = .v4
        }
        let conn = NWConnection(to: endpoint, using: params)
        return await withCheckedContinuation { (cont: CheckedContinuation<URL?, Never>) in
            let once = Once()
            conn.stateUpdateHandler = { st in
                switch st {
                case .ready:
                    var url: URL?
                    if case .hostPort(let host, let port) = conn.currentPath?.remoteEndpoint {
                        var h = "\(host)"
                        if let pct = h.firstIndex(of: "%") { h = String(h[..<pct]) }
                        url = URL(string: "http://\(h):\(port.rawValue)")
                    }
                    conn.cancel()
                    if once.claim() { cont.resume(returning: url) }
                case .failed, .cancelled:
                    if once.claim() { cont.resume(returning: nil) }
                default:
                    break
                }
            }
            conn.start(queue: .global())
            DispatchQueue.global().asyncAfter(deadline: .now() + 5) {
                conn.cancel()
                if once.claim() { cont.resume(returning: nil) }
            }
        }
    }
}

/// Resumes a continuation exactly once across racing callbacks.
nonisolated final class Once: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false
    func claim() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if done { return false }
        done = true
        return true
    }
}
