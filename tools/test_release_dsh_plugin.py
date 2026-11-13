import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from release_plugins import release

CODE = "abcdefghijklmnopqrstuvwxyz012345"
DEVICE = "devicecode_abcdefghijklmnopqrstu"
SERVER = "http://localhost:9001/mcp"
API = "http://localhost:9001/api/integrations"
# What a sign-in that waits once and is then allowed leaves in the log.
SIGN_IN = [f"device|{API}/device", "open|http://localhost:9001/?gamma_device=BCDF-GHJK",
           f"poll|{API}/device/token|{DEVICE}", f"poll|{API}/device/token|{DEVICE}"]
POWERSHELL = os.name == "nt" and not os.environ.get("GAMMA_TEST_POSIX_SHELL")


class DshInstallerTest(unittest.TestCase):
    def test_release_pins_version_download_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = release(Path(tmp) / "release", "1.2.3", "example/Gamma")
            bundle = next(p for p in assets if p.name == "dsh-gamma-1.2.3.tgz")
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            for name in ("install-gamma-dsh.ps1", "install-gamma-dsh.sh"):
                text = next(p for p in assets if p.name == name).read_text()
                self.assertIn("https://github.com/example/Gamma/releases/download/v1.2.3/dsh-gamma-1.2.3.tgz", text)
                self.assertIn(digest, text)
                self.assertIn("'1.2.3'", text)
                self.assertNotIn("__GAMMA_", text)
                self.assertTrue(text.isascii(), f"{name} is read by Windows PowerShell without a charset")
            checksums = next(p for p in assets if p.name == "gamma-dsh-SHA256SUMS.txt")
            names = [line.split("  ")[1] for line in checksums.read_text().splitlines()]
            self.assertEqual(names, ["dsh-gamma-1.2.3.tgz", "dsh-gamma.tgz", "install-gamma-dsh.ps1", "install-gamma-dsh.sh"])

    def run_installer(self, root, assets, *, code=None, bad_download=False, fail_dsh=False, fail_claim=False,
                      desktop=True, remove=False):
        """Exercise the real script; substitute only downloads, Gamma's replies (a
        sign-in that waits once, then is allowed; an older Gamma's claim of
        ``code``), the browser and the dsh CLI. ``fail_claim``: the sign-in is
        cancelled, the code refused."""
        log = root / "calls.txt"
        for done in (log, root / "calls.txt.polled"):
            if done.exists():
                done.unlink()
        home = root / "dsh home"
        profile = "desktop" if POWERSHELL else "web"
        manifest = home / "profiles" / profile / "package.json"
        if desktop and not manifest.exists():
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}")
        args = ["--remove"] if remove else [SERVER, *([code] if code else [])]
        bundle = next(p for p in assets if p.name.startswith("dsh-gamma-"))
        env = {**os.environ, "DSH_HOME": str(home), "GAMMA_TEST_BUNDLE": str(bundle), "GAMMA_TEST_LOG": str(log),
               "GAMMA_TEST_BAD": "1" if bad_download else "", "GAMMA_TEST_DSH_FAIL": "1" if fail_dsh else "",
               "GAMMA_TEST_CLAIM_FAIL": "1" if fail_claim else "", "GAMMA_TEST_VERSION": "1.2.3"}
        if POWERSHELL:
            env = {key: value for key, value in env.items() if key.upper() != "PSMODULEPATH"}
            env["LOCALAPPDATA"] = str(root / "user data")
            cli = root / "user data/Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd"
            cli.parent.mkdir(parents=True, exist_ok=True)
            # What `dsh plugin --profile <p> add <file>` leaves: the package in the
            # profile and its manifest naming it; `remove dsh-gamma` takes both away.
            cli.write_text('@echo off\r\n>>"%GAMMA_TEST_LOG%" echo dsh^|%*\r\n'
                           'if defined GAMMA_TEST_DSH_FAIL exit /b 7\r\n'
                           'if "%4"=="remove" (\r\n'
                           '  rmdir /s /q "%DSH_HOME%\\profiles\\%3\\node_modules\\dsh-gamma" 2>nul\r\n'
                           '  >"%DSH_HOME%\\profiles\\%3\\package.json" echo {}\r\n'
                           '  exit /b 0\r\n'
                           ')\r\n'
                           'mkdir "%DSH_HOME%\\profiles\\%3\\node_modules\\dsh-gamma" 2>nul\r\n'
                           '>"%DSH_HOME%\\profiles\\%3\\node_modules\\dsh-gamma\\package.json" '
                           'echo {"version": "%GAMMA_TEST_VERSION%"}\r\n'
                           '>"%DSH_HOME%\\profiles\\%3\\package.json" echo {"dependencies": {"dsh-gamma": "file"}}\r\n'
                           'exit /b 0\r\n')
            wrapper = root / "test.ps1"
            wrapper.write_text(r'''
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    if ($env:GAMMA_TEST_BAD) { Set-Content -LiteralPath $OutFile -Value 'not the bundle' }
    else { Copy-Item -LiteralPath $env:GAMMA_TEST_BUNDLE -Destination $OutFile }
}
function Invoke-RestMethod {
    param($Method, $Uri, $ContentType, $Body)
    $sent = ConvertFrom-Json $Body
    if ($Uri.EndsWith('/device')) {
        Add-Content -LiteralPath $env:GAMMA_TEST_LOG -Value "device|$Uri"
        return [pscustomobject]@{ device_code = 'devicecode_abcdefghijklmnopqrstu'; user_code = 'BCDF-GHJK'
                                  expires_in = 600; interval = 1 }
    }
    if ($Uri.EndsWith('/device/token')) {
        Add-Content -LiteralPath $env:GAMMA_TEST_LOG -Value "poll|$Uri|$($sent.device_code)"
        if ($env:GAMMA_TEST_CLAIM_FAIL) { throw 'The connection was cancelled in Gamma.' }
        $polled = "$($env:GAMMA_TEST_LOG).polled"
        if (Test-Path -LiteralPath $polled) { return [pscustomobject]@{ token = 'gamma_testtoken' } }
        Set-Content -LiteralPath $polled -Value 1
        return [pscustomobject]@{ status = 'pending' }
    }
    Add-Content -LiteralPath $env:GAMMA_TEST_LOG -Value ("claim|$Uri|" + $sent.code)
    if ($env:GAMMA_TEST_CLAIM_FAIL) { throw 'This setup command has expired.' }
    [pscustomobject]@{ token = 'gamma_testtoken' }
}
function Start-Process { param($FilePath) Add-Content -LiteralPath $env:GAMMA_TEST_LOG -Value "open|$FilePath" }
if ($args[1] -eq '--remove') { & $args[0] -Remove }
elseif ($args.Count -gt 2) { & $args[0] -ServerUrl $args[1] -SetupCode $args[2] }
else { & $args[0] -ServerUrl $args[1] }
''', encoding="utf-8")
            script = next(p for p in assets if p.name == "install-gamma-dsh.ps1")
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), str(script),
                       *args]
        else:
            bin_dir = root / "bin"
            bin_dir.mkdir(exist_ok=True)
            (bin_dir / "curl").write_text('''#!/bin/sh
for arg do last="$arg"; done
case "$last" in
    */api/integrations/device)
        printf 'device|%s\\n' "$last" >> "$GAMMA_TEST_LOG"
        printf '{"device_code":"devicecode_abcdefghijklmnopqrstu","user_code":"BCDF-GHJK","expires_in":600,"interval":1}' ;;
    */api/integrations/device/token)
        printf 'poll|%s|%s\\n' "$last" "$(printf '%s' "$*" | sed 's/.*"device_code":"\\([^"]*\\)".*/\\1/')" >> "$GAMMA_TEST_LOG"
        if [ -n "$GAMMA_TEST_CLAIM_FAIL" ]; then printf '{"detail":"The connection was cancelled in Gamma."}'
        elif [ -f "$GAMMA_TEST_LOG.polled" ]; then printf '{"token":"gamma_testtoken","name":"DeepSeek Harness","expires_at":1}'
        else : > "$GAMMA_TEST_LOG.polled"; printf '{"status":"pending"}'; fi ;;
    */api/integrations/claim)
        printf 'claim|%s|%s\\n' "$last" "$(printf '%s' "$*" | sed 's/.*"code":"\\([^"]*\\)".*/\\1/')" >> "$GAMMA_TEST_LOG"
        if [ -n "$GAMMA_TEST_CLAIM_FAIL" ]; then printf '{"detail":"This setup command has expired."}'
        else printf '{"token":"gamma_testtoken","name":"DeepSeek Harness","expires_at":1}'; fi ;;
    *)
        dest=""; prev=""
        for arg do [ "$prev" = "-o" ] && dest="$arg"; prev="$arg"; done
        if [ -n "$GAMMA_TEST_BAD" ]; then printf 'not the bundle' > "$dest"; else cp "$GAMMA_TEST_BUNDLE" "$dest"; fi ;;
esac
''')
            (bin_dir / "dsh").write_text('''#!/bin/sh
joined=$(printf '%s|' "$@")
printf 'dsh|%s\\n' "${joined%|}" >> "$GAMMA_TEST_LOG"
[ -z "$GAMMA_TEST_DSH_FAIL" ] || exit 7
if [ "$4" = remove ]; then
    rm -rf "$DSH_HOME/profiles/$3/node_modules/dsh-gamma"
    printf '{}\\n' > "$DSH_HOME/profiles/$3/package.json"
    exit 0
fi
mkdir -p "$DSH_HOME/profiles/$3/node_modules/dsh-gamma"
printf '{"version": "%s"}\\n' "$GAMMA_TEST_VERSION" > "$DSH_HOME/profiles/$3/node_modules/dsh-gamma/package.json"
printf '{"dependencies": {"dsh-gamma": "file"}}\\n' > "$DSH_HOME/profiles/$3/package.json"
''')
            for opener in ("open", "xdg-open"):
                (bin_dir / opener).write_text('#!/bin/sh\nprintf \'open|%s\\n\' "$1" >> "$GAMMA_TEST_LOG"\n')
            for file in bin_dir.iterdir():
                file.chmod(0o755)
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            script = next(p for p in assets if p.name == "install-gamma-dsh.sh")
            command = ["sh", script.as_posix(), *args]
            if os.name == "nt":
                # Git Bash prepends its own bin directories on Windows startup.
                env["GAMMA_TEST_BIN"] = "/" + bin_dir.drive[0].lower() + bin_dir.as_posix()[2:]
                for key in ("DSH_HOME", "GAMMA_TEST_BUNDLE", "GAMMA_TEST_LOG"):
                    env[key] = Path(env[key]).as_posix()
                command = [os.environ["GAMMA_TEST_POSIX_SHELL"], "-c",
                           'PATH="$GAMMA_TEST_BIN:$PATH"; export PATH; exec sh "$@"',
                           "gamma-test", script.as_posix(), *args]
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60)
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls, home, profile

    def test_install_connects_and_a_repeat_only_reconnects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = release(root / "release", "1.2.3")
            result, calls, home, profile = self.run_installer(root, assets)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            target = home / "dsh-gamma.tgz"
            self.assertEqual(target.read_bytes(), next(p for p in assets if p.name == "dsh-gamma.tgz").read_bytes())
            self.assertEqual(calls[0].replace("|", " ").split()[:4], ["dsh", "plugin", "--profile", profile])
            self.assertEqual(Path(calls[0].split("add", 1)[1].strip(' |"')), target)
            # the browser sign-in, its code printed for the page to be checked against
            self.assertEqual(calls[1:], SIGN_IN)
            self.assertIn("BCDF-GHJK", result.stdout)
            saved = (home / "gamma.json").read_bytes()
            self.assertFalse(saved.startswith(b"\xef\xbb\xbf"), "dsh parses the connection as JSON")
            self.assertEqual(json.loads(saved), {"url": SERVER, "token": "gamma_testtoken"})
            # The profile has this version: connecting again only saves the new token.
            (home / "gamma.json").unlink()
            result, calls, home, _ = self.run_installer(root, assets)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(calls, SIGN_IN)
            self.assertTrue((home / "gamma.json").is_file())

    def test_an_older_gammas_setup_code_is_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = release(root / "release", "1.2.3")
            result, calls, home, _ = self.run_installer(root, assets, code=CODE)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(calls[1:], [f"claim|{API}/claim|{CODE}"])
            self.assertEqual(json.loads((home / "gamma.json").read_bytes()), {"url": SERVER, "token": "gamma_testtoken"})

    def test_remove_takes_the_plugin_and_the_connection_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = release(root / "release", "1.2.3")
            result, _, home, profile = self.run_installer(root, assets)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # a failed removal keeps the connection, so the command can run again
            result, calls, home, _ = self.run_installer(root, assets, remove=True, fail_dsh=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Could not remove Gamma", result.stdout + result.stderr)
            self.assertTrue((home / "gamma.json").is_file())
            result, calls, home, _ = self.run_installer(root, assets, remove=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([c.replace("|", " ").split() for c in calls],
                             [["dsh", "plugin", "--profile", profile, "remove", "dsh-gamma"]])
            self.assertFalse((home / "gamma.json").exists())
            self.assertFalse((home / "dsh-gamma.tgz").exists())
            self.assertIn("disconnect it", result.stdout)
            # nothing left: removing again changes nothing and asks no profile
            result, calls, _, _ = self.run_installer(root, assets, remove=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(calls, [])

    def test_failures_stop_before_the_sign_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = release(root / "release", "1.2.3")
            for kwargs, expected in (({"bad_download": True}, "incomplete or changed"),
                                     ({"fail_dsh": True}, "Could not add Gamma")):
                result, calls, home, _ = self.run_installer(root, assets, **kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout + result.stderr)
                self.assertFalse(any(c.startswith(("device|", "claim|")) for c in calls), "Gamma is not asked")
                self.assertFalse((home / "gamma.json").exists())
            result, calls, home, _ = self.run_installer(root, assets, fail_claim=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cancelled", result.stdout + result.stderr)
            self.assertFalse((home / "gamma.json").exists())
            result, calls, home, _ = self.run_installer(root, assets, code=CODE, fail_claim=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expired", result.stdout + result.stderr)
            self.assertFalse((home / "gamma.json").exists())

    @unittest.skipUnless(POWERSHELL, "the desktop app's profile")
    def test_desktop_profile_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = release(root / "release", "1.2.3")
            result, calls, _, _ = self.run_installer(root, assets, desktop=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Open DeepSeek Harness once", result.stdout + result.stderr)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
