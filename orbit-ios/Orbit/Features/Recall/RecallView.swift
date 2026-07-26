import SwiftUI

@MainActor
final class RecallViewModel: ObservableObject {
    @Published var people: [PersonSummary] = []
    @Published var profile: PersonProfile?
    @Published var catchup: CatchupResponse?
    @Published var relDescription = ""
    @Published var relCadence = ""
    @Published var errorMessage: String?
    @Published var isLoadingProfile = false

    func loadPeople() async {
        do {
            people = try await APIClient.shared.fetchPeople()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadProfile(id: UUID) async {
        isLoadingProfile = true
        defer { isLoadingProfile = false }
        do {
            profile = try await APIClient.shared.fetchProfile(id: id)
            catchup = nil
            relDescription = profile?.relationshipState?.description ?? ""
            relCadence = profile?.relationshipState?.desiredCadence ?? ""
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadCatchup(id: UUID) async {
        do {
            catchup = try await APIClient.shared.fetchCatchup(id: id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func saveRelationship() async {
        guard let profile else { return }
        do {
            _ = try await APIClient.shared.updateRelationshipState(
                personId: profile.id,
                body: RelationshipStateCreate(
                    description: relDescription.isEmpty ? nil : relDescription,
                    desiredCloseness: nil,
                    desiredCadence: relCadence.isEmpty ? nil : relCadence,
                    direction: nil
                )
            )
            await loadProfile(id: profile.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct RecallView: View {
    @StateObject private var viewModel = RecallViewModel()

    var body: some View {
        NavigationSplitView {
            peopleList
        } detail: {
            if let profile = viewModel.profile {
                profileDetail(profile)
            } else {
                ZStack {
                    Color.orbitCanvas.ignoresSafeArea()
                    OrbitEmptyState(
                        title: "Select someone",
                        message: "Pick a person to open their story.",
                        systemImage: "person.crop.circle"
                    )
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var peopleList: some View {
        List {
            if viewModel.people.isEmpty {
                OrbitEmptyState(
                    title: "No people yet",
                    message: "Capture and accept proposals to build your orbit.",
                    systemImage: "person.2"
                )
                .listRowBackground(Color.orbitCanvas)
                .listRowSeparator(.hidden)
            } else {
                ForEach(viewModel.people) { person in
                    Button {
                        Task { await viewModel.loadProfile(id: person.id) }
                    } label: {
                        HStack(spacing: 12) {
                            OrbitAvatar(name: person.name, orbit: person.orbit)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(person.name)
                                    .font(.orbitTitle(16))
                                    .foregroundStyle(Color.orbitInk)
                                if let orbit = person.orbit {
                                    Text(orbit.rawValue.uppercased())
                                        .font(.orbitCaps(10))
                                        .tracking(1.0)
                                        .foregroundStyle(orbitColor(orbit))
                                }
                            }
                            Spacer()
                            if viewModel.profile?.id == person.id {
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(Color.orbitAccent)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .listRowBackground(Color.orbitSurface)
                    .listRowSeparatorTint(Color.orbitDivider)
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Color.orbitCanvas)
        .navigationTitle("Recall")
        .toolbarBackground(Color.orbitCanvas, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await viewModel.loadPeople() }
        .refreshable { await viewModel.loadPeople() }
    }

    private func profileDetail(_ profile: PersonProfile) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                hero(profile)

                factsBlock(profile)

                if !profile.timeline.isEmpty {
                    timelineBlock(profile)
                }

                if !profile.openTasks.isEmpty {
                    threadsBlock(profile)
                }

                relationshipBlock

                catchupBlock(profile)
            }
            .padding(16)
        }
        .orbitScreen()
        .navigationTitle(profile.name)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func hero(_ profile: PersonProfile) -> some View {
        let progress = orbitProgress(profile.orbit)
        return HStack(alignment: .center, spacing: 16) {
            OrbitMetricRing(
                valueLabel: profile.orbit?.rawValue.prefix(1).uppercased() ?? "—",
                caption: "Orbit",
                progress: progress,
                tint: orbitColor(profile.orbit),
                diameter: 140
            )
            VStack(alignment: .leading, spacing: 10) {
                Text(profile.name)
                    .font(.orbitTitle(28))
                    .foregroundStyle(Color.orbitInk)
                if let orbit = profile.orbit {
                    OrbitStatusChip(label: orbit.rawValue, tone: orbitTone(orbit))
                }
                if let last = profile.lastInteractionAt {
                    Text("LAST \(last.formatted(date: .abbreviated, time: .omitted).uppercased())")
                        .font(.orbitCaps(10))
                        .tracking(1.0)
                        .foregroundStyle(Color.orbitMuted)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private func factsBlock(_ profile: PersonProfile) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitSectionLabel(text: "Where things stand")
            if profile.currentFacts.isEmpty {
                Text("No confirmed facts yet.")
                    .font(.orbitBody(14))
                    .foregroundStyle(Color.orbitMuted)
            } else {
                ForEach(profile.currentFacts) { fact in
                    OrbitCard {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(fact.category.uppercased())
                                .font(.orbitCaps(10))
                                .tracking(1.0)
                                .foregroundStyle(Color.orbitInfo)
                            Text(fact.value)
                                .font(.orbitTitle(17))
                                .foregroundStyle(Color.orbitInk)
                            if let prev = profile.pastFacts.first(where: { $0.category == fact.category }) {
                                Text("Previously \(prev.value)")
                                    .font(.orbitBody(12))
                                    .foregroundStyle(Color.orbitFaint)
                            }
                        }
                    }
                }
            }
        }
    }

    private func timelineBlock(_ profile: PersonProfile) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitSectionLabel(text: "Timeline")
            ForEach(profile.timeline) { ev in
                OrbitCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(ev.listTitle)
                            .font(.orbitTitle(16))
                            .foregroundStyle(Color.orbitInk)
                        HStack(spacing: 6) {
                            Text(ev.date.formatted(date: .abbreviated, time: .omitted))
                                .font(.orbitBody(12))
                                .foregroundStyle(Color.orbitMuted)
                            if let loc = ev.location, !loc.isEmpty {
                                Text("· \(loc)")
                                    .font(.orbitBody(12))
                                    .foregroundStyle(Color.orbitMuted)
                            }
                        }
                        if let summary = ev.summary, !summary.isEmpty, summary != ev.title {
                            DisclosureGroup {
                                Text(summary)
                                    .font(.orbitBody(13))
                                    .foregroundStyle(Color.orbitMuted)
                                    .padding(.top, 4)
                            } label: {
                                Text("DETAILS")
                                    .font(.orbitCaps(10))
                                    .tracking(1.0)
                                    .foregroundStyle(Color.orbitFaint)
                            }
                        }
                    }
                }
            }
        }
    }

    private func threadsBlock(_ profile: PersonProfile) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitSectionLabel(text: "Open threads")
            ForEach(profile.openTasks) { task in
                OrbitCard {
                    HStack(alignment: .top, spacing: 10) {
                        Circle()
                            .fill(Color.orbitStatusMid)
                            .frame(width: 8, height: 8)
                            .padding(.top, 6)
                        Text(task.description)
                            .font(.orbitBody(15))
                            .foregroundStyle(Color.orbitInkSoft)
                    }
                }
            }
        }
    }

    private var relationshipBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitSectionLabel(text: "Relationship")
            OrbitCard {
                VStack(alignment: .leading, spacing: 12) {
                    field(label: "How things feel", text: $viewModel.relDescription, axis: true)
                    field(label: "Desired cadence", text: $viewModel.relCadence, axis: false)
                    OrbitPrimaryButton(title: "Save state") {
                        Task { await viewModel.saveRelationship() }
                    }
                }
            }
        }
    }

    private func catchupBlock(_ profile: PersonProfile) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitSectionLabel(text: "Catch me up")
            OrbitPrimaryButton(title: "Generate briefing") {
                Task { await viewModel.loadCatchup(id: profile.id) }
            }
            if let catchup = viewModel.catchup {
                OrbitCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(catchup.summary)
                            .font(.orbitBody(15))
                            .foregroundStyle(Color.orbitInkSoft)
                        ForEach(catchup.talkingPoints, id: \.self) { point in
                            HStack(alignment: .top, spacing: 8) {
                                Text("→")
                                    .foregroundStyle(Color.orbitAccent)
                                Text(point)
                                    .font(.orbitBody(14))
                                    .foregroundStyle(Color.orbitMuted)
                            }
                        }
                    }
                }
            }
        }
    }

    private func field(label: String, text: Binding<String>, axis: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased())
                .font(.orbitCaps(10))
                .tracking(1.0)
                .foregroundStyle(Color.orbitMuted)
            Group {
                if axis {
                    TextField(label, text: text, axis: .vertical)
                        .lineLimit(2...4)
                } else {
                    TextField(label, text: text)
                }
            }
            .font(.orbitBody(15))
            .foregroundStyle(Color.orbitInk)
            .padding(12)
            .background(Color.orbitCanvas)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
    }

    private func orbitProgress(_ orbit: OrbitLevel?) -> Double {
        switch orbit {
        case .inner: return 1.0
        case .close: return 0.8
        case .active: return 0.55
        case .extended: return 0.35
        case .outer: return 0.18
        case .none: return 0.08
        }
    }

    private func orbitColor(_ orbit: OrbitLevel?) -> Color {
        switch orbit {
        case .inner: return .orbitStatusHigh
        case .close: return .orbitAccent
        case .active: return .orbitInfo
        case .extended: return .orbitStatusMid
        case .outer: return .orbitStatusLow
        case .none: return .orbitFaint
        }
    }

    private func orbitTone(_ orbit: OrbitLevel) -> OrbitStatusChip.Tone {
        switch orbit {
        case .inner, .close: return .high
        case .active: return .info
        case .extended: return .mid
        case .outer: return .low
        }
    }
}

private struct OrbitAvatar: View {
    let name: String
    let orbit: OrbitLevel?

    var body: some View {
        ZStack {
            Circle()
                .stroke(orbitStroke, lineWidth: 2)
                .frame(width: 40, height: 40)
            Text(initials)
                .font(.orbitCaps(12))
                .foregroundStyle(Color.orbitInkSoft)
        }
        .accessibilityHidden(true)
    }

    private var initials: String {
        let parts = name.split(separator: " ")
        let letters = parts.prefix(2).compactMap { $0.first.map(String.init) }
        return letters.joined().uppercased()
    }

    private var orbitStroke: Color {
        switch orbit {
        case .inner: return .orbitStatusHigh
        case .close: return .orbitAccent
        case .active: return .orbitInfo
        case .extended: return .orbitStatusMid
        case .outer: return .orbitStatusLow
        case .none: return .orbitFaint
        }
    }
}
