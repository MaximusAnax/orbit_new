import SwiftUI

@main
struct OrbitApp: App {
    var body: some Scene {
        WindowGroup {
            OrbitTabView()
                .preferredColorScheme(.dark)
        }
    }
}
