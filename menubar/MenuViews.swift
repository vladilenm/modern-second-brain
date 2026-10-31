import AppKit

struct SourceSummary {
    let name: String
    let records: Int
    let cards: Int
    let error: String?
}

struct MenuSummary {
    let headline: String
    let symbol: String
    let color: NSColor
    let counts: String
    let timing: String
    let detail: String
    let fraction: Double?
}

/// Только информация. Все действия остаются обычными пунктами NSMenu:
/// со штатным выделением, клавиатурной навигацией и VoiceOver.
class MenuInfoView: NSView {
    override var isFlipped: Bool { true }

    @discardableResult
    func label(_ text: String, x: CGFloat, y: CGFloat, width: CGFloat,
               size: CGFloat = 12, weight: NSFont.Weight = .regular,
               color: NSColor = .labelColor, alignment: NSTextAlignment = .left) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.frame = NSRect(x: x, y: y, width: width, height: 20)
        field.font = .systemFont(ofSize: size, weight: weight)
        field.textColor = color
        field.alignment = alignment
        field.lineBreakMode = .byTruncatingTail
        field.toolTip = text
        addSubview(field)
        return field
    }

    func symbol(_ name: String, x: CGFloat, y: CGFloat, color: NSColor) {
        let image = NSImageView(frame: NSRect(x: x, y: y, width: 16, height: 16))
        image.image = NSImage(systemSymbolName: name, accessibilityDescription: nil)
        image.contentTintColor = color
        image.setAccessibilityElement(false) // Смысл значка уже написан рядом.
        addSubview(image)
    }
}

final class SummaryView: MenuInfoView {
    init(_ summary: MenuSummary) {
        super.init(frame: NSRect(x: 0, y: 0, width: 328, height: 128))
        update(summary)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(_ summary: MenuSummary) {
        subviews.forEach { $0.removeFromSuperview() }
        label("Второй мозг", x: 18, y: 10, width: 292, size: 14, weight: .semibold)
        symbol(summary.symbol, x: 18, y: 39, color: summary.color)
        label(summary.headline, x: 42, y: 36, width: 268, size: 13, weight: .medium)
        label(summary.counts, x: 18, y: 64, width: 292, size: 13)
        label(summary.timing, x: 18, y: 86, width: 292, size: 11, color: .secondaryLabelColor)

        var height: CGFloat = 116
        if !summary.detail.isEmpty {
            let detail = label(summary.detail, x: 18, y: 108, width: 292, size: 11,
                               color: .secondaryLabelColor)
            detail.maximumNumberOfLines = 2
            detail.lineBreakMode = .byWordWrapping
            detail.frame.size.height = 32
            height = 146
        }
        if let fraction = summary.fraction {
            let progress = NSProgressIndicator(frame: NSRect(x: 18, y: height - 4, width: 292, height: 6))
            progress.style = .bar
            progress.isIndeterminate = false
            progress.minValue = 0
            progress.maxValue = 1
            progress.doubleValue = max(0, min(1, fraction))
            progress.setAccessibilityLabel("Готовность карточек")
            addSubview(progress)
            height += 12
        }
        frame.size.height = height
    }
}

final class SourcesView: MenuInfoView {
    init(_ rows: [SourceSummary]) {
        super.init(frame: NSRect(x: 0, y: 0, width: 348, height: CGFloat(62 + rows.count * 28)))
        label("Источник", x: 18, y: 10, width: 160, size: 11, color: .secondaryLabelColor)
        label("Записи", x: 190, y: 10, width: 58, size: 11, color: .secondaryLabelColor, alignment: .right)
        label("Карточки", x: 260, y: 10, width: 70, size: 11, color: .secondaryLabelColor, alignment: .right)
        for (i, row) in rows.enumerated() {
            let y = CGFloat(36 + i * 28)
            let name = label(row.name, x: 18, y: y, width: 166, size: 13)
            name.toolTip = row.error ?? row.name
            if row.error != nil {
                symbol("exclamationmark.triangle.fill", x: 18, y: y + 2, color: .systemOrange)
                name.frame.origin.x = 40
                name.frame.size.width = 144
            }
            let records = label(String(row.records), x: 190, y: y, width: 58, size: 13, alignment: .right)
            records.font = .monospacedDigitSystemFont(ofSize: 13, weight: .medium)
            let cards = label(String(row.cards), x: 260, y: y, width: 70, size: 13,
                              color: row.cards < row.records ? .secondaryLabelColor : .labelColor,
                              alignment: .right)
            cards.font = .monospacedDigitSystemFont(ofSize: 13, weight: .regular)
        }
        label("Карточка — краткое содержание записи для поиска.", x: 18,
              y: CGFloat(38 + rows.count * 28), width: 312, size: 10, color: .secondaryLabelColor)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}
