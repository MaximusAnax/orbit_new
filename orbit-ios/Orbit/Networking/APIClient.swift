import Foundation

enum APIError: Error, LocalizedError {
    case invalidURL
    case httpError(Int)
    case decodingError

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid URL"
        case .httpError(let code): return "HTTP \(code)"
        case .decodingError: return "Failed to decode response"
        }
    }
}

@MainActor
final class APIClient: ObservableObject {
    static let shared = APIClient()

    var baseURL: URL {
        if let env = ProcessInfo.processInfo.environment["API_BASE_URL"],
           let url = URL(string: env) {
            return url
        }
        return URL(string: "http://127.0.0.1:8000")!
    }

    var authToken: String?

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()

    private let encoder = JSONEncoder()

    func makeURL(path: String, query: [URLQueryItem] = []) throws -> URL {
        let trimmed = path.hasPrefix("/") ? String(path.dropFirst()) : path
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }
        let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let segments = [basePath, "api/v1", trimmed]
            .flatMap { $0.split(separator: "/").map(String.init) }
            .filter { !$0.isEmpty }
        components.path = "/" + segments.joined(separator: "/")
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw APIError.invalidURL }
        return url
    }

    func request<T: Decodable>(
        _ path: String,
        method: String = "GET",
        query: [URLQueryItem] = [],
        body: Encodable? = nil
    ) async throws -> T {
        let url = try makeURL(path: path, query: query)

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpBody = try encoder.encode(AnyEncodable(body))
        }

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.httpError(0) }
        guard (200...299).contains(http.statusCode) else { throw APIError.httpError(http.statusCode) }

        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decodingError
        }
    }

    func uploadEvent(text: String) async throws -> EventAccepted {
        try await request("/events/text", method: "POST", body: TextEventBody(text: text))
    }

    func uploadEvent(audioURL: URL) async throws -> EventAccepted {
        let url = try makeURL(path: "/events")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let audioData = try Data(contentsOf: audioURL)
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"audio\"; filename=\"recording.m4a\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/m4a\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 202 else {
            throw APIError.httpError((response as? HTTPURLResponse)?.statusCode ?? 0)
        }
        return try decoder.decode(EventAccepted.self, from: data)
    }

    func fetchPeople() async throws -> [PersonSummary] {
        try await request("/people")
    }

    func fetchProfile(id: UUID) async throws -> PersonProfile {
        try await request("/people/\(id.uuidString)")
    }

    func fetchCatchup(id: UUID) async throws -> CatchupResponse {
        try await request("/people/\(id.uuidString)/catchup")
    }

    func fetchEvents() async throws -> [OrbitEvent] {
        try await request("/events")
    }

    func fetchProposals(eventId: UUID) async throws -> [Proposal] {
        try await request(
            "/proposals",
            query: [URLQueryItem(name: "event_id", value: eventId.uuidString)]
        )
    }

    func acceptProposal(id: UUID, value: String? = nil) async throws -> Proposal {
        if let value {
            return try await request("/proposals/\(id.uuidString)/accept", method: "POST", body: AcceptBody(value: value))
        }
        return try await request("/proposals/\(id.uuidString)/accept", method: "POST")
    }

    func rejectProposal(id: UUID) async throws -> Proposal {
        try await request("/proposals/\(id.uuidString)/reject", method: "POST")
    }

    func deferProposal(id: UUID) async throws -> Proposal {
        try await request("/proposals/\(id.uuidString)/defer", method: "POST")
    }

    func search(query: String) async throws -> [SearchResult] {
        try await request("/search", query: [URLQueryItem(name: "q", value: query)])
    }

    func discover(query: String) async throws -> [DiscoverResult] {
        try await request("/discover", query: [URLQueryItem(name: "q", value: query)])
    }

    func fetchMaintenance(limit: Int = 10) async throws -> [MaintenanceSuggestion] {
        try await request("/maintenance", query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    func resolveAmbiguity(flagId: UUID, personId: UUID) async throws -> AmbiguityFlag {
        try await request(
            "/ambiguity-flags/\(flagId.uuidString)/resolve",
            method: "POST",
            body: AmbiguityResolveBody(personId: personId)
        )
    }

    func updateRelationshipState(personId: UUID, body: RelationshipStateCreate) async throws -> RelationshipState {
        try await request(
            "/people/\(personId.uuidString)/relationship-state",
            method: "POST",
            body: body
        )
    }
}

private struct TextEventBody: Encodable {
    let text: String
}

private struct AcceptBody: Encodable {
    let value: String
}

private struct AmbiguityResolveBody: Encodable {
    let personId: UUID
    enum CodingKeys: String, CodingKey { case personId = "person_id" }
}

private struct AnyEncodable: Encodable {
    private let encode: (Encoder) throws -> Void
    init(_ wrapped: Encodable) {
        encode = wrapped.encode
    }
    func encode(to encoder: Encoder) throws { try encode(encoder) }
}
