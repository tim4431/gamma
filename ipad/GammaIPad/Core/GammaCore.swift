import Foundation
import JavaScriptCore

/// The web app's own JavaScript — ink codec and geometry, notebook rules,
/// the replica's sync rounds and edits (ipad/core/entry.js, bundled as
/// Resources/gamma-core.js) — in a JavaScriptCore context of its own, on a
/// queue of its own. Everything crosses as JSON text.
///
/// Two of these run: the sync core (rounds, which wait on the network) and
/// the edit core (the editors' calls), so drawing never waits for a round.
final class GammaCore {
    struct ScriptError: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    private let queue: DispatchQueue
    private let context: JSContext
    private let api: JSValue
    private var failure: String?

    static func bundledSource() throws -> String {
        guard let url = Bundle.main.url(forResource: "gamma-core", withExtension: "js") else {
            throw ScriptError(message: "gamma-core.js is not in the app (run ipad/scripts/build-core.mjs)")
        }
        return try String(contentsOf: url, encoding: .utf8)
    }

    init(name: String, source: String) throws {
        queue = DispatchQueue(label: "net.gammapdf.ipad.core.\(name)", qos: name == "sync" ? .utility : .userInitiated)
        guard let vm = JSVirtualMachine(), let context = JSContext(virtualMachine: vm) else {
            throw ScriptError(message: "JavaScriptCore is unavailable")
        }
        self.context = context
        context.name = "Gamma \(name)"
        var loadError: String?
        context.exceptionHandler = { _, exception in loadError = exception?.toString() }
        let log: @convention(block) (String) -> Void = { NSLog("[gamma-core] %@", $0) }
        context.setObject(log, forKeyedSubscript: "__gammaLog" as NSString)
        context.evaluateScript(source, withSourceURL: URL(string: "gamma-core.js"))
        if let loadError { throw ScriptError(message: "gamma-core.js: \(loadError)") }
        guard let api = context.objectForKeyedSubscript("GammaCore"), api.isObject else {
            throw ScriptError(message: "gamma-core.js defines no GammaCore")
        }
        self.api = api
        context.exceptionHandler = { [weak self] _, exception in self?.failure = exception?.toString() ?? "error" }
    }

    /// A synchronous helper (ink geometry, paper, views): JSON in, JSON out.
    func pure(_ name: String, _ args: [Any?]) throws -> Any? {
        let argsJSON = try JSON.string(args)
        return try queue.sync { () throws -> Any? in
            failure = nil
            let out = api.invokeMethod("pure", withArguments: [name, argsJSON])
            if let failure { throw ScriptError(message: failure) }
            return try JSON.parse(out?.toString() ?? "null")
        }
    }

    /// An async core function over the device's host. Every host method
    /// answers synchronously (`host` runs on this core's queue and may wait
    /// on the network), so the function has settled once the call returns:
    /// JavaScriptCore runs pending promise jobs as its outermost call ends.
    func run(_ name: String, _ args: [Any?], host: @escaping (String, String) -> String) throws -> Any? {
        let argsJSON = try JSON.string(args)
        return try queue.sync { () throws -> Any? in
            failure = nil
            let invoke: @convention(block) (String, String) -> String = { method, args in host(method, args) }
            guard let native = JSValue(newObjectIn: context) else { throw ScriptError(message: "no host object") }
            native.setObject(invoke, forKeyedSubscript: "invoke" as NSString)
            guard let slot = api.invokeMethod("run", withArguments: [name, argsJSON, native]) else {
                throw ScriptError(message: failure ?? "\(name) did not start")
            }
            var tries = 0
            while !slot.forProperty("done").toBool() && tries < 8 {
                context.evaluateScript("0") // one more outermost call: its end runs what is still queued
                tries += 1
            }
            guard slot.forProperty("done").toBool() else { throw ScriptError(message: "\(name) did not finish") }
            if let error = slot.forProperty("error"), !error.isNull, !error.isUndefined {
                throw ScriptError(message: error.toString())
            }
            return try JSON.parse(slot.forProperty("value")?.toString() ?? "null")
        }
    }
}
