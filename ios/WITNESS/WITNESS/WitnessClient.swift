import Foundation

/// Talks to the WITNESS dashboard server on the PC. Plain HTTP on the LAN.
nonisolated struct WitnessClient: Sendable {
    let base: URL

    enum ClientError: LocalizedError {
        case notWitness
        case http(Int)
        case badAddress

        var errorDescription: String? {
            switch self {
            case .notWitness: return "That address answered, but it isn't WITNESS."
            case .http(let code): return "The PC answered with an error (HTTP \(code))."
            case .badAddress: return "That doesn't look like an address. Try 192.168.1.20:8000."
            }
        }
    }

    private static let session: URLSession = {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 6
        c.waitsForConnectivity = false
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: c)
    }()

    /// Accepts "192.168.1.20", "192.168.1.20:8000", "http://pc.local:8000".
    static func url(from typed: String) -> URL? {
        var s = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "http://" + s }
        guard var comps = URLComponents(string: s), let host = comps.host, !host.isEmpty else {
            return nil
        }
        if comps.port == nil { comps.port = 8000 }
        comps.path = ""
        comps.query = nil
        return comps.url
    }

    func absolute(_ pathAndQuery: String) -> URL {
        // pathAndQuery comes from the server already percent-encoded, e.g.
        // /api/play?s=...&f=002_down%2Bprecision.mkv
        URL(string: pathAndQuery, relativeTo: base)?.absoluteURL ?? base
    }

    func get<T: Decodable & Sendable>(_ path: String, as type: T.Type) async throws -> T {
        let (data, resp) = try await Self.session.data(from: absolute(path))
        if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw ClientError.http(http.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func post(_ path: String) async throws {
        var req = URLRequest(url: absolute(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        let (_, resp) = try await Self.session.data(for: req)
        if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw ClientError.http(http.statusCode)
        }
    }

    /// Proves the address really is WITNESS before trusting it.
    func info() async throws -> ServerInfo {
        let info = try await get("/api/info", as: ServerInfo.self)
        guard info.app == "witness" else { throw ClientError.notWitness }
        return info
    }
}
