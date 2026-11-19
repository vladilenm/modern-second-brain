import AppKit

let brainDir = ""

@main enum ModelSettingsLayoutTest {
    static func main() {
        _ = NSApplication.shared
        let controller = ModelSettingsWindow()
        let window = controller.window!
        controller.layoutForProvider(isCLI: true)
        precondition(window.contentView!.frame.height == 409)
        let screen = window.screen ?? NSScreen.main!
        window.setFrameOrigin(NSPoint(x: window.frame.minX, y: screen.visibleFrame.minY + 8))
        controller.layoutForProvider(isCLI: false)
        precondition(window.contentView!.frame.height == 618)
        let content = window.contentView!
        for button in [controller.saveButton, controller.cancelButton!] {
            let rect = window.convertToScreen(content.convert(button.frame, to: nil))
            precondition(screen.visibleFrame.contains(rect), "Action button left the visible screen: \(rect)")
        }
        controller.layoutForProvider(isCLI: true)
        precondition(window.contentView!.frame.height == 409)
    }
}
