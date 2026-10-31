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
let builtinSources: [(key: String, name: String)] = [("claude_code", "Claude Code"), ("codex", "Codex"), ("chatgpt", "ChatGPT")]

/// Список источников пишет brain в status.json (sources_meta); встроенный — на случай старого файла.
func sourceOrder(_ status: [String: Any]) -> [(key: String, name: String)] {
    guard let meta = status["sources_meta"] as? [[String: Any]], !meta.isEmpty else { return builtinSources }
    return meta.compactMap { m -> (key: String, name: String)? in
        guard let key = m["source"] as? String else { return nil }
        return (key: key, name: m["title"] as? String ?? key)
    }
}

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
    var summaryView: SummaryView?
    var syncItem: NSMenuItem?
    var menuIsOpen = false
    var startingSync = false
    var syncRequestedAt: Date?
    var syncBaseline: String?
    var actionError: String?
    var inputPath = statusPath
    var modelSettings: [String: Any] = [:]
    var modelSettingsError: String?
    var loadingModelSettings = false
    var processorView: ProcessorView?
    var modelWindow: ModelSettingsWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let image = NSImage(systemSymbolName: "brain", accessibilityDescription: "Второй мозг") {
            image.isTemplate = true
            item.button?.image = image
            item.button?.imagePosition = .imageLeading
        }
        menu.delegate = self
        menu.autoenablesItems = false
        item.menu = menu
        self.item = item
        refresh()
        loadModelSettings()
        installEditingMenu()
        let timer = Timer(timeInterval: 2, repeats: true) { [weak self] _ in self?.refresh() }
        RunLoop.main.add(timer, forMode: .common) // Продолжать обновлять прогресс и при открытом меню.
        self.timer = timer
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openModelSettings()
        return true
    }

    /// Стандартные команды редактирования для полей окна настроек, включая вставку ключа.
    func installEditingMenu() {
        let main = NSMenu()
        let appMenu = NSMenu()
        let appItem = NSMenuItem(); appItem.submenu = appMenu; main.addItem(appItem)
        command("Модель и провайдер…", #selector(openModelSettings), icon: "sparkles", key: ",", in: appMenu)
        command("Завершить BrainBar", #selector(quit), icon: "power", key: "q", in: appMenu)
        let edit = NSMenu(title: "Правка")
        let editItem = NSMenuItem(); editItem.submenu = edit; main.addItem(editItem)
        for (title, action, key) in [("Вырезать", "cut:", "x"), ("Копировать", "copy:", "c"),
                                     ("Вставить", "paste:", "v"), ("Выбрать всё", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: NSSelectorFromString(action), keyEquivalent: key)
        }
        NSApp.mainMenu = main
    }

    // MARK: состояние

    func refresh() {
        if let data = FileManager.default.contents(atPath: inputPath),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            status = object
        } else {
            status = [:]
        }
        if startingSync {
            if state == "sync" || state == "summarize" || (status["last_sync_at"] as? String) != syncBaseline {
                startingSync = false
                syncRequestedAt = nil
            } else if let requested = syncRequestedAt, Date().timeIntervalSince(requested) > 15 {
                startingSync = false
                syncRequestedAt = nil
                actionError = "Синхронизация не началась"
            }
        }
        updateButton()
        if menuIsOpen {
            summaryView?.update(summary())
            syncItem?.isEnabled = !busy
            syncItem?.title = syncTitle
            updateProcessorView()
        }
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
    var busy: Bool { startingSync || state == "sync" || state == "summarize" }
    var syncErrors: [[String: Any]] { status["sync_errors"] as? [[String: Any]] ?? [] }
    var needsAttention: Bool { actionError != nil || state == "error" || state == "interrupted" || stale || !syncErrors.isEmpty }

    func sourceName(_ key: String) -> String {
        let meta = status["sources_meta"] as? [[String: Any]] ?? []
        return meta.first { ($0["source"] as? String) == key || ($0["connector"] as? String) == key }?["title"] as? String
            ?? sourceOrder(status).first { $0.key == key }?.name ?? key
    }

    var sources: [SourceSummary] {
        let data = status["sources"] as? [String: [String: Any]] ?? [:]
        let order = sourceOrder(status).map { $0.key }
        let keys = order.filter { data[$0] != nil } + data.keys.filter { !order.contains($0) }.sorted()
        return keys.map { key in
            let s = data[key] ?? [:]
            let name = sourceName(key)
            let error = syncErrors.first { sourceName($0["source"] as? String ?? "") == name }?["error"] as? String
            return SourceSummary(name: name, records: s["records"] as? Int ?? 0,
                                 cards: s["summarized"] as? Int ?? 0, error: error)
        }
    }

    var syncTitle: String {
        if startingSync { return "Запускаю синхронизацию…" }
        if busy { return "Синхронизация идёт…" }
        return "Синхронизировать сейчас"
    }

    func updateButton() {
        guard let button = item?.button else { return }
        let prefix = button.image == nil ? "🧠" : ""
        var suffix = ""
        var tip: String
        switch startingSync ? "sync" : state {
        case "sync":
            suffix = " ↻"
            tip = "Второй мозг: забираю новые разговоры"
        case "summarize":
            let p = progress
            suffix = p.total > 0 ? " \(p.done)/\(p.total)" : " ↻"
            tip = "Второй мозг: пересказываю «\(shorten(p.current, 80))»"
        case "error", "interrupted":
            suffix = " !"
            tip = "Второй мозг: последний прогон не удался"
        case "none":
            suffix = " ?"
            tip = "Второй мозг: синхронизация ещё не запускалась"
        default:
            if needsAttention {
                suffix = " !"
                tip = "Второй мозг: \(headline())"
            } else {
                tip = "Второй мозг: всё синхронизировано" + (pending > 0 ? ", без карточки ещё \(pending)" : "")
            }
        }
        if actionError != nil {
            suffix = " !"
            tip = "Второй мозг: \(headline())"
        }
        button.title = prefix + suffix
        button.toolTip = tip
    }

    // MARK: меню

    func headline() -> String {
        if let error = actionError { return error }
        if startingSync { return "Запускаю синхронизацию…" }
        switch state {
        case "sync": return "Обновляю базу…"
        case "summarize":
            let p = progress
            return p.total > 0 ? "Создаю карточки · \(p.done) из \(p.total)" : "Создаю карточки…"
        case "error": return "Не удалось обновить базу"
        case "interrupted": return "Синхронизация прервалась"
        case "none": return "Готов к первой синхронизации"
        default:
            if !syncErrors.isEmpty { return "Есть ошибки синхронизации" }
            if stale { return "Давно не обновлялось" }
            return pending > 0 ? "Карточки ожидают обработки" : "Всё синхронизировано"
        }
    }

    func summary() -> MenuSummary {
        let total = sources.reduce(0) { $0 + $1.records }
        let counts = "\(total) \(plural(total, "запись", "записи", "записей")) · \(sources.count) \(plural(sources.count, "источник", "источника", "источников"))"
        let timing = lastSync.map { "Запуск в \(clockFormatter.string(from: $0)) · Следующий в \(clockFormatter.string(from: nextRun()))" }
            ?? "Автоматически каждые 15 минут"
        var detail = ""
        if actionError != nil || state == "error" || state == "interrupted" {
            detail = "Попробуй синхронизировать ещё раз. Подробности — в журнале."
        } else if state == "summarize" {
            detail = progress.current.isEmpty ? "Готовлю краткое содержание для поиска." : "«\(shorten(progress.current, 90))»"
        } else if !syncErrors.isEmpty {
            detail = syncErrors.map { sourceName($0["source"] as? String ?? "") }.joined(separator: ", ")
                + ". Подробности — в журнале."
        } else if stale {
            detail = "Последний запуск \(lastSync.map(ago) ?? "давно"). Можно обновить вручную."
        } else if pending > 0 {
            detail = "В очереди \(pending) \(plural(pending, "карточка", "карточки", "карточек")). Записи уже доступны в поиске."
        }
        let appearance: (symbol: String, color: NSColor)
        if actionError != nil {
            appearance = ("exclamationmark.triangle.fill", .systemOrange)
        } else if busy {
            appearance = ("arrow.triangle.2.circlepath", .controlAccentColor)
        } else if needsAttention {
            appearance = ("exclamationmark.triangle.fill", .systemOrange)
        } else if state == "none" || pending > 0 {
            appearance = ("clock", .secondaryLabelColor)
        } else {
            appearance = ("checkmark.circle.fill", .systemGreen)
        }
        return MenuSummary(headline: headline(), symbol: appearance.symbol, color: appearance.color,
                           counts: counts, timing: timing, detail: detail,
                           fraction: state == "summarize" && progress.total > 0 ? Double(progress.done) / Double(progress.total) : nil)
    }

    @discardableResult
    func command(_ title: String, _ selector: Selector, icon: String, key: String = "",
                 enabled: Bool = true, in target: NSMenu? = nil) -> NSMenuItem {
        let mi = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        mi.target = self
        mi.isEnabled = enabled
        mi.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil)
        mi.image?.size = NSSize(width: 16, height: 16)
        (target ?? menu).addItem(mi)
        return mi
    }

    func submenu(_ title: String, icon: String) -> NSMenu {
        let child = NSMenu(title: title)
        child.autoenablesItems = false
        let parent = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        parent.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil)
        parent.image?.size = NSSize(width: 16, height: 16)
        parent.submenu = child
        menu.addItem(parent)
        return child
    }

    func menuWillOpen(_ menu: NSMenu) { menuIsOpen = true; loadModelSettings() }
    func menuDidClose(_ menu: NSMenu) { menuIsOpen = false }

    func menuNeedsUpdate(_ target: NSMenu) {
        refresh()
        menu.removeAllItems()
        let header = NSMenuItem(title: "Второй мозг", action: nil, keyEquivalent: "")
        let view = SummaryView(summary())
        header.view = view
        summaryView = view
        menu.addItem(header)
        menu.addItem(.separator())
        command("Открыть базу", #selector(openDashboard), icon: "rectangle.grid.2x2", key: "o")
        syncItem = command(syncTitle, #selector(syncNow), icon: "arrow.triangle.2.circlepath", key: "r", enabled: !busy)

        let recent = status["recent"] as? [[String: Any]] ?? []
        if !recent.isEmpty {
            let sub = submenu("Последние карточки", icon: "clock.arrow.circlepath")
            for r in recent {
                let mi = command(shorten(r["title"] as? String ?? "Без названия", 48),
                                 #selector(openRecord(_:)), icon: "doc.text", in: sub)
                mi.representedObject = r["id"]
                let source = sourceName(r["source"] as? String ?? "")
                mi.toolTip = (r["title"] as? String ?? "") + "\n" + source
                    + (parseDate(r["at"]).map { " · \(ago($0))" } ?? "")
            }
        }
        if needsAttention { command("Открыть журнал ошибок", #selector(openLog), icon: "exclamationmark.bubble") }
        menu.addItem(.separator())
        if !sources.isEmpty {
            let sub = submenu("Источники · \(sources.count)", icon: "square.stack.3d.up")
            let table = NSMenuItem(title: "Статистика источников", action: nil, keyEquivalent: "")
            table.view = SourcesView(sources)
            sub.addItem(table)
        }
        let settings = submenu("Настройки", icon: "gearshape")
        let processor = NSMenuItem(title: processorSummary.title, action: nil, keyEquivalent: "")
        let processorView = ProcessorView(processorSummary.title, detail: processorSummary.detail)
        processor.view = processorView
        self.processorView = processorView
        settings.addItem(processor)
        command("Модель и провайдер…", #selector(openModelSettings), icon: "sparkles", key: ",", in: settings)
        settings.addItem(.separator())
        command("Запускать при входе", #selector(toggleLogin), icon: "person.crop.circle", in: settings).state =
            SMAppService.mainApp.status == .enabled ? .on : .off
        settings.addItem(.separator())
        command("Открыть журнал", #selector(openLog), icon: "doc.plaintext", in: settings)
        command("Открыть папку проекта", #selector(openFolder), icon: "folder", in: settings)
        menu.addItem(.separator())
        command("Завершить BrainBar", #selector(quit), icon: "power", key: "q").toolTip =
            "Синхронизация продолжит работать по расписанию."
    }

    // MARK: действия

    var processorSummary: (title: String, detail: String) {
        let chosen = modelSettings["current"] as? [String: Any] ?? [:]
        if busy, let active = status["processor"] as? [String: Any], !(active["model"] as? String ?? "").isEmpty {
            let next = processorTitle(chosen)
            return (processorTitle(active), chosen.isEmpty || next == processorTitle(active)
                    ? "Обрабатывает текущий запуск" : "Следующий запуск: " + next)
        }
        if let error = modelSettingsError { return ("Не удалось прочитать настройки", error) }
        return chosen.isEmpty ? ("Загружаю настройки…", "Провайдер и модель для новых карточек")
            : (processorTitle(chosen), "Для новых карточек")
    }

    func updateProcessorView() {
        processorView?.update(processorSummary.title, detail: processorSummary.detail)
    }

    func loadModelSettings() {
        guard !loadingModelSettings else { return }
        loadingModelSettings = true
        ModelSettingsClient.request("show") { [weak self] result in
            guard let self = self else { return }
            self.loadingModelSettings = false
            switch result {
            case .success(let data): self.modelSettings = data; self.modelSettingsError = nil
            case .failure(let error): self.modelSettingsError = error.localizedDescription
            }
            self.updateProcessorView()
        }
    }

    @objc func openModelSettings() {
        if modelWindow == nil {
            modelWindow = ModelSettingsWindow()
            modelWindow?.onSave = { [weak self] data in
                self?.modelSettings = data
                self?.modelSettingsError = nil
                self?.updateProcessorView()
            }
        }
        modelWindow?.open()
    }

    @objc func syncNow() {
        guard !busy else { return }
        startingSync = true
        syncRequestedAt = Date()
        syncBaseline = status["last_sync_at"] as? String
        actionError = nil
        updateButton()
        DispatchQueue.global(qos: .userInitiated).async {
            let code = run("/bin/launchctl", ["kickstart", "gui/\(getuid())/\(agentLabel)"])
            DispatchQueue.main.async {
                if code != 0 {
                    self.startingSync = false
                    self.syncRequestedAt = nil
                    self.actionError = "Не удалось запустить синхронизацию"
                    self.refresh()
                    return
                }
                // refresh снимет состояние запуска, когда Python обновит status.json.
                self.refresh()
            }
        }
    }

    /// Свежая HTML-панель базы (brain dashboard) в браузере по умолчанию.
    @objc func openDashboard() {
        actionError = nil
        DispatchQueue.global(qos: .userInitiated).async {
            let code = run(brainDir + "/bin/brain", ["dashboard", "--open"])
            if code != 0 {
                DispatchQueue.main.async {
                    self.actionError = "Не удалось открыть базу"
                    self.refresh()
                }
            }
        }
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
        DispatchQueue.global(qos: .userInitiated).async {
            guard let out = FileHandle(forWritingAtPath: path) else { return }
            let code = run(brainDir + "/bin/brain", ["show", String(id), "--chars", "1000000"], stdout: out)
            try? out.close()
            if code == 0 { run("/usr/bin/open", ["-t", path]) }
        }
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
        do { modelSettings = try ModelSettingsClient.requestSync("show") }
        catch { modelSettingsError = error.localizedDescription }
        refresh()
        menuNeedsUpdate(menu)
        print("значок: [\(state)] \(headline())")
        print("предупреждение: \(needsAttention)")
        let s = summary()
        print("\(s.counts)\n\(s.timing)")
        if !s.detail.isEmpty { print(s.detail) }
        if let fraction = s.fraction { print("прогресс: \(Int(fraction * 100))%") }
        print("модель: \(processorSummary.title)\n\(processorSummary.detail)")
        for mi in menu.items {
            if mi.isSeparatorItem { print("—"); continue }
            print(mi.title + (!mi.isEnabled ? " [недоступно]" : "") + (mi.hasSubmenu ? "  ▸" : ""))
            for sub in mi.submenu?.items ?? [] { print("    " + sub.title) }
        }
        for source in sources { print("    \(source.name): \(source.records) записей, \(source.cards) карточек") }
    }
}

@main
struct BrainBarMain {
    static func main() {
        let app = NSApplication.shared
        let bar = BrainBar()
        if CommandLine.arguments.contains("--dump") {
            if let i = CommandLine.arguments.firstIndex(of: "--status"), i + 1 < CommandLine.arguments.count {
                bar.inputPath = CommandLine.arguments[i + 1]
            }
            bar.dump()
            return
        }
        app.delegate = bar
        app.setActivationPolicy(.accessory)
        withExtendedLifetime(bar) { app.run() } // NSApplication не удерживает delegate.
    }
}
