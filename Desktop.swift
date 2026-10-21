import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var origin: URL?
    var runtime: String = ""
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let appItem = NSMenuItem(); menu.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "TernPilot 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); menu.addItem(editItem); editItem.submenu = NSMenu(title: "편집")
        for (title, action, key) in [("실행 취소", "undo:", "z"), ("잘라내기", "cut:", "x"), ("복사", "copy:", "c"), ("붙여넣기", "paste:", "v"), ("모두 선택", "selectAll:", "a")] {
            editItem.submenu?.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let viewItem = NSMenuItem(); menu.addItem(viewItem); viewItem.submenu = NSMenu(title: "보기")
        let reload = viewItem.submenu!.addItem(withTitle: "다시 연결", action: #selector(connect), keyEquivalent: "r"); reload.target = self
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 820), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "TernPilot"; window.minSize = NSSize(width: 720, height: 560)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(calibratedRed: 0.098, green: 0.106, blue: 0.122, alpha: 1)
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self; web.uiDelegate = self
        window.contentView = web; window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        guard let file = Bundle.main.url(forResource: "runtime-path", withExtension: "txt"), let path = try? String(contentsOf: file, encoding: .utf8) else { fail("설치 경로를 찾을 수 없습니다."); return }
        runtime = path.trimmingCharacters(in: .whitespacesAndNewlines)
        connect()
    }
    @objc func connect() {
        var args = [runtime + "/desktop.py"]
        if !FileManager.default.fileExists(atPath: runtime + "/chat-config.json") {
            let picker = NSOpenPanel(); picker.title = "TernPilot에서 사용할 기존 Git 프로젝트 선택"
            picker.canChooseFiles = false; picker.canChooseDirectories = true; picker.allowsMultipleSelection = false
            guard picker.runModal() == .OK, let folder = picker.url else { return }
            args += ["--project", folder.path]
        }
        web.loadHTMLString("<body style='background:#191b1f;color:#b7e6cf;font:18px system-ui;padding:70px'>TernPilot에 연결하고 있습니다…</body>", baseURL: nil)
        let taskArgs = args
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process(); process.executableURL = URL(fileURLWithPath: "/usr/bin/python3"); process.arguments = taskArgs
            let pipe = Pipe(); process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = NSHomeDirectory() + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            process.environment = env
            do { try process.run() } catch { DispatchQueue.main.async { self.fail("Python을 실행하지 못했습니다. macOS 개발자 도구 설치를 확인하세요.") }; return }
            let data = pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
            let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async {
                guard process.terminationStatus == 0, let url = URL(string: output), url.scheme == "http", url.host == "127.0.0.1", url.port != nil, url.path == "/", (url.fragment?.count ?? 0) >= 32 else { self.fail("로컬 서버를 시작하지 못했습니다. 프로젝트 설정과 chat-server.log를 확인한 뒤 보기 → 다시 연결을 선택하세요."); return }
                self.origin = url; self.web.load(URLRequest(url: url))
            }
        }
    }
    func fail(_ message: String) { let alert = NSAlert(); alert.messageText = "TernPilot 연결 확인"; alert.informativeText = message; alert.runModal() }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.absoluteString == "about:blank" { decisionHandler(.allow); return }
        if let base = origin, url.scheme == base.scheme, url.host == base.host, url.port == base.port, url.path == "/" { decisionHandler(.allow); return }
        if navigationAction.navigationType == .linkActivated && ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? { return nil }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { if !flag { window.makeKeyAndOrderFront(nil); connect() }; return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { return false }
}
let app = NSApplication.shared
let delegate = AppDelegate(); app.delegate = delegate
app.run()
