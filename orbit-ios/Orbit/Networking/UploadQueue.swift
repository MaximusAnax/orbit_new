import Foundation

struct PendingUpload: Codable, Identifiable {
    let id: UUID
    let localFileURL: URL
    let createdAt: Date
    var retryCount: Int
}

@MainActor
final class UploadQueue: ObservableObject {
    static let shared = UploadQueue()

    @Published private(set) var pending: [PendingUpload] = []
    private let storageKey = "orbit.pendingUploads"

    init() {
        load()
    }

    func enqueue(localFileURL: URL) {
        let item = PendingUpload(id: UUID(), localFileURL: localFileURL, createdAt: Date(), retryCount: 0)
        pending.append(item)
        save()
        Task { await processQueue() }
    }

    func processQueue() async {
        guard !pending.isEmpty else { return }
        var remaining: [PendingUpload] = []
        for var item in pending {
            do {
                _ = try await APIClient.shared.uploadEvent(audioURL: item.localFileURL)
            } catch {
                item.retryCount += 1
                if item.retryCount < 5 {
                    remaining.append(item)
                }
            }
        }
        pending = remaining
        save()
    }

    private func save() {
        if let data = try? JSONEncoder().encode(pending.map { PendingUploadCodable(from: $0) }) {
            UserDefaults.standard.set(data, forKey: storageKey)
        }
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let items = try? JSONDecoder().decode([PendingUploadCodable].self, from: data) else { return }
        pending = items.map { $0.toPendingUpload() }
    }
}

private struct PendingUploadCodable: Codable {
    let id: UUID
    let path: String
    let createdAt: Date
    let retryCount: Int

    init(from item: PendingUpload) {
        id = item.id
        path = item.localFileURL.path
        createdAt = item.createdAt
        retryCount = item.retryCount
    }

    func toPendingUpload() -> PendingUpload {
        PendingUpload(id: id, localFileURL: URL(fileURLWithPath: path), createdAt: createdAt, retryCount: retryCount)
    }
}
