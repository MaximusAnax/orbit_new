import AVFoundation
import SwiftUI

@MainActor
final class CaptureViewModel: ObservableObject {
    @Published var isRecording = false
    @Published var elapsedSeconds = 0
    @Published var lastEventId: UUID?
    @Published var errorMessage: String?
    @Published var pendingReviewCount = 0
    @Published var pendingProposalCount = 0
    @Published var isSubmitting = false

    private var recorder: AVAudioRecorder?
    private var recordingURL: URL?
    private var timer: Timer?

    func toggleRecording() {
        if isRecording {
            stopRecording()
        } else {
            startRecording()
        }
    }

    private func startRecording() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .default)
        try? session.setActive(true)

        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44100,
            AVNumberOfChannelsKey: 1,
        ]
        recorder = try? AVAudioRecorder(url: url, settings: settings)
        recorder?.record()
        recordingURL = url
        isRecording = true
        elapsedSeconds = 0
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.elapsedSeconds += 1
            }
        }
    }

    private func stopRecording() {
        timer?.invalidate()
        timer = nil
        recorder?.stop()
        isRecording = false
        guard let url = recordingURL else { return }

        Task {
            isSubmitting = true
            defer { isSubmitting = false }
            do {
                let accepted = try await APIClient.shared.uploadEvent(audioURL: url)
                lastEventId = accepted.id
                await refreshPendingCount()
            } catch {
                UploadQueue.shared.enqueue(localFileURL: url)
                errorMessage = "Saved offline — will upload when connected."
            }
        }
    }

    func submitText(_ text: String) async {
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            let accepted = try await APIClient.shared.uploadEvent(text: text)
            lastEventId = accepted.id
            await refreshPendingCount()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshPendingCount() async {
        do {
            let events = try await APIClient.shared.fetchEvents()
            pendingReviewCount = events.filter { $0.status == "captured" }.count
            pendingProposalCount = events.reduce(0) { $0 + ($1.proposalSummary["pending"] ?? 0) }
            await UploadQueue.shared.processQueue()
        } catch {
            pendingReviewCount = 0
        }
    }

    var timerLabel: String {
        String(format: "%d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
    }
}

struct CaptureView: View {
    @StateObject private var viewModel = CaptureViewModel()
    @State private var textInput = ""
    @FocusState private var textFocused: Bool

    var body: some View {
        ScrollView {
            VStack(spacing: 28) {
                headerMetrics

                recordControl
                    .padding(.vertical, 8)

                textCapture
                    .padding(.horizontal, 20)

                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.orbitBody(13))
                        .foregroundStyle(Color.orbitStatusMid)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                        .accessibilityLabel(error)
                }

                if viewModel.lastEventId != nil {
                    Text("CAPTURED — HEAD TO REVIEW")
                        .font(.orbitCaps(11))
                        .tracking(1.2)
                        .foregroundStyle(Color.orbitAccent)
                }
            }
            .padding(.vertical, 20)
        }
        .orbitScreen()
        .navigationTitle("Capture")
        .navigationBarTitleDisplayMode(.large)
        .task { await viewModel.refreshPendingCount() }
        .refreshable { await viewModel.refreshPendingCount() }
    }

    private var headerMetrics: some View {
        HStack(spacing: 12) {
            metricTile(
                caption: "Events",
                value: "\(viewModel.pendingReviewCount)",
                tone: viewModel.pendingReviewCount > 0 ? .mid : .high
            )
            metricTile(
                caption: "Proposals",
                value: "\(viewModel.pendingProposalCount)",
                tone: viewModel.pendingProposalCount > 5 ? .low : (viewModel.pendingProposalCount > 0 ? .mid : .high)
            )
        }
        .padding(.horizontal, 20)
    }

    private func metricTile(caption: String, value: String, tone: OrbitStatusChip.Tone) -> some View {
        OrbitCard {
            VStack(alignment: .leading, spacing: 8) {
                Text(caption.uppercased())
                    .font(.orbitCaps(10))
                    .tracking(1.2)
                    .foregroundStyle(Color.orbitMuted)
                Text(value)
                    .font(.orbitHero(36))
                    .foregroundStyle(tone.color)
                Text(tone == .high ? "Clear" : "Awaiting review")
                    .font(.orbitBody(12))
                    .foregroundStyle(Color.orbitFaint)
            }
        }
    }

    private var recordControl: some View {
        VStack(spacing: 20) {
            Button {
                viewModel.toggleRecording()
            } label: {
                OrbitMetricRing(
                    valueLabel: viewModel.isRecording ? viewModel.timerLabel : "●",
                    caption: viewModel.isRecording ? "Recording" : "Capture",
                    progress: viewModel.isRecording
                        ? min(1, Double(viewModel.elapsedSeconds) / 120.0)
                        : (viewModel.isSubmitting ? 0.35 : 0.12),
                    tint: viewModel.isRecording ? Color.orbitStatusLow : Color.orbitAccent,
                    diameter: 240
                )
            }
            .buttonStyle(.plain)
            .accessibilityLabel(viewModel.isRecording ? "Stop recording" : "Start recording")
            .sensoryFeedback(.impact(weight: .medium, intensity: 0.7), trigger: viewModel.isRecording)

            Text(viewModel.isRecording ? "Tap to stop & upload" : "Tap the ring to record")
                .font(.orbitBody(14))
                .foregroundStyle(Color.orbitMuted)
        }
    }

    private var textCapture: some View {
        OrbitCard {
            VStack(alignment: .leading, spacing: 14) {
                OrbitSectionLabel(text: "Or type it")
                TextField("What just happened…", text: $textInput, axis: .vertical)
                    .font(.orbitBody(15))
                    .foregroundStyle(Color.orbitInk)
                    .lineLimit(3...6)
                    .focused($textFocused)
                    .padding(12)
                    .background(Color.orbitCanvas)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .stroke(textFocused ? Color.orbitAccent.opacity(0.5) : Color.orbitDivider, lineWidth: 1)
                    )

                OrbitPrimaryButton(
                    title: "Save text",
                    isEnabled: !textInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !viewModel.isSubmitting
                ) {
                    let draft = textInput
                    textInput = ""
                    textFocused = false
                    Task { await viewModel.submitText(draft) }
                }
            }
        }
    }
}
