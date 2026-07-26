import SwiftUI

@MainActor
final class ReviewViewModel: ObservableObject {
    @Published var events: [OrbitEvent] = []
    @Published var proposals: [Proposal] = []
    @Published var peopleById: [UUID: String] = [:]
    @Published var selectedEvent: OrbitEvent?
    @Published var editingProposalId: UUID?
    @Published var editValue: String = ""
    @Published var errorMessage: String?
    @Published var isLoading = false

    var proposalsByPerson: [(personId: UUID, name: String, proposals: [Proposal])] {
        let grouped = Dictionary(grouping: proposals) { $0.personId }
        return grouped.map { key, value in
            (key, peopleById[key] ?? "Person", value)
        }
        .sorted { $0.name < $1.name }
    }

    var pendingEventCount: Int {
        events.filter { ($0.proposalSummary["pending"] ?? 0) > 0 }.count
    }

    func loadEvents() async {
        isLoading = true
        defer { isLoading = false }
        do {
            events = try await APIClient.shared.fetchEvents()
            let people = try await APIClient.shared.fetchPeople()
            peopleById = Dictionary(uniqueKeysWithValues: people.map { ($0.id, $0.name) })
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadProposals(for event: OrbitEvent) async {
        selectedEvent = event
        do {
            proposals = try await APIClient.shared.fetchProposals(eventId: event.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func accept(_ proposal: Proposal, value: String? = nil) async {
        _ = try? await APIClient.shared.acceptProposal(id: proposal.id, value: value)
        if let event = selectedEvent { await loadProposals(for: event) }
        await loadEvents()
    }

    func acceptAll() async {
        for p in proposals where p.decision == "pending" || p.decision == "deferred" {
            _ = try? await APIClient.shared.acceptProposal(id: p.id)
        }
        if let event = selectedEvent { await loadProposals(for: event) }
        await loadEvents()
    }

    func reject(_ proposal: Proposal) async {
        _ = try? await APIClient.shared.rejectProposal(id: proposal.id)
        if let event = selectedEvent { await loadProposals(for: event) }
    }

    func deferProposal(_ proposal: Proposal) async {
        _ = try? await APIClient.shared.deferProposal(id: proposal.id)
        if let event = selectedEvent { await loadProposals(for: event) }
    }

    func resolveFlag(_ flag: AmbiguityFlag, personId: UUID) async {
        _ = try? await APIClient.shared.resolveAmbiguity(flagId: flag.id, personId: personId)
        await loadEvents()
        if let event = events.first(where: { $0.id == flag.eventId }) {
            await loadProposals(for: event)
        }
    }
}

struct ReviewView: View {
    @StateObject private var viewModel = ReviewViewModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                summaryHeader

                if viewModel.events.isEmpty && !viewModel.isLoading {
                    OrbitEmptyState(
                        title: "Nothing to review",
                        message: "Capture a moment and proposals will land here.",
                        systemImage: "checklist"
                    )
                } else {
                    eventsSection
                }

                if let event = viewModel.selectedEvent {
                    ambiguitySection(for: event)
                    proposalsSection
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .orbitScreen()
        .navigationTitle("Review")
        .navigationBarTitleDisplayMode(.large)
        .task { await viewModel.loadEvents() }
        .refreshable { await viewModel.loadEvents() }
    }

    private var summaryHeader: some View {
        HStack(spacing: 12) {
            OrbitCard {
                VStack(alignment: .leading, spacing: 6) {
                    Text("QUEUE")
                        .font(.orbitCaps(10))
                        .tracking(1.2)
                        .foregroundStyle(Color.orbitMuted)
                    Text("\(viewModel.pendingEventCount)")
                        .font(.orbitHero(40))
                        .foregroundStyle(
                            viewModel.pendingEventCount == 0
                                ? Color.orbitStatusHigh
                                : Color.orbitStatus(urgency: min(1, Double(viewModel.pendingEventCount) / 8.0))
                        )
                    Text("events with pending")
                        .font(.orbitBody(12))
                        .foregroundStyle(Color.orbitFaint)
                }
            }
            OrbitCard {
                VStack(alignment: .leading, spacing: 6) {
                    Text("SELECTED")
                        .font(.orbitCaps(10))
                        .tracking(1.2)
                        .foregroundStyle(Color.orbitMuted)
                    Text(viewModel.selectedEvent.map { _ in "1" } ?? "—")
                        .font(.orbitHero(40))
                        .foregroundStyle(Color.orbitInk)
                    Text(viewModel.selectedEvent?.listTitle ?? "Tap an event")
                        .font(.orbitBody(12))
                        .foregroundStyle(Color.orbitFaint)
                        .lineLimit(2)
                }
            }
        }
    }

    private var eventsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitSectionLabel(text: "Events")
            ForEach(viewModel.events) { event in
                eventRow(event)
            }
        }
    }

    private func eventRow(_ event: OrbitEvent) -> some View {
        let pending = event.proposalSummary["pending"] ?? 0
        let unresolved = event.ambiguityFlags.filter { $0.resolvedPersonId == nil }.count
        let selected = viewModel.selectedEvent?.id == event.id

        return Button {
            Task { await viewModel.loadProposals(for: event) }
        } label: {
            OrbitCard {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top) {
                        Text(event.listTitle)
                            .font(.orbitTitle(17))
                            .foregroundStyle(Color.orbitInk)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: 8)
                        if selected {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(Color.orbitAccent)
                                .accessibilityLabel("Selected")
                        }
                    }

                    HStack(spacing: 8) {
                        Text(event.date.formatted(date: .abbreviated, time: .omitted))
                            .font(.orbitBody(12))
                            .foregroundStyle(Color.orbitMuted)
                        if let loc = event.location, !loc.isEmpty {
                            Text("· \(loc)")
                                .font(.orbitBody(12))
                                .foregroundStyle(Color.orbitMuted)
                        }
                    }

                    HStack(spacing: 8) {
                        if pending > 0 {
                            OrbitStatusChip(label: "\(pending) proposals", tone: .mid)
                        } else {
                            OrbitStatusChip(label: "Clear", tone: .high)
                        }
                        if unresolved > 0 {
                            OrbitStatusChip(label: "\(unresolved) ambiguous", tone: .low)
                        }
                    }

                    if let summary = event.summary, !summary.isEmpty, summary != event.title {
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
            .overlay(
                RoundedRectangle(cornerRadius: OrbitTheme.cornerRadius, style: .continuous)
                    .stroke(selected ? Color.orbitAccent.opacity(0.55) : Color.clear, lineWidth: 1.5)
            )
        }
        .buttonStyle(.plain)
        .accessibilityHint("Shows proposals for this event")
    }

    @ViewBuilder
    private func ambiguitySection(for event: OrbitEvent) -> some View {
        let open = event.ambiguityFlags.filter { $0.resolvedPersonId == nil }
        if !open.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                OrbitSectionLabel(text: "Ambiguity")
                ForEach(open) { flag in
                    OrbitCard {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(flag.rawFragment)
                                .font(.orbitBody(15))
                                .foregroundStyle(Color.orbitInk)
                            Text("WHO WAS THIS ABOUT?")
                                .font(.orbitCaps(10))
                                .tracking(1.0)
                                .foregroundStyle(Color.orbitMuted)
                            FlowPeople(people: viewModel.peopleById) { pid, name in
                                OrbitGhostButton(title: name) {
                                    Task { await viewModel.resolveFlag(flag, personId: pid) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var proposalsSection: some View {
        if !viewModel.proposals.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    OrbitSectionLabel(text: "Proposals")
                    Spacer()
                    OrbitGhostButton(title: "Accept all") {
                        Task { await viewModel.acceptAll() }
                    }
                }

                ForEach(viewModel.proposalsByPerson, id: \.personId) { group in
                    Text(group.name.uppercased())
                        .font(.orbitCaps(11))
                        .tracking(1.2)
                        .foregroundStyle(Color.orbitAccent)
                        .padding(.top, 4)

                    ForEach(group.proposals) { proposal in
                        proposalCard(proposal)
                    }
                }
            }
        } else if viewModel.selectedEvent != nil {
            OrbitEmptyState(
                title: "No proposals yet",
                message: "Pipeline may still be running — pull to refresh.",
                systemImage: "hourglass"
            )
        }
    }

    private func proposalCard(_ proposal: Proposal) -> some View {
        let actionable = proposal.decision == "pending" || proposal.decision == "deferred"
        return OrbitCard {
            VStack(alignment: .leading, spacing: 12) {
                Text(proposal.proposedFact.displayValue)
                    .font(.orbitTitle(18))
                    .foregroundStyle(Color.orbitInk)
                HStack(spacing: 8) {
                    OrbitStatusChip(label: proposal.proposedFact.category, tone: .info)
                    OrbitStatusChip(
                        label: proposal.decision,
                        tone: proposal.decision == "accepted" ? .high
                            : proposal.decision == "rejected" ? .low
                            : .mid
                    )
                }

                if actionable {
                    if viewModel.editingProposalId == proposal.id {
                        TextField("Edit value", text: $viewModel.editValue)
                            .font(.orbitBody(15))
                            .padding(10)
                            .background(Color.orbitCanvas)
                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        OrbitPrimaryButton(title: "Save & accept") {
                            Task {
                                await viewModel.accept(proposal, value: viewModel.editValue)
                                viewModel.editingProposalId = nil
                            }
                        }
                    } else {
                        HStack(spacing: 8) {
                            OrbitGhostButton(title: "Accept") {
                                Task { await viewModel.accept(proposal) }
                            }
                            OrbitGhostButton(title: "Edit") {
                                viewModel.editingProposalId = proposal.id
                                viewModel.editValue = proposal.proposedFact.displayValue
                            }
                            OrbitGhostButton(title: "Reject", destructive: true) {
                                Task { await viewModel.reject(proposal) }
                            }
                            OrbitGhostButton(title: "Defer") {
                                Task { await viewModel.deferProposal(proposal) }
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct FlowPeople: View {
    let people: [UUID: String]
    let onSelect: (UUID, String) -> Void

    var body: some View {
        let pairs = people.sorted { $0.value < $1.value }
        VStack(alignment: .leading, spacing: 8) {
            ForEach(pairs, id: \.key) { pid, name in
                Button {
                    onSelect(pid, name)
                } label: {
                    Text(name.uppercased())
                        .font(.orbitCaps(11))
                        .tracking(0.8)
                        .foregroundStyle(Color.orbitInkSoft)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.orbitSurfaceRaised)
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(name)
            }
        }
    }
}
