import Foundation

enum OrbitLevel: String, Codable {
    case inner, close, active, extended, outer
}

struct PersonSummary: Codable, Identifiable {
    let id: UUID
    let name: String
    let preferredName: String?
    let photoUrl: String?
    let orbit: OrbitLevel?
    let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, name, orbit
        case preferredName = "preferred_name"
        case photoUrl = "photo_url"
        case createdAt = "created_at"
    }
}

struct ProposedFact: Codable {
    let category: String
    let value: String
    let status: String
    let confidence: Double
    let educationLevel: String?

    enum CodingKeys: String, CodingKey {
        case category, value, status, confidence
        case educationLevel = "education_level"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        category = try c.decode(String.self, forKey: .category)
        value = try c.decode(String.self, forKey: .value)
        status = try c.decode(String.self, forKey: .status)
        confidence = try c.decode(Double.self, forKey: .confidence)
        educationLevel = try c.decodeIfPresent(String.self, forKey: .educationLevel)
    }

    var displayValue: String {
        guard category == "school", let level = educationLevel else { return value }
        let label: String
        switch level {
        case "high_school": label = "high school"
        case "undergrad": label = "undergraduate"
        case "grad": label = "graduate"
        case "phd": label = "PhD"
        case "alumni": label = "alumni"
        default: label = level.replacingOccurrences(of: "_", with: " ")
        }
        if value.lowercased().contains(label.lowercased()) { return value }
        return "\(value) (\(label))"
    }
}

struct Proposal: Codable, Identifiable {
    let id: UUID
    let eventId: UUID
    let personId: UUID
    let proposedFact: ProposedFact
    let decision: String

    enum CodingKeys: String, CodingKey {
        case id, decision
        case eventId = "event_id"
        case personId = "person_id"
        case proposedFact = "proposed_fact"
    }
}

struct EventAccepted: Codable {
    let id: UUID
    let status: String
}

struct AmbiguityFlag: Codable, Identifiable {
    let id: UUID
    let eventId: UUID
    let rawFragment: String
    let candidatePersonIds: [UUID]
    let resolvedPersonId: UUID?

    enum CodingKeys: String, CodingKey {
        case id
        case eventId = "event_id"
        case rawFragment = "raw_fragment"
        case candidatePersonIds = "candidate_person_ids"
        case resolvedPersonId = "resolved_person_id"
    }
}

struct OrbitEvent: Codable, Identifiable {
    let id: UUID
    let date: Date
    let location: String?
    let title: String?
    let rawTranscript: String?
    let summary: String?
    let source: String
    let status: String
    let proposalSummary: [String: Int]
    let ambiguityFlags: [AmbiguityFlag]

    enum CodingKeys: String, CodingKey {
        case id, date, location, title, summary, source, status
        case rawTranscript = "raw_transcript"
        case proposalSummary = "proposal_summary"
        case ambiguityFlags = "ambiguity_flags"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(UUID.self, forKey: .id)
        date = try c.decode(Date.self, forKey: .date)
        location = try c.decodeIfPresent(String.self, forKey: .location)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        rawTranscript = try c.decodeIfPresent(String.self, forKey: .rawTranscript)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        source = try c.decode(String.self, forKey: .source)
        status = try c.decode(String.self, forKey: .status)
        proposalSummary = try c.decodeIfPresent([String: Int].self, forKey: .proposalSummary) ?? [:]
        ambiguityFlags = try c.decodeIfPresent([AmbiguityFlag].self, forKey: .ambiguityFlags) ?? []
    }

    var listTitle: String {
        if let title, !title.isEmpty { return title }
        if let summary, !summary.isEmpty {
            return summary.count > 60 ? String(summary.prefix(57)) + "…" : summary
        }
        return "Captured event"
    }
}

struct Fact: Codable, Identifiable {
    let id: UUID
    let category: String
    let value: String
    let status: String
    let organizationName: String?

    enum CodingKeys: String, CodingKey {
        case id, category, value, status
        case organizationName = "organization_name"
    }
}

struct PastFact: Codable, Identifiable {
    let id: UUID
    let category: String
    let value: String
}

struct TimelineEvent: Codable, Identifiable {
    let id: UUID
    let date: Date
    let title: String?
    let summary: String?
    let location: String?

    var listTitle: String {
        if let title, !title.isEmpty { return title }
        if let summary, !summary.isEmpty {
            return summary.count > 60 ? String(summary.prefix(57)) + "…" : summary
        }
        return "Event"
    }
}

struct TaskItem: Codable, Identifiable {
    let id: UUID
    let personId: UUID
    let description: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case id, description, status
        case personId = "person_id"
    }
}

struct RelationshipState: Codable, Identifiable {
    let id: UUID
    let description: String?
    let desiredCloseness: String?
    let desiredCadence: String?
    let direction: String?
    let source: String

    enum CodingKeys: String, CodingKey {
        case id, description, source, direction
        case desiredCloseness = "desired_closeness"
        case desiredCadence = "desired_cadence"
    }
}

struct PersonProfile: Codable, Identifiable {
    let id: UUID
    let name: String
    let preferredName: String?
    let photoUrl: String?
    let orbit: OrbitLevel?
    let currentFacts: [Fact]
    let pastFacts: [PastFact]
    let timeline: [TimelineEvent]
    let openTasks: [TaskItem]
    let relationshipState: RelationshipState?
    let lastInteractionAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, name, orbit, timeline
        case preferredName = "preferred_name"
        case photoUrl = "photo_url"
        case currentFacts = "current_facts"
        case pastFacts = "past_facts"
        case openTasks = "open_tasks"
        case relationshipState = "relationship_state"
        case lastInteractionAt = "last_interaction_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(UUID.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        preferredName = try c.decodeIfPresent(String.self, forKey: .preferredName)
        photoUrl = try c.decodeIfPresent(String.self, forKey: .photoUrl)
        orbit = try c.decodeIfPresent(OrbitLevel.self, forKey: .orbit)
        currentFacts = try c.decodeIfPresent([Fact].self, forKey: .currentFacts) ?? []
        pastFacts = try c.decodeIfPresent([PastFact].self, forKey: .pastFacts) ?? []
        timeline = try c.decodeIfPresent([TimelineEvent].self, forKey: .timeline) ?? []
        openTasks = try c.decodeIfPresent([TaskItem].self, forKey: .openTasks) ?? []
        relationshipState = try c.decodeIfPresent(RelationshipState.self, forKey: .relationshipState)
        lastInteractionAt = try c.decodeIfPresent(Date.self, forKey: .lastInteractionAt)
    }
}

struct CatchupResponse: Codable {
    let summary: String
    let talkingPoints: [String]

    enum CodingKeys: String, CodingKey {
        case summary
        case talkingPoints = "talking_points"
    }
}

struct SearchResult: Codable, Identifiable {
    var id: UUID { personId }
    let personId: UUID
    let personName: String
    let matchReason: String

    enum CodingKeys: String, CodingKey {
        case personId = "person_id"
        case personName = "person_name"
        case matchReason = "match_reason"
    }
}

struct DiscoverResult: Codable, Identifiable {
    var id: UUID { personId }
    let personId: UUID
    let personName: String
    let matchReason: String
    let path: [String]

    enum CodingKeys: String, CodingKey {
        case personId = "person_id"
        case personName = "person_name"
        case matchReason = "match_reason"
        case path
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        personId = try c.decode(UUID.self, forKey: .personId)
        personName = try c.decode(String.self, forKey: .personName)
        matchReason = try c.decode(String.self, forKey: .matchReason)
        path = try c.decodeIfPresent([String].self, forKey: .path) ?? []
    }
}

struct MaintenanceSuggestion: Codable, Identifiable {
    var id: UUID { personId }
    let personId: UUID
    let personName: String
    let score: Double
    let targetDays: Int
    let daysSinceContact: Int?
    let reasons: [String]
    let reasonCodes: [String]

    enum CodingKeys: String, CodingKey {
        case personId = "person_id"
        case personName = "person_name"
        case score
        case targetDays = "target_days"
        case daysSinceContact = "days_since_contact"
        case reasons
        case reasonCodes = "reason_codes"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        personId = try c.decode(UUID.self, forKey: .personId)
        personName = try c.decode(String.self, forKey: .personName)
        score = try c.decode(Double.self, forKey: .score)
        targetDays = try c.decode(Int.self, forKey: .targetDays)
        daysSinceContact = try c.decodeIfPresent(Int.self, forKey: .daysSinceContact)
        reasons = try c.decodeIfPresent([String].self, forKey: .reasons) ?? []
        reasonCodes = try c.decodeIfPresent([String].self, forKey: .reasonCodes) ?? []
    }
}

struct RelationshipStateCreate: Encodable {
    let description: String?
    let desiredCloseness: String?
    let desiredCadence: String?
    let direction: String?

    enum CodingKeys: String, CodingKey {
        case description, direction
        case desiredCloseness = "desired_closeness"
        case desiredCadence = "desired_cadence"
    }
}
