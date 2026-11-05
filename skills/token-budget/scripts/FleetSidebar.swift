import Cocoa
import SwiftUI

// MARK: - Models
struct FleetData: Codable {
    let now: Double
    let today_str: String
    let current_mac_acct: String?
    let proxy_health: ProxyHealth?
    let switch_alert: SwitchAlert?
    let accounts: [String: ClaudeAcct]
    let rotation_order: [ClaudeAcct]
    let antigravity: AgyStats
    let summary: FleetSummary
    let heavy_threads: [HeavyThread]
}

struct SwitchAlert: Codable {
    let level: String       // "critical" | "warn"
    let title: String
    let msg: String
    let target_prefix: String
    let target_name: String
}

struct ProxyHealth: Codable {
    let ok: Bool
    let level: String       // "ok" | "warn" | "error"
    let label: String?
    let status_text: String
    let reason: String
    let ip: String
    let node: String
    let delay_ms: Int
    let active_conns: Int
    let stale_conns: Int
    let sys_proxy_on: Bool
    let closed_conns: Int?
}

struct ClaudeAcct: Codable, Identifiable {
    var id: String { acct_prefix }
    let acct_prefix: String
    let name: String
    let note: String
    let email: String
    let is_current: Bool
    let ready: Bool
    let status: String
    let fh: Double
    let sd: Double
    let rem_pts: Double
    let rem_windows: Double
    let rem_usd: Double
    let five_resets_ts: Double?
    let seven_resets_ts: Double?
    let days_left: Double
    let daily_target_pts: Double
    let daily_target_win: Double
    let daily_target_usd: Double
    let advice: String
    let priority: Int
}

struct AgyStats: Codable {
    let steps_today: Int
    let quota: AgyQuota
    let gpt_plus: [GptPlusAcct]
}

struct AgyQuota: Codable {
    let email: String
    let note: String
    let gemini_5h_rem: Double
    let gemini_7d_rem: Double
    let claude_5h_rem: Double
    let claude_7d_rem: Double
}

struct GptPlusAcct: Codable, Identifiable {
    let id: String
    let email: String
    let note: String
    let is_current: Bool
    let fh_rem: Double
    let sd_rem: Double
    let sd_used: Double
    let days_left: Double
    let reset_credits: Int
}

struct FleetSummary: Codable {
    let mac_rem_pts: Double
    let mac_rem_win: Double
    let mac_rem_usd: Double
    let mac_daily_target_pts: Double
    let mac_daily_target_win: Double
    let fleet_daily_target_usd: Double
    let today_total_usd: Double
}

struct HeavyThread: Codable, Identifiable {
    var id: String { thread }
    let thread: String
    let usd_24h: Double
    let usd_today: Double
    let turns_24h: Int
    let max_ctx: Int
}

struct VaultConfig: Codable {
    var mail_tool_url: String
    var onet_webmail_url: String
    var gmx_webmail_url: String?
    var claude_login_url: String
    var accounts: [VaultAccount]
    var extra_accounts: [VaultExtraAccount]?
}

struct VaultAccount: Codable, Identifiable {
    var id: String { acct_prefix }
    var acct_prefix: String
    var org_prefix: String
    var name: String
    var note: String
    var email: String
    var provider: String
}

struct VaultExtraAccount: Codable, Identifiable {
    var id: String { email }
    var name: String
    var note: String
    var email: String
    var provider: String
}

// MARK: - High-Contrast Palette
struct Theme {
    static let bgMain = Color(red: 0.07, green: 0.08, blue: 0.11).opacity(0.96)
    static let bgCard = Color(red: 0.12, green: 0.14, blue: 0.19)
    static let bgCardActive = Color(red: 0.16, green: 0.19, blue: 0.26)
    static let bgRedAlert = Color(red: 0.78, green: 0.10, blue: 0.15)
    static let bgWarnAlert = Color(red: 0.45, green: 0.26, blue: 0.05)
    static let bgOkBar = Color(red: 0.09, green: 0.22, blue: 0.17)
    static let textPrimary = Color(red: 0.96, green: 0.97, blue: 0.99)
    static let textSecondary = Color(red: 0.78, green: 0.82, blue: 0.89)
    static let textMuted = Color(red: 0.62, green: 0.67, blue: 0.76)
    static let accentGold = Color(red: 1.00, green: 0.84, blue: 0.25)
    static let accentGreen = Color(red: 0.22, green: 0.90, blue: 0.55)
    static let accentCyan = Color(red: 0.30, green: 0.85, blue: 0.98)
    static let accentOrange = Color(red: 1.00, green: 0.62, blue: 0.22)
    static let accentRed = Color(red: 1.00, green: 0.32, blue: 0.35)
    static let accentPurple = Color(red: 0.68, green: 0.45, blue: 0.98)
}

// MARK: - ViewModel
class SidebarStore: ObservableObject {
    @Published var fleet: FleetData?
    @Published var vault: VaultConfig
    @Published var safeIP: String = "auto"
    @Published var toastMsg: String? = nil
    @Published var isCollapsed: Bool = false
    @Published var editingAcctId: String? = nil
    @Published var lastUpdated: String = ""
    @Published var isSyncing: Bool = false
    @Published var isPingingProxy: Bool = false

    var onProxyLevelChange: ((String) -> Void)?
    private var timer: Timer?
    let vaultPath = NSString(string: "~/.claude/accounts-vault.json").expandingTildeInPath
    let safeIpPath = NSString(string: "~/.claude/safe_ip.txt").expandingTildeInPath
    let tokensScript = NSString(string: "~/.claude/scripts/tokens.py").expandingTildeInPath
    let sessionsScript = NSString(string: "~/.claude/scripts/sessions.py").expandingTildeInPath
    var pythonBin: String {
        let p = NSString(string: "~/.gemini/antigravity/bin/python3").expandingTildeInPath
        return FileManager.default.fileExists(atPath: p) ? p : "/usr/bin/python3"
    }
    var proxyLabel: String {
        fleet?.proxy_health?.label ?? "代理"
    }

    init() {
        self.vault = SidebarStore.loadVaultStatic(path: NSString(string: "~/.claude/accounts-vault.json").expandingTildeInPath)
        refreshAll(forcePing: false, manual: false)
        let t = Timer(timeInterval: 10.0, repeats: true) { [weak self] _ in
            self?.refreshFleet(forcePing: false, manual: false)
        }
        RunLoop.main.add(t, forMode: .common)
        self.timer = t
    }

    static func loadVaultStatic(path: String) -> VaultConfig {
        if let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
           let decoded = try? JSONDecoder().decode(VaultConfig.self, from: data) {
            return decoded
        }
        return VaultConfig(
            mail_tool_url: "https://claude.ai",
            onet_webmail_url: "https://claude.ai",
            gmx_webmail_url: "https://claude.ai",
            claude_login_url: "https://claude.ai/login",
            accounts: [],
            extra_accounts: []
        )
    }

    func saveVault() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(vault) {
            try? data.write(to: URL(fileURLWithPath: vaultPath))
            showToast("✅ 已保存账号备注标签")
        }
    }

    func refreshAll(forcePing: Bool = false, manual: Bool = true) {
        self.vault = SidebarStore.loadVaultStatic(path: vaultPath)
        if let ip = try? String(contentsOfFile: safeIpPath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines), !ip.isEmpty {
            self.safeIP = ip
        }
        if manual {
            showToast("🔄 正在实时同步额度与\(proxyLabel)状态...")
        }
        refreshFleet(forcePing: forcePing, manual: manual)
    }

    func refreshFleet(forcePing: Bool = false, manual: Bool = false) {
        if forcePing || manual { isPingingProxy = true }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: self.pythonBin)
            var args = [self.tokensScript, "--json"]
            if forcePing { args.append("--ping-tuzi") }
            proc.arguments = args
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = Pipe()
            do {
                try proc.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                proc.waitUntilExit()
                if let decoded = try? JSONDecoder().decode(FleetData.self, from: data) {
                    let df = DateFormatter()
                    df.dateFormat = "HH:mm:ss"
                    let nowStr = df.string(from: Date())
                    DispatchQueue.main.async {
                        self.fleet = decoded
                        self.lastUpdated = nowStr
                        self.isPingingProxy = false
                        let alertLevel = (decoded.switch_alert?.level == "critical") ? "error" : (decoded.proxy_health?.level ?? "ok")
                        self.onProxyLevelChange?(alertLevel)
                        if manual {
                            self.showToast("✅ 额度与\(self.proxyLabel)已实时刷新 (\(nowStr))")
                        } else if forcePing, let ph = decoded.proxy_health {
                            self.showToast(ph.status_text)
                        }
                    }
                } else {
                    DispatchQueue.main.async { self.isPingingProxy = false }
                }
            } catch {
                DispatchQueue.main.async { self.isPingingProxy = false }
            }
        }
    }

    func healTuziConnections() {
        guard !isPingingProxy else { return }
        isPingingProxy = true
        showToast("🧹 正在清理\(proxyLabel)高并发残留连接并重测...")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: self.pythonBin)
            proc.arguments = [self.tokensScript, "--heal-tuzi"]
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = Pipe()
            try? proc.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            proc.waitUntilExit()
            let closed = (try? JSONDecoder().decode(ProxyHealth.self, from: data))?.closed_conns ?? 0
            DispatchQueue.main.async {
                self.showToast("✅ 已清理 \(closed) 条\(self.proxyLabel)并发连接！正在刷新...")
                self.refreshFleet(forcePing: true)
            }
        }
    }

    func copyText(_ text: String, label: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
        showToast("📋 已复制\(label)：\(text.prefix(22))")
    }

    func openURL(_ urlStr: String) {
        if let u = URL(string: urlStr) {
            NSWorkspace.shared.open(u)
        }
    }

    func quickSwitch(acct: VaultAccount) {
        if let ph = fleet?.proxy_health, ph.level == "error" {
            showToast("🚨 \(proxyLabel)代理异常！已拦截切号，请先点[清并发重连]！")
            return
        }
        copyText(acct.email, label: "\(acct.name)邮箱")
        if acct.email.lowercased().hasSuffix("@example.com") {
            openURL(vault.gmx_webmail_url ?? "https://www.gmx.es")
        } else {
            openURL(vault.mail_tool_url)
        }
        showToast("🛡️ 已复制 \(acct.name) 邮箱 + 正在执行净室防封切号隔离...")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            proc.arguments = [self.sessionsScript, "--prep-switch"]
            try? proc.run()
            proc.waitUntilExit()
            DispatchQueue.main.async {
                let claudeURL = URL(fileURLWithPath: "/Applications/Claude.app")
                NSWorkspace.shared.openApplication(at: claudeURL, configuration: NSWorkspace.OpenConfiguration(), completionHandler: nil)
                self.showToast("✅ 已完成防封隔离 (各号独立2k种子 + 清残留CLI)！")
                self.refreshFleet()
            }
        }
    }

    func runSessionsApply() {
        guard !isSyncing else { return }
        isSyncing = true
        showToast("🛡️ 正在同步侧边栏 + 执行跨号独立SessionID防封隔离...")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            proc.arguments = [self.sessionsScript, "--prep-switch"]
            try? proc.run()
            proc.waitUntilExit()
            DispatchQueue.main.async {
                self.isSyncing = false
                self.showToast("✅ 全舰队 38 条主线程已 1:1 独立隔离同步！")
                self.refreshFleet()
            }
        }
    }

    func showToast(_ msg: String) {
        withAnimation {
            self.toastMsg = msg
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
            withAnimation {
                if self?.toastMsg == msg {
                    self?.toastMsg = nil
                }
            }
        }
    }
}

// MARK: - Helpers
func fmtDualShort(_ ts: Double?) -> String {
    guard let ts = ts, ts > 0 else { return "—" }
    let dt = Date(timeIntervalSince1970: ts)
    let dfBj = DateFormatter()
    dfBj.timeZone = TimeZone(secondsFromGMT: 8 * 3600)
    dfBj.dateFormat = "MM-dd HH:mm"
    return "京\(dfBj.string(from: dt))"
}

// MARK: - Views
struct SidebarMainView: View {
    @ObservedObject var store: SidebarStore
    let onToggleCollapse: (Bool) -> Void

    var isProxyError: Bool {
        store.fleet?.proxy_health?.level == "error"
    }

    var body: some View {
        ZStack(alignment: .top) {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Theme.bgMain)
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(isProxyError ? Theme.accentRed : Color.white.opacity(0.22), lineWidth: isProxyError ? 2.5 : 1.2)
                )

            if store.isCollapsed {
                CollapsedPillView(store: store) {
                    store.isCollapsed = false
                    onToggleCollapse(false)
                }
            } else {
                ExpandedPanelView(store: store) {
                    store.isCollapsed = true
                    onToggleCollapse(true)
                }
            }

            if let toast = store.toastMsg {
                Text(toast)
                    .font(.system(size: 11.5, weight: .bold))
                    .foregroundColor(Color.black)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 7)
                    .background(Theme.accentGold)
                    .cornerRadius(8)
                    .shadow(color: Color.black.opacity(0.5), radius: 8, x: 0, y: 3)
                    .padding(.top, 10)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .environment(\.colorScheme, .dark)
    }
}

struct CollapsedPillView: View {
    @ObservedObject var store: SidebarStore
    let onExpand: () -> Void

    var proxyLevel: String {
        store.fleet?.proxy_health?.level ?? "ok"
    }

    var body: some View {
        VStack(spacing: 6) {
            Button(action: onExpand) {
                VStack(spacing: 4) {
                    Image(systemName: proxyLevel == "error" ? "exclamationmark.triangle.fill" : "sidebar.left")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(proxyLevel == "error" ? .white : Theme.accentCyan)
                    Text(proxyLevel == "error" ? "断连!" : store.proxyLabel)
                        .font(.system(size: 10.5, weight: .heavy))
                        .foregroundColor(.white)
                    if let ph = store.fleet?.proxy_health {
                        Text(ph.delay_ms > 0 ? "\(ph.delay_ms)ms" : "ERR")
                            .font(.system(size: 9.5, weight: .heavy, design: .monospaced))
                            .foregroundColor(proxyLevel == "error" ? Theme.accentGold : (proxyLevel == "warn" ? Theme.accentOrange : Theme.accentGreen))
                    }
                }
                .padding(.vertical, 7)
                .frame(maxWidth: .infinity)
                .background(proxyLevel == "error" ? Theme.bgRedAlert : Color.clear)
                .cornerRadius(8)
            }
            .buttonStyle(.plain)

            Divider().background(Color.white.opacity(0.25))

            if let rot = store.fleet?.rotation_order {
                ForEach(rot) { a in
                    VStack(spacing: 1.5) {
                        Text(a.name)
                            .font(.system(size: 10, weight: .heavy))
                            .foregroundColor(a.is_current ? Theme.accentGold : Theme.textSecondary)
                        Text("\(Int(a.rem_pts))%")
                            .font(.system(size: 10, weight: .bold, design: .monospaced))
                            .foregroundColor(a.rem_pts <= 5 ? Theme.accentRed : (a.ready ? Theme.accentGreen : Theme.accentOrange))
                    }
                    .padding(.vertical, 3)
                }
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 4)
    }
}

struct ExpandedPanelView: View {
    @ObservedObject var store: SidebarStore
    let onCollapse: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // 1. Header
            HStack(spacing: 7) {
                Circle()
                    .fill(store.fleet?.proxy_health?.level == "error" ? Theme.accentRed : Theme.accentGreen)
                    .frame(width: 9, height: 9)
                VStack(alignment: .leading, spacing: 1.5) {
                    Text("⚡ 全舰队算力与秒切侧边栏")
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundColor(Theme.textPrimary)
                    Text("10秒自动同步 · 上次 \(store.lastUpdated)")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundColor(Theme.accentCyan)
                }
                Spacer()
                Button(action: { store.refreshAll(forcePing: true, manual: true) }) {
                    HStack(spacing: 3) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 10.5, weight: .bold))
                        Text(store.isPingingProxy ? "刷新中" : "刷新")
                            .font(.system(size: 10.5, weight: .heavy))
                    }
                    .foregroundColor(Color.black)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 5)
                    .background(Theme.accentGold)
                    .cornerRadius(6)
                }
                .buttonStyle(.plain)
                .help("手动立即刷新全舰队额度并检测代理状态")

                Button(action: onCollapse) {
                    Image(systemName: "chevron.right.2")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundColor(Color.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 5)
                        .background(Theme.accentCyan)
                        .cornerRadius(6)
                }
                .buttonStyle(.plain)
                .help("收起为贴边窄条")
            }
            .padding(.horizontal, 10)
            .padding(.top, 10)

            // 2. Real-Time Tuzi Proxy Monitor Banner (turns BRIGHT RED on failure / high concurrency!)
            if let ph = store.fleet?.proxy_health {
                TuziProxyBanner(ph: ph, store: store)
                    .padding(.horizontal, 10)
            }

            // 2.5 80% 切号防封预警横幅（5h>=80% 或 周额>=80% 自动高亮提醒切号）
            if let sa = store.fleet?.switch_alert {
                SwitchAlertBanner(sa: sa, store: store)
                    .padding(.horizontal, 10)
            }

            // 3. Quick Action Bar
            HStack(spacing: 4) {
                SmallActionBtn(title: "📬 054120", bg: Color(red: 0.38, green: 0.22, blue: 0.68), fg: Theme.textPrimary) {
                    store.openURL(store.vault.mail_tool_url)
                }
                SmallActionBtn(title: "🇪🇸 GMX邮", bg: Color(red: 0.12, green: 0.42, blue: 0.62), fg: Theme.textPrimary) {
                    store.openURL(store.vault.gmx_webmail_url ?? "https://www.gmx.es")
                }
                SmallActionBtn(title: "🇵🇱 Onet邮", bg: Color(red: 0.16, green: 0.34, blue: 0.66), fg: Theme.textPrimary) {
                    store.openURL(store.vault.onet_webmail_url)
                }
                SmallActionBtn(title: store.isSyncing ? "同步中" : "🛡️ 同步隔离", bg: Color(red: 0.14, green: 0.48, blue: 0.45), fg: Theme.textPrimary) {
                    store.runSessionsApply()
                }
                SmallActionBtn(title: "🔑 密码本", bg: Color(red: 0.25, green: 0.28, blue: 0.35), fg: Theme.accentGold) {
                    NSWorkspace.shared.open(URL(fileURLWithPath: store.vaultPath))
                }
            }
            .padding(.horizontal, 10)

            Divider().background(Color.white.opacity(0.2))

            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 9) {
                    // 4. 今日预算进度条
                    if let s = store.fleet?.summary {
                        DailyBudgetCard(summary: s)
                    }

                    // 5. Claude 存活账号池 + 一键复制切号卡片
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("🟣 CLAUDE 存活账号池 (满80%提醒切号 · 点按钮秒切)")
                                .font(.system(size: 11, weight: .heavy))
                                .foregroundColor(Theme.accentGold)
                            Spacer()
                            if let s = store.fleet?.summary {
                                let totalCap = (store.fleet?.rotation_order.count ?? 4) * 100
                                Text("总余 \(Int(s.mac_rem_pts))/\(totalCap)p (~\(String(format: "%.1f", s.mac_rem_win))窗)")
                                    .font(.system(size: 10.5, weight: .bold, design: .monospaced))
                                    .foregroundColor(Theme.accentGreen)
                            }
                        }

                        if let accounts = store.fleet?.rotation_order {
                            ForEach(accounts) { acct in
                                ClaudeAcctCard(acct: acct, store: store)
                            }
                        }
                    }

                    // 6. 反重力双池 + GPT Plus 2 号
                    if let agy = store.fleet?.antigravity {
                        MultiEngineCard(agy: agy, store: store)
                    }

                    // 7. >200k 吞金巨兽线程省流榜
                    if let heavy = store.fleet?.heavy_threads, !heavy.isEmpty {
                        HeavyThreadsSection(threads: Array(heavy.prefix(6)), store: store)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.bottom, 12)
            }
        }
    }
}

struct TuziProxyBanner: View {
    let ph: ProxyHealth
    @ObservedObject var store: SidebarStore

    var bannerBg: Color {
        if ph.level == "error" { return Theme.bgRedAlert }
        if ph.level == "warn" { return Theme.bgWarnAlert }
        return Theme.bgOkBar
    }

    var borderCol: Color {
        if ph.level == "error" { return Theme.accentRed }
        if ph.level == "warn" { return Theme.accentOrange }
        return Theme.accentGreen.opacity(0.55)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Text(ph.status_text)
                    .font(.system(size: ph.level == "error" ? 11.5 : 10.5, weight: .heavy, design: .monospaced))
                    .foregroundColor(ph.level == "error" ? .white : (ph.level == "warn" ? Theme.accentGold : Theme.accentGreen))
                    .lineLimit(1)
                Spacer()
                Button(action: { store.refreshFleet(forcePing: true) }) {
                    Text(store.isPingingProxy ? "测速..." : "🎯测速")
                        .font(.system(size: 9.5, weight: .bold))
                        .foregroundColor(Theme.textPrimary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2.5)
                        .background(Color.black.opacity(0.45))
                        .cornerRadius(4)
                }
                .buttonStyle(.plain)

                Button(action: { store.healTuziConnections() }) {
                    Text(ph.level == "error" ? "⚡清并发重连" : "🧹清并发")
                        .font(.system(size: 9.5, weight: .heavy))
                        .foregroundColor(.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2.5)
                        .background(ph.level == "error" ? Theme.accentGold : Theme.accentCyan)
                        .cornerRadius(4)
                }
                .buttonStyle(.plain)
                .help("一键清理高并发残留连接并重新测速")
            }

            Text(ph.reason)
                .font(.system(size: 9.5, weight: .semibold))
                .foregroundColor(ph.level == "error" ? Theme.accentGold : Theme.textSecondary)
                .lineLimit(1)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(bannerBg)
        .overlay(
            RoundedRectangle(cornerRadius: 7)
                .stroke(borderCol, lineWidth: ph.level == "error" ? 2.0 : 1.0)
        )
        .cornerRadius(7)
    }
}

struct SwitchAlertBanner: View {
    let sa: SwitchAlert
    @ObservedObject var store: SidebarStore

    var isCrit: Bool { sa.level == "critical" }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Text(sa.title)
                    .font(.system(size: 10.5, weight: .heavy))
                    .foregroundColor(isCrit ? .white : Theme.accentGold)
                    .lineLimit(1)
                Spacer()
                if !sa.target_prefix.isEmpty,
                   let v = store.vault.accounts.first(where: { $0.acct_prefix == sa.target_prefix }) {
                    Button(action: { store.quickSwitch(acct: v) }) {
                        Text("⚡切往\(v.name)")
                            .font(.system(size: 9.5, weight: .heavy))
                            .foregroundColor(.black)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2.5)
                            .background(Theme.accentGold)
                            .cornerRadius(4)
                    }
                    .buttonStyle(.plain)
                }
            }
            Text(sa.msg)
                .font(.system(size: 9.5, weight: .semibold))
                .foregroundColor(isCrit ? Theme.accentGold : Theme.textSecondary)
                .lineLimit(2)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(isCrit ? Theme.bgRedAlert : Theme.bgWarnAlert)
        .overlay(
            RoundedRectangle(cornerRadius: 7)
                .stroke(isCrit ? Theme.accentRed : Theme.accentOrange, lineWidth: isCrit ? 2.0 : 1.2)
        )
        .cornerRadius(7)
    }
}

struct SmallActionBtn: View {
    let title: String
    let bg: Color
    let fg: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 10, weight: .bold))
                .foregroundColor(fg)
                .padding(.horizontal, 4)
                .padding(.vertical, 5)
                .frame(maxWidth: .infinity)
                .background(bg)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.white.opacity(0.22), lineWidth: 0.8))
                .cornerRadius(6)
        }
        .buttonStyle(.plain)
    }
}

struct DailyBudgetCard: View {
    let summary: FleetSummary

    var body: some View {
        let pct = min(1.0, summary.today_total_usd / max(1.0, summary.fleet_daily_target_usd))
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text("🎯 今日花费 vs 黄金定额")
                    .font(.system(size: 10.5, weight: .bold))
                    .foregroundColor(Theme.textSecondary)
                Spacer()
                Text(String(format: "$%.1f / $%.0f (%.1f窗/天)", summary.today_total_usd, summary.fleet_daily_target_usd, summary.mac_daily_target_win))
                    .font(.system(size: 11, weight: .heavy, design: .monospaced))
                    .foregroundColor(pct > 0.9 ? Theme.accentOrange : Theme.accentCyan)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3.5)
                        .fill(Color.black.opacity(0.5))
                    RoundedRectangle(cornerRadius: 3.5)
                        .fill(pct > 0.9 ? Theme.accentOrange : Theme.accentCyan)
                        .frame(width: geo.size.width * CGFloat(pct))
                }
            }
            .frame(height: 6)
        }
        .padding(8)
        .background(Theme.bgCard)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.white.opacity(0.12), lineWidth: 1))
        .cornerRadius(8)
    }
}

struct ClaudeAcctCard: View {
    let acct: ClaudeAcct
    @ObservedObject var store: SidebarStore

    var vaultIndex: Int? {
        store.vault.accounts.firstIndex(where: { $0.acct_prefix == acct.acct_prefix || $0.name == acct.name })
    }

    var vaultItem: VaultAccount? {
        if let idx = vaultIndex {
            return store.vault.accounts[idx]
        }
        return nil
    }

    var statusColor: Color {
        if acct.rem_pts <= 5 { return Theme.accentRed }
        if !acct.ready { return Theme.accentOrange }
        return Theme.accentGreen
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            // Top Row: Name + Email prefix + Remaining %
            HStack(spacing: 5) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                Text("\(acct.name) · \(acct.note)")
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundColor(acct.is_current ? Theme.accentGold : Theme.textPrimary)
                if acct.is_current {
                    Text("当前在线")
                        .font(.system(size: 9, weight: .heavy))
                        .foregroundColor(.black)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1.5)
                        .background(Theme.accentGold)
                        .cornerRadius(4)
                } else if acct.acct_prefix.hasPrefix("pending_") {
                    Text("新购Pro")
                        .font(.system(size: 9, weight: .heavy))
                        .foregroundColor(.black)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1.5)
                        .background(Theme.accentGreen)
                        .cornerRadius(4)
                }
                Spacer()
                Text("已用\(Int(acct.sd))% / 余\(Int(acct.rem_pts))%")
                    .font(.system(size: 11, weight: .heavy, design: .monospaced))
                    .foregroundColor(statusColor)
            }

            // Progress bar (5h + 7d)
            HStack(spacing: 8) {
                HStack(spacing: 4) {
                    Text("5h已用:\(Int(acct.fh))% · 约余\(String(format: "%.1f", acct.rem_windows))窗")
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundColor(acct.fh >= 100 ? Theme.accentOrange : Theme.textSecondary)
                    if !acct.ready, let rts = acct.five_resets_ts {
                        Text("(\(fmtDualShort(rts))恢复)")
                            .font(.system(size: 9.5, weight: .bold))
                            .foregroundColor(Theme.accentOrange)
                    }
                }
                Spacer()
                Text("周重置:\(fmtDualShort(acct.seven_resets_ts))")
                    .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                    .foregroundColor(Theme.textMuted)
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(Color.black.opacity(0.55))
                    RoundedRectangle(cornerRadius: 3)
                        .fill(statusColor)
                        .frame(width: geo.size.width * CGFloat(max(0.03, acct.rem_pts / 100.0)))
                }
            }
            .frame(height: 5)

            // Quick Copy & Switch Row
            let emailStr = vaultItem?.email ?? acct.email
            let noteStr = vaultItem?.note ?? acct.note

            HStack(spacing: 4) {
                Button(action: {
                    store.copyText(emailStr, label: "\(acct.name)邮箱")
                }) {
                    HStack(spacing: 2.5) {
                        Image(systemName: "doc.on.doc.fill")
                            .font(.system(size: 8.5))
                        Text("复制邮箱")
                            .font(.system(size: 9.5, weight: .bold))
                    }
                    .foregroundColor(Theme.textPrimary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                    .background(Color(red: 0.18, green: 0.36, blue: 0.68))
                    .cornerRadius(5)
                }
                .buttonStyle(.plain)
                .help(emailStr)

                Button(action: {
                    store.editingAcctId = (store.editingAcctId == acct.acct_prefix) ? nil : acct.acct_prefix
                }) {
                    HStack(spacing: 2.5) {
                        Image(systemName: "tag.fill")
                            .font(.system(size: 8.5))
                        Text(noteStr.isEmpty ? "改备注" : noteStr)
                            .font(.system(size: 9.5, weight: .bold))
                    }
                    .foregroundColor(Theme.textPrimary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                    .background(Color.black.opacity(0.35))
                    .cornerRadius(5)
                }
                .buttonStyle(.plain)
                .help("修改该账号显示的邮箱标识与备注标签")

                Spacer()

                Button(action: {
                    if let v = vaultItem {
                        store.quickSwitch(acct: v)
                    } else {
                        store.copyText(emailStr, label: "\(acct.name)邮箱")
                    }
                }) {
                    HStack(spacing: 2.5) {
                        Image(systemName: "bolt.fill")
                            .font(.system(size: 8.5))
                        Text("一键切号准备")
                            .font(.system(size: 9.5, weight: .heavy))
                    }
                    .foregroundColor(.black)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 4)
                    .background(Theme.accentCyan)
                    .cornerRadius(5)
                }
                .buttonStyle(.plain)
                .help("同步多号侧边栏并唤起 Claude Desktop")
            }

            // Inline editor for email & note label
            if store.editingAcctId == acct.acct_prefix, let idx = vaultIndex {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 4) {
                        Text("邮箱:")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(Theme.accentGold)
                        TextField("输入邮箱标识 (仅作显示)", text: $store.vault.accounts[idx].email)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(size: 10.5, weight: .medium))
                    }
                    HStack(spacing: 4) {
                        Text("备注:")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(Theme.accentGold)
                        TextField("输入账号简称备注", text: $store.vault.accounts[idx].note)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(size: 10.5, weight: .medium))
                        Button(action: {
                            store.saveVault()
                            store.editingAcctId = nil
                        }) {
                            Text("保存")
                                .font(.system(size: 10, weight: .heavy))
                                .foregroundColor(.black)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Theme.accentGreen)
                                .cornerRadius(5)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(7)
                .background(Color.black.opacity(0.55))
                .cornerRadius(6)
            }
        }
        .padding(8)
        .background(acct.is_current ? Theme.bgCardActive : Theme.bgCard)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(acct.is_current ? Theme.accentGold : Color.white.opacity(0.14), lineWidth: acct.is_current ? 1.5 : 1)
        )
        .cornerRadius(8)
    }
}

struct MultiEngineCard: View {
    let agy: AgyStats
    @ObservedObject var store: SidebarStore

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("🚀 反重力双池 + GPT PLUS 双号")
                .font(.system(size: 11, weight: .heavy))
                .foregroundColor(Theme.accentCyan)

            // Antigravity row
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text("🔵 反重力 · \(agy.quota.email)")
                        .font(.system(size: 11.5, weight: .heavy))
                        .foregroundColor(Theme.accentCyan)
                    Spacer()
                    Button(action: {
                        store.copyText(agy.quota.email, label: "反重力邮箱")
                    }) {
                        Text("复制邮箱")
                            .font(.system(size: 9.5, weight: .bold))
                            .foregroundColor(Theme.textPrimary)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 3)
                            .background(Color(red: 0.18, green: 0.36, blue: 0.68))
                            .cornerRadius(4)
                    }
                    .buttonStyle(.plain)
                }
                HStack {
                    Text("Gemini池: 周余 \(Int(agy.quota.gemini_7d_rem))% (5h余\(Int(agy.quota.gemini_5h_rem))%)")
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundColor(Theme.accentGreen)
                    Spacer()
                    Text("内置Claude: 余 \(Int(agy.quota.claude_7d_rem))%")
                        .font(.system(size: 10, weight: .heavy, design: .monospaced))
                        .foregroundColor(Theme.accentGold)
                }
            }
            .padding(8)
            .background(Theme.bgCard)
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.white.opacity(0.12), lineWidth: 1))
            .cornerRadius(7)

            // GPT Plus rows
            ForEach(agy.gpt_plus) { gp in
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 5) {
                            Text("🟢 GPT+ · \(gp.note)@...")
                                .font(.system(size: 11, weight: .heavy))
                                .foregroundColor(Theme.textPrimary)
                            if gp.is_current {
                                Text("Codex当前")
                                    .font(.system(size: 8.5, weight: .heavy))
                                    .foregroundColor(.black)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 1)
                                    .background(Theme.accentGreen)
                                    .cornerRadius(3)
                            }
                        }
                        Text("周余 \(Int(gp.sd_rem))% · 5h余 \(Int(gp.fh_rem))% · +\(gp.reset_credits)重置券")
                            .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                            .foregroundColor(Theme.textSecondary)
                    }
                    Spacer()
                    Button(action: {
                        store.copyText(gp.email, label: "GPT+邮箱")
                    }) {
                        Text("复制邮箱")
                            .font(.system(size: 9.5, weight: .bold))
                            .foregroundColor(.black)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3.5)
                            .background(Theme.accentGreen)
                            .cornerRadius(4)
                    }
                    .buttonStyle(.plain)
                }
                .padding(8)
                .background(Theme.bgCard)
                .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.white.opacity(0.12), lineWidth: 1))
                .cornerRadius(7)
            }
        }
    }
}

struct HeavyThreadsSection: View {
    let threads: [HeavyThread]
    @ObservedObject var store: SidebarStore

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("🦖 24H 吞金线程榜 (点线程秒拷省流交接口令)")
                .font(.system(size: 11, weight: .heavy))
                .foregroundColor(Theme.accentOrange)

            ForEach(threads) { tr in
                let ctxK = tr.max_ctx / 1000
                let isHeavy = ctxK >= 200
                Button(action: {
                    let cmd = "读 ~/.claude/session-handoffs/\(tr.thread).auto.md 接着做"
                    store.copyText(cmd, label: "省流交接口令")
                }) {
                    HStack(spacing: 6) {
                        Text(isHeavy ? "🔴" : "🟡")
                            .font(.system(size: 9.5))
                        Text(tr.thread)
                            .font(.system(size: 10.5, weight: .bold))
                            .foregroundColor(Theme.textPrimary)
                            .lineLimit(1)
                        Spacer()
                        Text("\(ctxK)k")
                            .font(.system(size: 10, weight: .heavy, design: .monospaced))
                            .foregroundColor(isHeavy ? Theme.accentRed : Theme.accentGold)
                        Text(String(format: "$%.1f", tr.usd_24h))
                            .font(.system(size: 10, weight: .bold, design: .monospaced))
                            .foregroundColor(Theme.textSecondary)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(isHeavy ? Color(red: 0.30, green: 0.11, blue: 0.14) : Theme.bgCard)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(isHeavy ? Theme.accentRed.opacity(0.45) : Color.white.opacity(0.1), lineWidth: 1))
                    .cornerRadius(6)
                }
                .buttonStyle(.plain)
                .help("点击复制：读 ~/.claude/session-handoffs/\(tr.thread).auto.md 接着做")
            }
        }
    }
}

// MARK: - Floating Panel & AppDelegate
class FloatingSidebarPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var panel: FloatingSidebarPanel!
    var statusItem: NSStatusItem!
    let store = SidebarStore()

    let expandedWidth: CGFloat = 342
    let expandedHeight: CGFloat = 820
    let collapsedWidth: CGFloat = 58
    let collapsedHeight: CGFloat = 350

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setupMenuBar()
        setupFloatingPanel()
        store.onProxyLevelChange = { [weak self] level in
            guard let self = self, let btn = self.statusItem?.button else { return }
            let lbl = self.store.proxyLabel
            if level == "error" {
                btn.title = "🚨\(lbl)断连!"
            } else if level == "warn" {
                btn.title = "🟡\(lbl)高并发"
            } else {
                btn.title = "🟢\(lbl)·算力池"
            }
        }
    }

    func setupMenuBar() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let btn = statusItem.button {
            btn.title = "🟢\(store.proxyLabel)·算力池"
            btn.action = #selector(togglePanelVisibility)
            btn.target = self
        }
    }

    func setupFloatingPanel() {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let x = screen.maxX - expandedWidth - 6
        let y = screen.midY - expandedHeight / 2

        panel = FloatingSidebarPanel(
            contentRect: NSRect(x: x, y: y, width: expandedWidth, height: expandedHeight),
            styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.isMovableByWindowBackground = true

        let contentView = SidebarMainView(store: store) { [weak self] collapsed in
            self?.resizePanel(collapsed: collapsed)
        }
        panel.contentView = NSHostingView(rootView: contentView)
        panel.orderFrontRegardless()
    }

    func resizePanel(collapsed: Bool) {
        guard let screen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame else { return }
        let newW = collapsed ? collapsedWidth : expandedWidth
        let newH = collapsed ? collapsedHeight : expandedHeight
        let newX = screen.maxX - newW - 4
        let curMidY = panel.frame.midY
        let newY = max(screen.minY + 20, min(screen.maxY - newH - 20, curMidY - newH / 2))
        panel.setFrame(NSRect(x: newX, y: newY, width: newW, height: newH), display: true, animate: true)
    }

    @objc func togglePanelVisibility() {
        if panel.isVisible {
            panel.orderOut(nil)
        } else {
            panel.orderFrontRegardless()
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
