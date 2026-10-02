import SwiftUI

/// Pick a PC found on the Wi-Fi, or type its address.
struct ConnectView: View {
    @Environment(AppModel.self) private var model
    @State private var browser = PCBrowser()
    @State private var typed = ""
    @State private var resolving: String?
    @FocusState private var fieldFocused: Bool

    var body: some View {
        ScrollView {
            VStack(spacing: 28) {
                header
                foundList
                manualEntry
                if let err = model.connectError {
                    Label(err, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(Theme.warn)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .panel()
                }
                help
            }
            .frame(maxWidth: 560)
            .padding(.horizontal, 20)
            .padding(.vertical, 40)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .onAppear { browser.start() }
        .onDisappear { browser.stop() }
    }

    private var header: some View {
        VStack(spacing: 10) {
            Image("Logo")
                .resizable()
                .scaledToFit()
                .frame(width: 84, height: 84)
                .clipShape(RoundedRectangle(cornerRadius: 20))
            Text("WITNESS")
                .font(.system(size: 30, weight: .heavy))
                .tracking(6)
                .foregroundStyle(Theme.text)
            Text("Your clips, reels and alerts from the PC.")
                .font(.callout)
                .foregroundStyle(Theme.muted)
        }
    }

    @ViewBuilder
    private var foundList: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("ON YOUR WI-FI")
                    .font(.caption.weight(.bold))
                    .tracking(1.5)
                    .foregroundStyle(Theme.muted)
                if browser.state == .browsing {
                    ProgressView().controlSize(.mini)
                }
            }
            switch browser.state {
            case .denied:
                VStack(alignment: .leading, spacing: 8) {
                    Label("WITNESS isn't allowed to look on your Wi-Fi.",
                          systemImage: "wifi.exclamationmark")
                        .foregroundStyle(Theme.warn)
                    Text("Settings → Privacy & Security → Local Network → turn on WITNESS. "
                         + "Or type the PC's address below.")
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                    Button("Open Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    }
                    .font(.footnote.weight(.semibold))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .panel()
            case .failed(let why):
                Text("Couldn't search the network: \(why)")
                    .font(.footnote)
                    .foregroundStyle(Theme.warn)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .panel()
            default:
                if browser.found.isEmpty {
                    Text("Looking for a PC running WITNESS…")
                        .font(.callout)
                        .foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .panel()
                } else {
                    ForEach(browser.found) { pc in
                        Button {
                            Task { await connect(pc) }
                        } label: {
                            HStack(spacing: 14) {
                                Image(systemName: "desktopcomputer")
                                    .font(.title2)
                                    .foregroundStyle(Theme.accent)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(pc.name).font(.headline).foregroundStyle(Theme.text)
                                    if let host = pc.url?.host() {
                                        Text(host + (pc.version.isEmpty ? "" : " · \(pc.version)"))
                                            .font(.caption.monospaced())
                                            .foregroundStyle(Theme.muted)
                                    }
                                }
                                Spacer()
                                if resolving == pc.id || (model.connecting && resolving == pc.id) {
                                    ProgressView()
                                } else {
                                    Image(systemName: "chevron.right").foregroundStyle(Theme.muted)
                                }
                            }
                            .contentShape(Rectangle())
                            .panel()
                        }
                        .buttonStyle(.plain)
                        .disabled(model.connecting)
                    }
                }
            }
        }
    }

    private var manualEntry: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("OR TYPE THE ADDRESS")
                .font(.caption.weight(.bold))
                .tracking(1.5)
                .foregroundStyle(Theme.muted)
            HStack(spacing: 10) {
                TextField("192.168.1.20:8000", text: $typed)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($fieldFocused)
                    .submitLabel(.go)
                    .onSubmit { Task { await connectTyped() } }
                    .padding(12)
                    .background(Theme.raised, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Theme.line))
                Button {
                    Task { await connectTyped() }
                } label: {
                    if model.connecting && resolving == nil {
                        ProgressView().frame(width: 74)
                    } else {
                        Text("Connect").frame(width: 74)
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty || model.connecting)
            }
        }
    }

    private var help: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Not finding it?")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(Theme.text)
            Text("WITNESS has to be running on the PC, with web_lan: true in config.yaml, "
                 + "and this iPad on the same Wi-Fi. The address is printed in the PC "
                 + "window when a session starts (\"Live view: http://…\").")
                .font(.footnote)
                .foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func connect(_ pc: PCBrowser.Found) async {
        resolving = pc.id
        defer { resolving = nil }
        var url = pc.url
        if url == nil { url = await PCBrowser.resolve(pc.endpoint) }
        guard let url else {
            model.connectError = "Found \(pc.name), but couldn't work out its address. "
                + "Type it below instead."
            return
        }
        await model.connect(to: url)
    }

    private func connectTyped() async {
        fieldFocused = false
        guard let url = WitnessClient.url(from: typed) else {
            model.connectError = WitnessClient.ClientError.badAddress.errorDescription
            return
        }
        await model.connect(to: url)
    }
}
