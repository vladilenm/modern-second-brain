// BrainBar — значок второго мозга в строке меню macOS.
// Раз в 2 секунды читает ~/brain/data/status.json (его пишет brain sync) и показывает,
// что происходит: забирает разговоры (↻), пересказывает (12/40), ошибка (!) или всё спокойно.
// Сборка: menubar/build.sh. Проверка без интерфейса: BrainBar --dump.
import AppKit
import ServiceManagement

let home = NSHomeDirectory()
let brainDir = home + "/brain"
let statusPath = brainDir + "/data/status.json"
let logPath = home + "/Library/Logs/brain-sync.log"
let agentLabel = "com.vladilen.brain-sync"
let syncInterval: TimeInterval = 15 * 60  // агент запускается в :00, :15, :30 и :45
let batchLimit = 40  // llm.batch_limit: сколько карточек делает один прогон
let sourceOrder: [(key: String, name: String)] = [("claude_code", "Claude Code"), ("codex", "Codex"), ("chatgpt", "ChatGPT")]

let isoParser = ISO8601DateFormatter()
let clockFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "HH:mm"
    return f
}()

func parseDate(_ value: Any?) -> Date? {
    (value as? String).flatMap { isoParser.date(from: $0) }
}

func ago(_ date: Date) -> String {
    let minutes = Int(Date().timeIntervalSince(date) / 60)
    if minutes < 1 { return "только что" }
    if minutes < 60 { return "\(minutes) мин назад" }
    if minutes < 24 * 60 { return "\(minutes / 60) ч назад" }
    return "\(minutes / (24 * 60)) дн назад"
}

/// Ближайшая четверть часа — по ней launchd запускает brain sync.
func nextRun(after now: Date = Date()) -> Date {
    let cal = Calendar.current
    let minute = cal.component(.minute, from: now)
    let hourStart = cal.date(bySettingHour: cal.component(.hour, from: now), minute: 0, second: 0, of: now)!
    return hourStart.addingTimeInterval(TimeInterval((minute / 15 + 1) * 15 * 60))
}

func plural(_ n: Int, _ one: String, _ few: String, _ many: String) -> String {
    let a = n % 100, b = n % 10
    if a > 10 && a < 20 { return many }
    if b == 1 { return one }
    if b > 1 && b < 5 { return few }
    return many
}

func shorten(_ text: String, _ limit: Int) -> String {
    let flat = text.split(whereSeparator: \.isNewline).joined(separator: " ")
    return flat.count <= limit ? flat : String(flat.prefix(limit - 1)) + "…"
}

@discardableResult
func run(_ path: String, _ args: [String], stdout: FileHandle? = nil) -> Int32 {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: path)
    process.arguments = args
    if let stdout = stdout { process.standardOutput = stdout }
    do { try process.run() } catch { return -1 }
    process.waitUntilExit()
    return process.terminationStatus
}

final class BrainBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var item: NSStatusItem?
    let menu = NSMenu()
    var status: [String: Any] = [:]
    var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let image = NSImage(systemSymbolName: "brain", accessibilityDescription: "Второй мозг") {
            image.isTemplate = true
            item.button?.image = image
            item.button?.imagePosition = .imageLeading
        }
        menu.delegate = self
        item.menu = menu
        self.item = item
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.refresh() }
    }

    // MARK: состояние

    func refresh() {
        if let data = FileManager.default.contents(atPath: statusPath),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            status = object
        } else {
            status = [:]
        }
        updateButton()
    }

    /// Состояние с поправкой на процесс, который умер посреди работы.
    var state: String {
        guard let raw = status["state"] as? String else { return "none" }
        if raw == "sync" || raw == "summarize", let pid = status["pid"] as? Int, kill(pid_t(pid), 0) != 0 {
            return "interrupted"
        }
        return raw
    }

    var progress: (done: Int, total: Int, current: String) {
        let p = status["progress"] as? [String: Any] ?? [:]
        return (p["done"] as? Int ?? 0, p["total"] as? Int ?? 0, p["current"] as? String ?? "")
    }

    var lastSync: Date? { parseDate(status["last_sync_at"]) }

    /// Три пропущенных интервала подряд — повод насторожиться (хотя Mac мог и спать).
    var stale: Bool { lastSync.map { Date().timeIntervalSince($0) > syncInterval * 3 } ?? false }

    var pending: Int { status["pending"] as? Int ?? 0 }

    func updateButton() {
        guard let button = item?.button else { return }
        let prefix = button.image == nil ? "🧠" : ""
        var suffix = ""
        var tip: String
        switch state {
        case "sync":
            suffix = " ↻"
            tip = "Второй мозг: забираю новые разговоры"
        case "summarize":
            let p = progress
            suffix = " \(min(p.done + 1, p.total))/\(p.total)"
            tip = "Второй мозг: пересказываю «\(shorten(p.current, 80))»"
        case "error", "interrupted":
            suffix = " !"
            tip = "Второй мозг: последний прогон не удался"
        case "none":
            suffix = " ?"
            tip = "Второй мозг: синхронизация ещё не запускалась"
        default:
            if stale {
                suffix = " !"
                tip = "Второй мозг: давно не было синхронизации"
            } else {
                tip = "Второй мозг: всё синхронизировано" + (pending > 0 ? ", без карточки ещё \(pending)" : "")
            }
        }
        button.title = prefix + suffix
        button.toolTip = tip
    }

    // MARK: меню

    func headline() -> String {
        switch state {
        case "sync": return "Забираю новые разговоры…"
        case "summarize":
            let p = progress
            return "Пересказываю \(min(p.done + 1, p.total)) из \(p.total)…"
        case "error": return "Последний прогон завершился ошибкой"
        case "interrupted": return "Последний прогон прервался"
        case "none": return "Синхронизация ещё не запускалась"
        default:
            return pending > 0
                ? "Работает · \(pending) \(plural(pending, "разговор", "разговора", "разговоров")) ещё без карточки"
                : "Работает · у всех разговоров есть карточки"
        }
    }

    func info(_ text: String, bold: Bool = false) {
        let mi = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        if bold {
            mi.attributedTitle = NSAttributedString(
                string: text, attributes: [.font: NSFont.boldSystemFont(ofSize: NSFont.systemFontSize)])
        }
        mi.isEnabled = false
        menu.addItem(mi)
    }

    @discardableResult
    func command(_ title: String, _ selector: Selector, enabled: Bool = true) -> NSMenuItem {
        let mi = NSMenuItem(title: title, action: enabled ? selector : nil, keyEquivalent: "")
        mi.target = self
        menu.addItem(mi)
        return mi
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        refresh()
        menu.removeAllItems()
        info(headline(), bold: true)
        if state == "summarize", !progress.current.isEmpty { info("«\(shorten(progress.current, 60))»") }
        if state == "error", let error = status["error"] as? String { info(shorten(error, 80)) }

        if let last = lastSync {
            menu.addItem(.separator())
            info("Последняя синхронизация в \(clockFormatter.string(from: last)), \(ago(last))")
            info("Следующая в \(clockFormatter.string(from: nextRun()))")
            if stale { info("⚠︎ Давно не синхронизировалось — загляни в лог") }
        }

        let sources = status["sources"] as? [String: [String: Any]] ?? [:]
        if !sources.isEmpty {
            menu.addItem(.separator())
            var total = 0
            for (key, name) in sourceOrder {
                guard let s = sources[key] else { continue }
                let records = s["records"] as? Int ?? 0
                total += records
                info("\(name): \(records), с карточкой \(s["summarized"] as? Int ?? 0)")
            }
            info("Всего \(total) · \(pending) без карточки")
            if pending > 0 {
                // Карточка — краткое содержание, ключевые слова и атомы от модели. Без неё ищется только сырой текст.
                let runs = (pending + batchLimit - 1) / batchLimit
                info(runs == 1
                     ? "Карточки сделает синхронизация в \(clockFormatter.string(from: nextRun()))"
                     : "Карточки сделают ближайшие \(runs) \(plural(runs, "синхронизация", "синхронизации", "синхронизаций"))")
                info("Пока они ищутся только по тексту разговора")
            }
        }
        for e in status["sync_errors"] as? [[String: Any]] ?? [] {
            info("⚠︎ \(e["source"] as? String ?? ""): \(shorten(e["error"] as? String ?? "", 70))")
        }

        let recent = status["recent"] as? [[String: Any]] ?? []
        if !recent.isEmpty {
            menu.addItem(.separator())
            let sub = NSMenu()
            for r in recent {
                let mi = NSMenuItem(title: shorten(r["title"] as? String ?? "?", 60),
                                    action: #selector(openRecord(_:)), keyEquivalent: "")
                mi.target = self
                mi.representedObject = r["id"]
                if let at = parseDate(r["at"]) { mi.toolTip = "пересказано \(ago(at))" }
                sub.addItem(mi)
            }
            let parent = NSMenuItem(title: "Последние карточки", action: nil, keyEquivalent: "")
            parent.submenu = sub
            menu.addItem(parent)
        }

        menu.addItem(.separator())
        command("Открыть базу", #selector(openDashboard))
        command("Синхронизировать сейчас", #selector(syncNow), enabled: state != "sync" && state != "summarize")
        command("Открыть лог", #selector(openLog))
        command("Открыть папку ~/brain", #selector(openFolder))
        command("Запускать при входе в систему", #selector(toggleLogin)).state =
            SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(.separator())
        command("Выйти", #selector(quit))
    }

    // MARK: действия

    @objc func syncNow() {
        run("/bin/launchctl", ["kickstart", "gui/\(getuid())/\(agentLabel)"])
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.refresh() }
    }

    /// Свежая HTML-панель базы (brain dashboard) в браузере по умолчанию.
    @objc func openDashboard() {
        run(brainDir + "/bin/brain", ["dashboard", "--open"])
    }

    @objc func openLog() {
        run("/usr/bin/open", ["-a", "Console", logPath])
    }

    @objc func openFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: brainDir))
    }

    /// Карточка и расшифровка — во временный файл, который откроется в текстовом редакторе.
    @objc func openRecord(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? Int else { return }
        let dir = NSTemporaryDirectory() + "brain-cards"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let path = "\(dir)/\(id).txt"
        FileManager.default.createFile(atPath: path, contents: nil, attributes: [.posixPermissions: 0o600])
        guard let out = FileHandle(forWritingAtPath: path) else { return }
        run(brainDir + "/bin/brain", ["show", String(id), "--chars", "1000000"], stdout: out)
        try? out.close()
        run("/usr/bin/open", ["-t", path])
    }

    @objc func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            let alert = NSAlert()
            alert.messageText = "Не получилось изменить автозапуск"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }

    @objc func quit() {
        NSApp.terminate(nil)
    }

    /// Текст меню без интерфейса — для проверки из терминала.
    func dump() {
        refresh()
        menuNeedsUpdate(menu)
        print("значок: [\(state)] \(headline())")
        for mi in menu.items {
            if mi.isSeparatorItem { print("—"); continue }
            print(mi.title + (mi.action == nil && !mi.hasSubmenu ? "" : "  ▸"))
            for sub in mi.submenu?.items ?? [] { print("    " + sub.title) }
        }
    }
}

let app = NSApplication.shared
let bar = BrainBar()
if CommandLine.arguments.contains("--dump") {
    bar.dump()
    exit(0)
}
app.delegate = bar
app.setActivationPolicy(.accessory)
app.run()
