import XCTest
@testable import Orbit

@MainActor
final class APIClientURLTests: XCTestCase {
    func testMakeURLKeepsQuerySeparateFromPath() throws {
        let client = APIClient.shared
        let url = try client.makeURL(
            path: "/proposals",
            query: [URLQueryItem(name: "event_id", value: "00443195-CF8C-456E-BCED-4913B41778A7")]
        )
        XCTAssertFalse(url.absoluteString.contains("%3F"), "query must not be path-encoded")
        XCTAssertTrue(url.absoluteString.contains("event_id="))
        XCTAssertTrue(url.path.contains("/api/v1/proposals"))
    }
}

@MainActor
final class ReviewViewModelTests: XCTestCase {
    func testReviewViewModelInitialState() {
        let vm = ReviewViewModel()
        XCTAssertTrue(vm.events.isEmpty)
        XCTAssertTrue(vm.proposals.isEmpty)
        XCTAssertNil(vm.selectedEvent)
    }

    func testProposalsGroupByPerson() {
        let vm = ReviewViewModel()
        let pid = UUID()
        vm.peopleById = [pid: "Sarah"]
        XCTAssertTrue(vm.proposalsByPerson.isEmpty)
    }
}

@MainActor
final class CaptureViewModelTests: XCTestCase {
    func testCaptureViewModelInitialState() {
        let vm = CaptureViewModel()
        XCTAssertFalse(vm.isRecording)
        XCTAssertEqual(vm.pendingReviewCount, 0)
        XCTAssertEqual(vm.timerLabel, "0:00")
    }
}

@MainActor
final class MaintenanceModelTests: XCTestCase {
    func testMaintenanceSuggestionDecodes() throws {
        let json = """
        {"person_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","person_name":"Alex","score":1.5,"target_days":14,"days_since_contact":20,"reasons":["Open thread: hi"],"reason_codes":["open_task"]}
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(MaintenanceSuggestion.self, from: json)
        XCTAssertEqual(decoded.personName, "Alex")
        XCTAssertEqual(decoded.reasonCodes, ["open_task"])
    }
}
