import SwiftUI

@main
struct CoverageApp: App {

    @State private var report: FoundationReport = .loading

    var body: some Scene {
        WindowGroup {
            FoundationSmokeTestView(report: report)
                .task { report = await FoundationSmokeTestView.run() }
        }
        .windowStyle(.titleBar)
    }
}

enum FoundationReport: Equatable {
    case loading
    case ok(schemaVersion: Int, carrierCount: Int, renewalStatusCount: Int)
    case failure(String)
}

struct FoundationSmokeTestView: View {

    let report: FoundationReport

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("PolicyDB — Foundation Smoke Test")
                .font(.title2.bold())

            switch report {
            case .loading:
                ProgressView("Checking foundation…")
            case let .ok(schema, carriers, statuses):
                GroupBox("Database") {
                    LabeledContent("Schema version", value: "\(schema)")
                }
                GroupBox("Config") {
                    LabeledContent("Carriers", value: "\(carriers)")
                    LabeledContent("Renewal statuses", value: "\(statuses)")
                }
                Label("Foundation OK — Phase 2 (Clients CRUD) goes here.",
                      systemImage: "checkmark.seal.fill")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            case let .failure(msg):
                Label("Foundation check failed", systemImage: "exclamationmark.triangle.fill")
                    .font(.headline)
                    .foregroundStyle(.red)
                Text(msg)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
            }

            Spacer()
        }
        .padding(24)
        .frame(minWidth: 520, minHeight: 320)
    }

    static func run() async -> FoundationReport {
        do {
            let manager = try DatabaseManager.default()
            try manager.assertCompatibleSchema()
            let schemaVersion = try manager.currentSchemaVersion()
            let config = try ConfigReader.load()
            return .ok(
                schemaVersion: schemaVersion,
                carrierCount: config.carriers.count,
                renewalStatusCount: config.renewalStatuses.count
            )
        } catch {
            return .failure(String(describing: error))
        }
    }
}

#Preview("Loading") {
    FoundationSmokeTestView(report: .loading)
}

#Preview("OK") {
    FoundationSmokeTestView(report: .ok(schemaVersion: 163, carrierCount: 42, renewalStatusCount: 7))
}

#Preview("Failure") {
    FoundationSmokeTestView(report: .failure("DatabaseManagerError.fileMissing(\"/Users/you/.policydb/policydb.sqlite\")"))
}
