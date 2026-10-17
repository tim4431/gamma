import Foundation

/// The embedded CPython 3.13 (Python.xcframework from Python-Apple-support,
/// docs/dev/ipad.md "The local server"), running Gamma's FastAPI backend
/// for the app's life. The C API comes in through the bridging header
/// (GammaIPad-Bridging-Header.h); the configuration follows the CPython iOS
/// guide (https://docs.python.org/3/using/ios.html) and the init-config API
/// (https://docs.python.org/3.13/c-api/init_config.html).
///
/// One interpreter, started once on a thread of its own and never
/// finalized: `gamma_ios.main()` (ipad/python/gamma_ios.py) runs uvicorn
/// under a supervisor and never returns. An interpreter cannot be started a
/// second time in one process, but the server inside it can: `restart`
/// asks the supervisor to run it again (after a failed start, or once iOS
/// reclaimed its socket), on the same port.
enum PythonRuntime {
    /// Where the build put Python in the app bundle (the run-script phase in
    /// ipad/project.yml and install_python): the standard library under
    /// python/lib, the backend under python/app, the wheels for this
    /// platform under python/app_packages.
    struct Layout: Equatable {
        let home: String
        let searchPaths: [String]

        init(resources: URL) {
            let home = resources.appendingPathComponent("python", isDirectory: true)
            let stdlib = home.appendingPathComponent("lib/python3.13", isDirectory: true)
            self.home = home.path
            searchPaths = [
                stdlib.path,
                stdlib.appendingPathComponent("lib-dynload", isDirectory: true).path,
                home.appendingPathComponent("app", isDirectory: true).path,
                home.appendingPathComponent("app_packages", isDirectory: true).path,
            ]
        }
    }

    /// The server's entry. Whatever fails, the traceback goes to the server's
    /// log by this code itself, since a failure before gamma_ios redirects
    /// the output (its own import, say) would otherwise go to a console no
    /// one sees. An uncaught SystemExit (uvicorn's way of saying it could
    /// not start) would end the process, which here is the whole app: it
    /// becomes an ordinary error, logged and reported like the rest.
    static let entry = """
    import os, sys, traceback
    try:
        import gamma_ios
        gamma_ios.main()
    except BaseException as error:
        text = traceback.format_exc()
        logs = os.path.join(os.environ.get("GAMMA_DATA_DIR", "."), "logs")
        try:
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, "server.log"), "a", encoding="utf-8", errors="backslashreplace") as log:
                log.write("\\n--- the server did not start ---\\n" + text)
        except OSError:
            pass
        sys.stderr.write(text)
        raise RuntimeError("the server exited") from None
    """

    /// The interpreter's thread needs more than a secondary thread's 512 KiB
    /// default: Python's own recursion and the server's imports run on it.
    static let stackSize = 16 << 20

    /// Read by the tests, which can start the server only when the app did not.
    @MainActor private(set) static var started = false

    /// Whether the interpreter is up and the entry still runs, read from any
    /// thread under `lock`: what `restart` needs to know. The entry's own end
    /// (an import that failed) leaves nothing to restart.
    private static let lock = NSLock()
    nonisolated(unsafe) private static var interpreterUp = false
    nonisolated(unsafe) private static var entryExited = false

    /// Asks the server to run again in the interpreter it has
    /// (`gamma_ios.restart`: a server still running is stopped, and a new one
    /// listens on the same port), from any thread but the interpreter's own.
    /// False when there is nothing to restart: the interpreter never came up,
    /// or the entry itself ended (an import error), which only a new build
    /// fixes. The call takes the GIL, so it waits for the server's thread to
    /// yield it, which uvicorn's loop does at every tick.
    nonisolated static func restart() -> Bool {
        lock.lock()
        let alive = interpreterUp && !entryExited
        lock.unlock()
        guard alive else { return false }
        let state = PyGILState_Ensure()
        defer { PyGILState_Release(state) }
        return PyRun_SimpleStringFlags("import gamma_ios\ngamma_ios.restart()\n", nil) == 0
    }

    /// Starts the interpreter with `environment` set first (gamma.config
    /// reads it at import). `onExit` hears why the server stopped, which it
    /// only does on a failure. False when it was already started.
    @MainActor
    static func start(environment: [String: String], layout: Layout, log: URL,
                      onExit: @escaping @MainActor @Sendable (String) -> Void) -> Bool {
        guard !started else { return false }
        started = true
        // Process-wide, and before any Python code runs: os.environ is a copy
        // taken when the interpreter starts.
        for (name, value) in environment { setenv(name, value, 1) }
        let thread = Thread {
            redirectOutput(to: log)
            let reason = run(layout)
            Task { @MainActor in onExit(reason) }
        }
        thread.name = "gamma-server"
        thread.stackSize = stackSize
        thread.start()
        return true
    }

    /// The process's standard output and error become the server's log,
    /// appended, before the interpreter prints anything: what Python says
    /// before gamma_ios redirects its streams (a failure to start, an import
    /// that fails, a traceback the entry could not write itself) would
    /// otherwise go to a console an iPad does not have. The app's own
    /// output is nothing it needs; the log is where a failed start is read.
    private static func redirectOutput(to log: URL) {
        let fd = open(log.path, O_WRONLY | O_APPEND | O_CREAT, 0o644)
        guard fd >= 0 else { return }
        dup2(fd, STDOUT_FILENO)
        dup2(fd, STDERR_FILENO)
        close(fd)
    }

    /// Configures and starts the interpreter, then runs the server. Returns
    /// only when something failed, with what to show.
    private static func run(_ layout: Layout) -> String {
        var preconfig = PyPreConfig()
        PyPreConfig_InitPythonConfig(&preconfig)
        // UTF-8 for files, the locale and the standard streams, whatever the
        // device's locale says.
        preconfig.utf8_mode = 1
        var status = Py_PreInitialize(&preconfig)
        if PyStatus_Exception(status) != 0 { return describe(status, doing: "prepare") }

        // On the heap, so the config and its fields are addressed through one
        // pointer (Swift forbids `&config` and `&config.home` in one call).
        let config = UnsafeMutablePointer<PyConfig>.allocate(capacity: 1)
        config.initialize(to: PyConfig())
        defer { config.deallocate() }
        PyConfig_InitPythonConfig(config)
        config.pointee.buffered_stdio = 0 // the log gets each line as it is written
        config.pointee.write_bytecode = 0 // the signed bundle is read-only
        config.pointee.install_signal_handlers = 0 // the process's signals are the app's, and this is not the main thread

        status = PyConfig_SetBytesString(config, &config.pointee.home, layout.home)
        if PyStatus_Exception(status) != 0 {
            PyConfig_Clear(config)
            return describe(status, doing: "find its home")
        }
        // Exactly these paths: nothing is searched outside the bundle.
        config.pointee.module_search_paths_set = 1
        for path in layout.searchPaths {
            guard let wide = Py_DecodeLocale(path, nil) else {
                PyConfig_Clear(config)
                return "Python could not read the path \(path)."
            }
            status = PyWideStringList_Append(&config.pointee.module_search_paths, wide)
            PyMem_RawFree(wide)
            if PyStatus_Exception(status) != 0 {
                PyConfig_Clear(config)
                return describe(status, doing: "set its paths")
            }
        }

        status = Py_InitializeFromConfig(config)
        PyConfig_Clear(config)
        if PyStatus_Exception(status) != 0 { return describe(status, doing: "start") }
        lock.lock()
        interpreterUp = true
        lock.unlock()

        // Blocks while the supervisor runs, which is for good unless the entry
        // itself fails. The traceback of a failure is in the log.
        let result = PyRun_SimpleStringFlags(entry, nil)
        lock.lock()
        entryExited = true
        lock.unlock()
        return result == 0 ? "The server stopped." : "The server stopped with an error."
    }

    private static func describe(_ status: PyStatus, doing step: String) -> String {
        let function = status.`func`.map { String(cString: $0) + ": " } ?? ""
        let message = status.err_msg.map { String(cString: $0) } ?? "exit code \(status.exitcode)"
        return "Python could not \(step): \(function)\(message)"
    }
}
