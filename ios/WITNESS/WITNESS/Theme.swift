import SwiftUI

/// The PC dashboard's palette (webserver.py THEME_DEFAULTS), so the app and the
/// Safari dashboard read as the same product. Dark only, like the dashboard.
enum Theme {
    static let bg = Color(hex: 0x0E0E16)
    static let panel = Color(hex: 0x12181D)
    static let raised = Color(hex: 0x1A2128)
    static let line = Color(hex: 0x232D34)
    static let text = Color(hex: 0xE9E9ED)
    static let muted = Color(hex: 0x8A90A0)
    static let accent = Color(hex: 0x9184D9)
    static let accentLight = Color(hex: 0xC7BDFF)
    static let danger = Color(hex: 0xFF4D3D)
    static let warn = Color(hex: 0xF5A623)
    static let ok = Color(hex: 0x5AC47D)

    /// Chip colours by role (clip_index.TAG_ROLE), matching the web Clip
    /// viewer's .chip.kill / .prec / .elim / .assist / .manual.
    static func chip(_ role: String) -> Color {
        switch role {
        case "kill": return accentLight            // a down
        case "prec": return Color(hex: 0xF5C76A)   // precision (headshot)
        case "elim": return Color(hex: 0xFF8A7A)
        case "manual": return Color(hex: 0x7FD79B)
        default: return muted                      // assist
        }
    }
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: opacity)
    }

    /// "#f5a623" from the server (heat colour). Falls back to the accent.
    init(css: String?) {
        let s = (css ?? "").trimmingCharacters(in: CharacterSet(charactersIn: "# "))
        if s.count == 6, let v = UInt32(s, radix: 16) {
            self.init(hex: v)
        } else {
            self = Theme.accent
        }
    }
}

/// Panel look shared by cards and bars.
struct Panel: ViewModifier {
    var padding: CGFloat = 14
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line))
    }
}

extension View {
    func panel(padding: CGFloat = 14) -> some View { modifier(Panel(padding: padding)) }
}

enum Format {
    /// 3725 -> "1:02:05", 65 -> "1:05"
    static func clock(_ seconds: Int) -> String {
        let h = seconds / 3600, m = (seconds % 3600) / 60, s = seconds % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%d:%02d", m, s)
    }
}
