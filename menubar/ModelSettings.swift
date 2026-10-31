import AppKit

enum ModelSettingsError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        if case .message(let text) = self { return text }
        return nil
    }
}

/// Ключи передаются только через stdin; в ответе Python возвращает лишь безопасные поля.
enum ModelSettingsClient {
    static func requestSync(_ action: String, payload: [String: Any] = [:]) throws -> [String: Any] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: brainDir + "/bin/brain")
        process.arguments = ["model", action]
        let input = Pipe(), output = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        let data = try JSONSerialization.data(withJSONObject: payload)
        try process.run()
        try input.fileHandleForWriting.write(contentsOf: data)
        try input.fileHandleForWriting.close()
        // Читать до waitUntilExit: длинный список моделей может заполнить pipe.
        let response = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard let object = try? JSONSerialization.jsonObject(with: response) as? [String: Any] else {
            throw ModelSettingsError.message("Не удалось прочитать настройки. Проверь config.json.")
        }
        if let error = object["error"] as? String { throw ModelSettingsError.message(error) }
        guard process.terminationStatus == 0 else {
            throw ModelSettingsError.message("Не удалось обновить настройки.")
        }
        return object
    }

    static func request(_ action: String, payload: [String: Any] = [:],
                        completion: @escaping (Result<[String: Any], Error>) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Result { try requestSync(action, payload: payload) }
            DispatchQueue.main.async { completion(result) }
        }
    }
}

func processorTitle(_ data: [String: Any]) -> String {
    let model = data["model"] as? String ?? ""
    return model.isEmpty ? "Модель не настроена" : "\(data["provider"] as? String ?? "API") · \(model)"
}

final class ProcessorView: MenuInfoView {
    init(_ title: String, detail: String) {
        super.init(frame: NSRect(x: 0, y: 0, width: 348, height: 74))
        update(title, detail: detail)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    func update(_ title: String, detail: String) {
        subviews.forEach { $0.removeFromSuperview() }
        label("Обработка заметок", x: 18, y: 8, width: 312, size: 11, color: .secondaryLabelColor)
        label(title, x: 18, y: 27, width: 312, size: 13, weight: .medium)
        label(detail, x: 18, y: 48, width: 312, size: 11, color: .secondaryLabelColor)
    }
}

final class ModelSettingsWindow: NSWindowController, NSTextFieldDelegate, NSWindowDelegate {
    let provider = NSPopUpButton(frame: .zero, pullsDown: false)
    let endpoint = NSTextField()
    let key = NSSecureTextField()
    let model = NSComboBox()
    let keyHint = NSTextField(labelWithString: "")
    let feedback = NSTextField(wrappingLabelWithString: "Загружаю настройки…")
    let current = NSTextField(labelWithString: "")
    let jsonMode = NSButton(checkboxWithTitle: "Запрашивать ответ в формате JSON", target: nil, action: nil)
    let reload = NSButton(title: "Обновить", target: nil, action: nil)
    let saveButton = NSButton(title: "Сохранить", target: nil, action: nil)
    var providers: [[String: Any]] = []
    var generation = 0
    var saving = false
    var onSave: (([String: Any]) -> Void)?

    init() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 618),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Обработка заметок"
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.delegate = self
        let view = MenuInfoView(frame: NSRect(x: 0, y: 0, width: 560, height: 618))
        window.contentView = view
        view.symbol("sparkles", x: 28, y: 29, color: .controlAccentColor)
        view.label("Модель и провайдер", x: 56, y: 24, width: 470, size: 20, weight: .semibold).frame.size.height = 28
        current.frame = NSRect(x: 28, y: 62, width: 504, height: 20)
        current.font = .systemFont(ofSize: 12)
        current.textColor = .secondaryLabelColor
        current.lineBreakMode = .byTruncatingTail
        view.addSubview(current)

        view.label("Провайдер", x: 28, y: 108, width: 504, size: 12, weight: .medium)
        provider.frame = NSRect(x: 26, y: 132, width: 508, height: 28)
        provider.target = self; provider.action = #selector(providerChanged)
        provider.setAccessibilityLabel("Провайдер")
        view.addSubview(provider)

        view.label("Адрес API", x: 28, y: 174, width: 504, size: 12, weight: .medium)
        endpoint.frame = NSRect(x: 28, y: 198, width: 504, height: 25)
        endpoint.placeholderString = "https://api.example.com/v1"
        endpoint.setAccessibilityLabel("Адрес API")
        endpoint.delegate = self
        view.addSubview(endpoint)

        view.label("API-ключ", x: 28, y: 237, width: 504, size: 12, weight: .medium)
        key.frame = NSRect(x: 28, y: 261, width: 504, height: 25)
        key.setAccessibilityLabel("API-ключ")
        key.delegate = self
        view.addSubview(key)
        keyHint.frame = NSRect(x: 28, y: 291, width: 504, height: 18)
        keyHint.font = .systemFont(ofSize: 11)
        keyHint.textColor = .secondaryLabelColor
        view.addSubview(keyHint)

        view.label("Модель", x: 28, y: 328, width: 504, size: 12, weight: .medium)
        model.frame = NSRect(x: 28, y: 352, width: 402, height: 26)
        model.placeholderString = "Выбери из списка или введи название"
        model.completes = true
        model.numberOfVisibleItems = 12
        model.setAccessibilityLabel("Модель")
        view.addSubview(model)
        reload.frame = NSRect(x: 440, y: 349, width: 96, height: 32)
        reload.bezelStyle = .rounded
        reload.target = self; reload.action = #selector(loadModels)
        reload.toolTip = "Загрузить список моделей у выбранного провайдера"
        view.addSubview(reload)

        jsonMode.frame = NSRect(x: 26, y: 394, width: 508, height: 24)
        jsonMode.toolTip = "Отключи, если API не поддерживает response_format: json_object."
        view.addSubview(jsonMode)
        feedback.frame = NSRect(x: 28, y: 433, width: 504, height: 45)
        feedback.font = .systemFont(ofSize: 12)
        feedback.textColor = .secondaryLabelColor
        feedback.setAccessibilityElement(true)
        view.addSubview(feedback)
        let line = NSBox(frame: NSRect(x: 28, y: 493, width: 504, height: 1))
        line.boxType = .separator; view.addSubview(line)
        let note = NSTextField(wrappingLabelWithString:
            "Изменения применятся при следующем запуске. Готовые карточки сохранятся; текущая обработка продолжится на прежней модели.")
        note.frame = NSRect(x: 28, y: 510, width: 504, height: 38)
        note.font = .systemFont(ofSize: 11)
        note.textColor = .secondaryLabelColor
        view.addSubview(note)
        let cancel = NSButton(title: "Отмена", target: self, action: #selector(cancel))
        cancel.frame = NSRect(x: 320, y: 563, width: 100, height: 32)
        cancel.bezelStyle = .rounded; cancel.keyEquivalent = "\u{1b}"
        view.addSubview(cancel)
        saveButton.frame = NSRect(x: 424, y: 563, width: 112, height: 32)
        saveButton.bezelStyle = .rounded; saveButton.keyEquivalent = "\r"
        saveButton.target = self; saveButton.action = #selector(save)
        view.addSubview(saveButton)
        window.center()
        enable(false)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func open() {
        if window?.isVisible == true {
            NSApp.activate(ignoringOtherApps: true); window?.makeKeyAndOrderFront(nil)
            return
        }
        showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
        enable(false)
        message("Загружаю настройки…")
        generation += 1
        let token = generation
        ModelSettingsClient.request("show") { [weak self] result in
            guard let self = self, self.generation == token else { return }
            switch result {
            case .success(let data):
                self.populate(data)
                self.window?.makeFirstResponder(self.provider)
            case .failure(let error): self.message(error.localizedDescription, error: true)
            }
        }
    }

    func populate(_ data: [String: Any]) {
        providers = data["providers"] as? [[String: Any]] ?? []
        let active = data["current"] as? [String: Any] ?? [:]
        current.stringValue = "Сейчас: " + processorTitle(active)
        current.toolTip = current.stringValue
        provider.removeAllItems()
        // Адрес отличает несколько пользовательских API на одном хосте.
        provider.addItems(withTitles: providers.map {
            ($0["custom"] as? Bool ?? false) ? ($0["base_url"] as? String ?? "API") : ($0["name"] as? String ?? "API")
        } + ["Другой провайдер…"])
        let index = providers.firstIndex { ($0["base_url"] as? String) == (active["base_url"] as? String) }
        provider.selectItem(at: index ?? providers.count)
        enable(true)
        providerChanged()
    }

    var selected: [String: Any] {
        let index = provider.indexOfSelectedItem
        return providers.indices.contains(index) ? providers[index] : [:]
    }

    @objc func providerChanged() {
        generation += 1
        let choice = selected
        endpoint.stringValue = choice["base_url"] as? String ?? ""
        endpoint.isEditable = choice.isEmpty || (choice["custom"] as? Bool ?? false)
        key.stringValue = ""
        let saved = choice["has_api_key"] as? Bool ?? false
        key.placeholderString = saved ? "Оставь пустым, чтобы сохранить текущий ключ" : "Вставь API-ключ"
        keyHint.stringValue = (choice["key_from_env"] as? Bool ?? false)
            ? "Используется ключ из окружения. Новый ключ заменит его для этого провайдера."
            : (saved ? "Ключ сохранён для этого провайдера." : "Ключ хранится локально в config.json.")
        model.removeAllItems()
        model.stringValue = choice["model"] as? String ?? ""
        jsonMode.state = (choice["json_mode"] as? Bool ?? true) ? .on : .off
        reload.isEnabled = true
        if !choice.isEmpty && (saved || !(choice["requires_key"] as? Bool ?? false)) {
            loadModels()
        } else {
            message("Укажи настройки, затем обнови список или введи модель вручную.")
        }
    }

    func controlTextDidChange(_ notification: Notification) {
        // Ответ на запрос со старым адресом/ключом больше не относится к этим полям.
        generation += 1
        reload.isEnabled = true
        let typed = model.stringValue
        model.removeAllItems()
        model.stringValue = typed
        if notification.object as? NSTextField === endpoint {
            let original = selected["base_url"] as? String ?? ""
            if endpoint.stringValue.trimmingCharacters(in: CharacterSet(charactersIn: "/ ")) != original {
                keyHint.stringValue = "Для другого адреса используется отдельный ключ."
                key.placeholderString = "Вставь API-ключ для этого адреса, если он требуется"
            }
        }
        message("Обнови список моделей для новых настроек или введи название вручную.")
    }

    func payload() -> [String: Any] {
        var data: [String: Any] = ["base_url": endpoint.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
                                   "model": model.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
                                   "json_mode": jsonMode.state == .on]
        if !key.stringValue.isEmpty { data["api_key"] = key.stringValue }
        return data
    }

    @objc func loadModels() {
        guard !saving else { return }
        generation += 1
        let token = generation
        reload.isEnabled = false
        message("Загружаю доступные модели…")
        ModelSettingsClient.request("models", payload: payload()) { [weak self] result in
            guard let self = self, self.generation == token else { return }
            self.reload.isEnabled = true
            switch result {
            case .success(let data):
                let ids = data["models"] as? [String] ?? []
                let typed = self.model.stringValue
                self.model.removeAllItems()
                self.model.addItems(withObjectValues: ids)
                self.model.stringValue = typed
                self.message(ids.isEmpty ? "Список пуст. Введи название модели вручную."
                             : "Доступно моделей: \(ids.count). Можно выбрать из списка или ввести название.")
            case .failure(let error): self.message(error.localizedDescription, error: true)
            }
        }
    }

    @objc func save() {
        guard !saving && saveButton.isEnabled else { return }
        guard !model.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            message("Выбери модель из списка или введи её название.", error: true)
            window?.makeFirstResponder(model)
            return
        }
        generation += 1
        let token = generation
        saving = true
        enable(false)
        message("Сохраняю настройки…")
        ModelSettingsClient.request("save", payload: payload()) { [weak self] result in
            guard let self = self else { return }
            self.saving = false
            if case .success(let data) = result { self.onSave?(data) }
            guard self.generation == token else { return }
            self.enable(true)
            switch result {
            case .success: self.key.stringValue = ""; self.close()
            case .failure(let error): self.message(error.localizedDescription, error: true)
            }
        }
    }

    @objc func cancel() {
        guard !saving else { return }
        generation += 1
        key.stringValue = ""
        close()
    }

    func windowWillClose(_ notification: Notification) {
        generation += 1
        key.stringValue = ""
    }

    func enable(_ enabled: Bool) {
        [provider, endpoint, key, model, jsonMode, reload, saveButton].forEach { $0.isEnabled = enabled }
        window?.standardWindowButton(.closeButton)?.isEnabled = !saving
    }

    func message(_ text: String, error: Bool = false) {
        feedback.stringValue = text
        feedback.textColor = error ? .systemRed : .secondaryLabelColor
        feedback.toolTip = text
    }
}
