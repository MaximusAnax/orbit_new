import SwiftUI

// MARK: - Color tokens (Whoop-inspired: dark canvas, status spectrum, neon accent)

extension Color {
    static let orbitCanvas = Color(red: 0.039, green: 0.039, blue: 0.039) // #0A0A0A
    static let orbitSurface = Color(red: 0.110, green: 0.110, blue: 0.118) // #1C1C1E
    static let orbitSurfaceRaised = Color(red: 0.165, green: 0.165, blue: 0.169) // #2A2A2B
    static let orbitDivider = Color(red: 0.145, green: 0.145, blue: 0.145)

    static let orbitInk = Color.white
    static let orbitInkSoft = Color(red: 0.961, green: 0.961, blue: 0.969)
    static let orbitMuted = Color(red: 0.631, green: 0.631, blue: 0.667) // #A1A1AA
    static let orbitFaint = Color(red: 0.373, green: 0.373, blue: 0.396)

    /// Primary action / capture accent (strain neon)
    static let orbitAccent = Color(red: 0.000, green: 1.000, blue: 0.482) // #00FF7B
    static let orbitAccentDim = Color(red: 0.000, green: 0.373, blue: 0.184)

    static let orbitStatusHigh = Color(red: 0.086, green: 0.925, blue: 0.024) // green
    static let orbitStatusMid = Color(red: 1.000, green: 0.871, blue: 0.000) // yellow
    static let orbitStatusLow = Color(red: 1.000, green: 0.000, blue: 0.149) // red
    static let orbitInfo = Color(red: 0.000, green: 0.576, blue: 0.906) // sleep blue

    /// Status ramp for pending load / freshness (0 = calm/green, 1 = urgent/red).
    static func orbitStatus(urgency: Double) -> Color {
        let u = max(0, min(1, urgency))
        if u < 0.33 {
            let t = u / 0.33
            return Color(
                red: 0.086 + (1.0 - 0.086) * t,
                green: 0.925 + (0.871 - 0.925) * t,
                blue: 0.024 * (1 - t)
            )
        } else if u < 0.67 {
            let t = (u - 0.33) / 0.34
            return Color(red: 1.0, green: 0.871 * (1 - t), blue: 0.149 * t)
        } else {
            return .orbitStatusLow
        }
    }
}

enum OrbitTheme {
    static let accent = Color.orbitAccent
    static let cornerRadius: CGFloat = 16
    static let ringStroke: CGFloat = 10
}

// MARK: - Typography

extension Font {
    static func orbitHero(_ size: CGFloat = 64) -> Font {
        .system(size: size, weight: .bold, design: .rounded).monospacedDigit()
    }

    static func orbitTitle(_ size: CGFloat = 22) -> Font {
        .system(size: size, weight: .bold, design: .default)
    }

    static func orbitBody(_ size: CGFloat = 15) -> Font {
        .system(size: size, weight: .regular, design: .default)
    }

    static func orbitCaps(_ size: CGFloat = 12) -> Font {
        .system(size: size, weight: .bold, design: .default)
    }

    static func orbitMetric(_ size: CGFloat = 17) -> Font {
        .system(size: size, weight: .semibold, design: .default).monospacedDigit()
    }
}

// MARK: - Shared chrome

struct OrbitSectionLabel: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.orbitCaps(11))
            .tracking(1.4)
            .foregroundStyle(Color.orbitMuted)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct OrbitStatusChip: View {
    let label: String
    let tone: Tone

    enum Tone {
        case high, mid, low, info, neutral

        var color: Color {
            switch self {
            case .high: return .orbitStatusHigh
            case .mid: return .orbitStatusMid
            case .low: return .orbitStatusLow
            case .info: return .orbitInfo
            case .neutral: return .orbitMuted
            }
        }
    }

    var body: some View {
        Text(label.uppercased())
            .font(.orbitCaps(10))
            .tracking(0.8)
            .foregroundStyle(tone.color)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(tone.color.opacity(0.12))
            .clipShape(Capsule())
            .accessibilityLabel(label)
    }
}

struct OrbitCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.orbitSurface)
            .clipShape(RoundedRectangle(cornerRadius: OrbitTheme.cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: OrbitTheme.cornerRadius, style: .continuous)
                    .stroke(Color.orbitDivider, lineWidth: 1)
            )
    }
}

struct OrbitPrimaryButton: View {
    let title: String
    var isEnabled: Bool = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title.uppercased())
                .font(.orbitCaps(13))
                .tracking(1.2)
                .foregroundStyle(isEnabled ? Color.black : Color.orbitFaint)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(isEnabled ? Color.orbitAccent : Color.orbitSurfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .disabled(!isEnabled)
        .buttonStyle(.plain)
        .accessibilityLabel(title)
    }
}

struct OrbitGhostButton: View {
    let title: String
    var destructive: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title.uppercased())
                .font(.orbitCaps(11))
                .tracking(0.8)
                .foregroundStyle(destructive ? Color.orbitStatusLow : Color.orbitInkSoft)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(Color.orbitSurfaceRaised)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
    }
}

/// Glanceable metric ring — adapted from WHOOP recovery ring for Orbit pending / orbit level.
struct OrbitMetricRing: View {
    let valueLabel: String
    let caption: String
    var progress: Double // 0...1
    var tint: Color = .orbitAccent
    var diameter: CGFloat = 220

    @State private var animated: Double = 0

    var body: some View {
        ZStack {
            Circle()
                .trim(from: 0.02, to: 0.98)
                .stroke(Color.orbitSurfaceRaised, style: StrokeStyle(lineWidth: OrbitTheme.ringStroke, lineCap: .round))
                .rotationEffect(.degrees(90))

            Circle()
                .trim(from: 0.02, to: 0.02 + 0.96 * animated)
                .stroke(tint, style: StrokeStyle(lineWidth: OrbitTheme.ringStroke, lineCap: .round))
                .rotationEffect(.degrees(90))
                .animation(.easeOut(duration: 0.9), value: animated)

            VStack(spacing: 6) {
                Text(caption.uppercased())
                    .font(.orbitCaps(12))
                    .tracking(1.6)
                    .foregroundStyle(Color.orbitMuted)
                Text(valueLabel)
                    .font(.orbitHero(56))
                    .foregroundStyle(Color.orbitInk)
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
            }
        }
        .frame(width: diameter, height: diameter)
        .onAppear { animated = max(0, min(1, progress)) }
        .onChange(of: progress) { _, new in animated = max(0, min(1, new)) }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(caption) \(valueLabel)")
    }
}

struct OrbitEmptyState: View {
    let title: String
    let message: String
    var systemImage: String = "circle.dashed"

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: systemImage)
                .font(.system(size: 36, weight: .light))
                .foregroundStyle(Color.orbitFaint)
            Text(title)
                .font(.orbitTitle(18))
                .foregroundStyle(Color.orbitInkSoft)
            Text(message)
                .font(.orbitBody(14))
                .foregroundStyle(Color.orbitMuted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
        .padding(.horizontal, 24)
        .accessibilityElement(children: .combine)
    }
}

struct OrbitScreenBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(Color.orbitCanvas.ignoresSafeArea())
            .toolbarBackground(Color.orbitCanvas, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
    }
}

extension View {
    func orbitScreen() -> some View {
        modifier(OrbitScreenBackground())
    }
}

// MARK: - Tab shell

struct OrbitTabView: View {
    var body: some View {
        TabView {
            NavigationStack {
                CaptureView()
            }
            .tabItem { Label("Capture", systemImage: "waveform.circle.fill") }

            NavigationStack {
                ReviewView()
            }
            .tabItem { Label("Review", systemImage: "checklist") }

            NavigationStack {
                RecallView()
            }
            .tabItem { Label("Recall", systemImage: "person.2.fill") }

            NavigationStack {
                DiscoverView()
            }
            .tabItem { Label("Discover", systemImage: "sparkle.magnifyingglass") }
        }
        .tint(Color.orbitAccent)
        .preferredColorScheme(.dark)
        .onAppear {
            let appearance = UITabBarAppearance()
            appearance.configureWithOpaqueBackground()
            appearance.backgroundColor = UIColor(Color.orbitCanvas)
            UITabBar.appearance().standardAppearance = appearance
            UITabBar.appearance().scrollEdgeAppearance = appearance
        }
    }
}
