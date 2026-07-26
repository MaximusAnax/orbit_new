import SwiftUI

@MainActor
final class DiscoverViewModel: ObservableObject {
    @Published var query = ""
    @Published var searchResults: [SearchResult] = []
    @Published var discoverResults: [DiscoverResult] = []
    @Published var maintenance: [MaintenanceSuggestion] = []
    @Published var mode: DiscoverMode = .discover
    @Published var selectedPersonId: UUID?
    @Published var isLoading = false
    @Published var errorMessage: String?

    enum DiscoverMode: String, CaseIterable {
        case discover = "Discover"
        case search = "Search"
        case reachOut = "Reach out"
    }

    func loadMaintenance() async {
        isLoading = true
        defer { isLoading = false }
        do {
            maintenance = try await APIClient.shared.fetchMaintenance()
        } catch {
            errorMessage = error.localizedDescription
            maintenance = []
        }
    }

    func runQuery() async {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            switch mode {
            case .discover:
                discoverResults = try await APIClient.shared.discover(query: query)
                searchResults = []
            case .search:
                searchResults = try await APIClient.shared.search(query: query)
                discoverResults = []
            case .reachOut:
                await loadMaintenance()
            }
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            searchResults = []
            discoverResults = []
        }
    }
}

struct DiscoverView: View {
    @StateObject private var viewModel = DiscoverViewModel()
    @FocusState private var searchFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                modePicker

                if viewModel.mode != .reachOut {
                    searchBar
                }

                if let err = viewModel.errorMessage {
                    Text(err)
                        .font(.orbitBody(13))
                        .foregroundStyle(Color.orbitStatusMid)
                        .padding(.horizontal, 4)
                }

                results
            }
            .padding(16)
        }
        .orbitScreen()
        .navigationTitle("Discover")
        .navigationBarTitleDisplayMode(.large)
        .task {
            if viewModel.mode == .reachOut {
                await viewModel.loadMaintenance()
            }
        }
    }

    private var modePicker: some View {
        HStack(spacing: 8) {
            ForEach(DiscoverViewModel.DiscoverMode.allCases, id: \.self) { mode in
                let selected = viewModel.mode == mode
                Button {
                    viewModel.mode = mode
                    if mode == .reachOut {
                        Task { await viewModel.loadMaintenance() }
                    }
                } label: {
                    Text(mode.rawValue.uppercased())
                        .font(.orbitCaps(11))
                        .tracking(0.8)
                        .foregroundStyle(selected ? Color.black : Color.orbitMuted)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(selected ? Color.orbitAccent : Color.orbitSurface)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
    }

    private var searchBar: some View {
        OrbitCard {
            VStack(alignment: .leading, spacing: 12) {
                OrbitSectionLabel(text: viewModel.mode == .discover ? "Ask Orbit" : "Search memory")
                HStack(spacing: 10) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(Color.orbitMuted)
                    TextField(
                        viewModel.mode == .discover ? "Who do I know at…" : "Name, place, interest…",
                        text: $viewModel.query
                    )
                    .font(.orbitBody(15))
                    .foregroundStyle(Color.orbitInk)
                    .focused($searchFocused)
                    .submitLabel(.search)
                    .onSubmit { Task { await viewModel.runQuery() } }
                }
                .padding(12)
                .background(Color.orbitCanvas)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(searchFocused ? Color.orbitAccent.opacity(0.5) : Color.orbitDivider, lineWidth: 1)
                )

                OrbitPrimaryButton(
                    title: "Go",
                    isEnabled: !viewModel.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !viewModel.isLoading
                ) {
                    Task { await viewModel.runQuery() }
                }
            }
        }
    }

    @ViewBuilder
    private var results: some View {
        if viewModel.isLoading {
            ProgressView()
                .tint(Color.orbitAccent)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 32)
                .accessibilityLabel("Loading")
        } else if viewModel.mode == .reachOut {
            reachOutResults
        } else if viewModel.discoverResults.isEmpty && viewModel.searchResults.isEmpty {
            OrbitEmptyState(
                title: "No matches yet",
                message: viewModel.query.isEmpty
                    ? "Ask who you know at a company, school, or place."
                    : "Try a broader query or switch modes.",
                systemImage: "sparkle.magnifyingglass"
            )
        } else {
            VStack(alignment: .leading, spacing: 10) {
                OrbitSectionLabel(text: "Matches")
                ForEach(viewModel.discoverResults) { result in
                    matchCard(
                        name: result.personName,
                        reason: result.matchReason,
                        path: result.path
                    )
                }
                ForEach(viewModel.searchResults) { result in
                    matchCard(
                        name: result.personName,
                        reason: result.matchReason,
                        path: []
                    )
                }
            }
        }
    }

    private var reachOutResults: some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitSectionLabel(text: "Who might you reach out to")
            if viewModel.maintenance.isEmpty {
                OrbitEmptyState(
                    title: "You're caught up",
                    message: "No one is overdue based on your orbit cadence.",
                    systemImage: "checkmark.seal"
                )
            } else {
                ForEach(viewModel.maintenance) { item in
                    let urgency = min(1.0, max(0.0, item.score))
                    OrbitCard {
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text(item.personName)
                                    .font(.orbitTitle(18))
                                    .foregroundStyle(Color.orbitInk)
                                Spacer()
                                Circle()
                                    .fill(Color.orbitStatus(urgency: urgency))
                                    .frame(width: 10, height: 10)
                                    .accessibilityLabel("Priority")
                            }
                            if let days = item.daysSinceContact {
                                Text("\(days) DAYS SINCE CONTACT · TARGET \(item.targetDays)D")
                                    .font(.orbitCaps(10))
                                    .tracking(0.8)
                                    .foregroundStyle(Color.orbitMuted)
                            }
                            ForEach(item.reasons, id: \.self) { reason in
                                Text(reason)
                                    .font(.orbitBody(13))
                                    .foregroundStyle(Color.orbitMuted)
                            }
                        }
                    }
                }
            }
        }
    }

    private func matchCard(name: String, reason: String, path: [String]) -> some View {
        OrbitCard {
            VStack(alignment: .leading, spacing: 8) {
                Text(name)
                    .font(.orbitTitle(17))
                    .foregroundStyle(Color.orbitInk)
                Text(reason)
                    .font(.orbitBody(13))
                    .foregroundStyle(Color.orbitMuted)
                if !path.isEmpty {
                    Text(path.joined(separator: " → ").uppercased())
                        .font(.orbitCaps(10))
                        .tracking(0.6)
                        .foregroundStyle(Color.orbitAccent)
                }
            }
        }
    }
}
