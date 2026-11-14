// "Open with Gamma" on a folder and on an open folder's background, for the
// Microsoft Store package (docs/architecture.md "Opening files and folders").
// A packaged app reaches Explorer's context menu only through a COM class the
// package declares (assets/appx-extensions.xml: a surrogate server and
// windows.fileExplorerContextMenus); this is that class, an IExplorerCommand.
// Explorer runs it in a surrogate process of the package. Invoke hands the
// folders to the app as the NSIS installer's verb does, as its command-line
// arguments, through ActivateApplication, so the app runs as the package:
// its virtualized AppData, its one instance, which takes them from there.
//
// Plain COM, no ATL, WRL or WIL, so MSVC and MinGW-w64 both build it
// (scripts/build-open-with.cjs); scripts/after-pack.cjs puts it beside
// Gamma.exe in the Store package only. verb.h is generated from the table.

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif

#include <windows.h>
#include <objbase.h>
#include <ocidl.h>
#include <servprov.h>
#include <shobjidl.h>

#include <new>
#include <string>
#include <vector>

#include "verb.h"

namespace {

HMODULE g_module = nullptr;
LONG g_live = 0;  // objects alive and class-factory locks held

// {45BA127D-10A8-46EA-8AB7-56EA9078943C}, CLSID_ApplicationActivationManager
// (named here: MinGW-w64 defines it only with INITGUID).
const CLSID kActivationManager = {0x45ba127d, 0x10a8, 0x46ea, {0x8a, 0xb7, 0x56, 0xea, 0x90, 0x78, 0x94, 0x3c}};

// A string for Explorer, which frees it with CoTaskMemFree.
HRESULT Dup(const std::wstring& s, LPWSTR* out) {
  if (!out) return E_POINTER;
  size_t bytes = (s.size() + 1) * sizeof(wchar_t);
  *out = static_cast<LPWSTR>(CoTaskMemAlloc(bytes));
  if (!*out) return E_OUTOFMEMORY;
  memcpy(*out, s.c_str(), bytes);
  return S_OK;
}

// The directory this DLL is in: the package's app\, beside Gamma.exe.
std::wstring ModuleDir() {
  std::wstring path(MAX_PATH, L'\0');
  for (;;) {
    DWORD n = GetModuleFileNameW(g_module, &path[0], static_cast<DWORD>(path.size()));
    if (n == 0) return L"";
    if (n < path.size()) {
      path.resize(n);
      break;
    }
    path.resize(path.size() * 2);
  }
  size_t slash = path.find_last_of(L'\\');
  return slash == std::wstring::npos ? L"" : path.substr(0, slash);
}

std::wstring Leaf(const std::wstring& path) {
  size_t slash = path.find_last_of(L'\\');
  return slash == std::wstring::npos ? path : path.substr(slash + 1);
}

std::wstring Parent(const std::wstring& path) {
  size_t slash = path.find_last_of(L'\\');
  return slash == std::wstring::npos ? L"" : path.substr(0, slash);
}

template <typename F>
F Kernel32(const char* name) {
  return reinterpret_cast<F>(reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"), name)));
}

// The package's family name: the surrogate runs with the package's identity;
// failing that, the package's install directory is named by its full name
// (...\WindowsApps\<full name>\app\GammaOpenWith.dll). "" outside a package.
// Looked up at run time: the two calls are Windows 8's and 8.1's.
std::wstring PackageFamilyName() {
  using Current = LONG(WINAPI*)(UINT32*, PWSTR);
  using FromFull = LONG(WINAPI*)(PCWSTR, UINT32*, PWSTR);
  wchar_t name[256];
  UINT32 length = ARRAYSIZE(name);
  if (auto current = Kernel32<Current>("GetCurrentPackageFamilyName")) {
    if (current(&length, name) == ERROR_SUCCESS) return name;
  }
  if (auto fromFull = Kernel32<FromFull>("PackageFamilyNameFromFullName")) {
    std::wstring full = Leaf(Parent(ModuleDir()));
    length = ARRAYSIZE(name);
    if (!full.empty() && fromFull(full.c_str(), &length, name) == ERROR_SUCCESS) return name;
  }
  return L"";
}

// One argument as CommandLineToArgvW reads it back: in quotes, the
// backslashes before a quote and before the closing one doubled, so a
// drive's root D:\ arrives as D:\ and not as D:".
std::wstring Quote(const std::wstring& arg) {
  std::wstring out = L"\"";
  size_t slashes = 0;
  for (wchar_t c : arg) {
    if (c == L'\\') {
      slashes++;
      continue;
    }
    out.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\');
    out += c;
    slashes = 0;
  }
  out.append(slashes * 2, L'\\');
  out += L'"';
  return out;
}

void AddPath(IShellItem* item, std::vector<std::wstring>& paths) {
  LPWSTR path = nullptr;
  if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)) && path) {
    paths.emplace_back(path);
    CoTaskMemFree(path);
  }
}

// The selected folders; one that is no file-system folder (a library, a
// zip) has no path and is left out.
HRESULT FromItems(IShellItemArray* items, std::vector<std::wstring>& paths) {
  DWORD count = 0;
  HRESULT hr = items->GetCount(&count);
  if (FAILED(hr)) return hr;
  for (DWORD i = 0; i < count; i++) {
    IShellItem* item = nullptr;
    if (SUCCEEDED(items->GetItemAt(i, &item))) {
      AddPath(item, paths);
      item->Release();
    }
  }
  return S_OK;
}

// An open folder's background: Explorer passes no items, and the folder is
// the one its view shows.
HRESULT FromSite(IUnknown* site, std::vector<std::wstring>& paths) {
  if (!site) return E_FAIL;
  IServiceProvider* provider = nullptr;
  HRESULT hr = site->QueryInterface(IID_PPV_ARGS(&provider));
  if (FAILED(hr)) return hr;
  IFolderView* view = nullptr;
  hr = provider->QueryService(__uuidof(IFolderView), IID_PPV_ARGS(&view));  // SID_SFolderView
  provider->Release();
  if (FAILED(hr)) return hr;
  IShellItem* folder = nullptr;
  hr = view->GetFolder(IID_PPV_ARGS(&folder));
  view->Release();
  if (FAILED(hr)) return hr;
  AddPath(folder, paths);
  folder->Release();
  return S_OK;
}

// Starts the app with the folders as its arguments: as the package, by its
// application user model id; outside a package (a DLL beside Gamma.exe of
// an installed build, or the native test), the executable beside this DLL.
HRESULT Launch(const std::vector<std::wstring>& paths) {
  std::wstring args;
  for (const std::wstring& path : paths) {
    if (!args.empty()) args += L' ';
    args += Quote(path);
  }
  std::wstring family = PackageFamilyName();
  if (!family.empty()) {
    IApplicationActivationManager* manager = nullptr;
    HRESULT hr = CoCreateInstance(kActivationManager, nullptr, CLSCTX_LOCAL_SERVER, IID_PPV_ARGS(&manager));
    if (FAILED(hr)) return hr;
    CoAllowSetForegroundWindow(manager, nullptr);  // the app may come to the front
    std::wstring aumid = family + L"!" + GAMMA_APP_ID;
    DWORD pid = 0;
    hr = manager->ActivateApplication(aumid.c_str(), args.c_str(), AO_NONE, &pid);
    manager->Release();
    return hr;
  }
  std::wstring dir = ModuleDir();
  // Started without the package's identity, the app would read another data
  // directory than the one its own start gives it: never from a package.
  std::wstring upper = dir;
  CharUpperW(&upper[0]);
  if (upper.find(L"\\WINDOWSAPPS\\") != std::wstring::npos) return E_UNEXPECTED;
  std::wstring exe = dir + L"\\" + GAMMA_EXE;
  std::wstring command = Quote(exe) + L" " + args;
  STARTUPINFOW startup = {};
  startup.cb = sizeof startup;
  PROCESS_INFORMATION process = {};
  if (!CreateProcessW(exe.c_str(), &command[0], nullptr, nullptr, FALSE, 0, nullptr, nullptr, &startup, &process)) {
    return HRESULT_FROM_WIN32(GetLastError());
  }
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return S_OK;
}

class OpenWithGamma final : public IExplorerCommand, public IObjectWithSite {
 public:
  OpenWithGamma() { InterlockedIncrement(&g_live); }

  // IUnknown
  IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
    if (!ppv) return E_POINTER;
    if (riid == __uuidof(IUnknown) || riid == __uuidof(IExplorerCommand)) {
      *ppv = static_cast<IExplorerCommand*>(this);
    } else if (riid == __uuidof(IObjectWithSite)) {
      *ppv = static_cast<IObjectWithSite*>(this);
    } else {
      *ppv = nullptr;
      return E_NOINTERFACE;
    }
    AddRef();
    return S_OK;
  }
  IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
  IFACEMETHODIMP_(ULONG) Release() override {
    ULONG refs = InterlockedDecrement(&refs_);
    if (refs == 0) delete this;
    return refs;
  }

  // IExplorerCommand
  IFACEMETHODIMP GetTitle(IShellItemArray*, LPWSTR* title) override { return Dup(GAMMA_VERB_TITLE, title); }
  IFACEMETHODIMP GetIcon(IShellItemArray*, LPWSTR* icon) override {
    return Dup(ModuleDir() + L"\\" + GAMMA_EXE + L",0", icon);
  }
  IFACEMETHODIMP GetToolTip(IShellItemArray*, LPWSTR* tip) override {
    if (tip) *tip = nullptr;
    return E_NOTIMPL;
  }
  IFACEMETHODIMP GetCanonicalName(GUID* name) override {
    if (!name) return E_POINTER;
    *name = CLSID_OpenWithGamma;
    return S_OK;
  }
  IFACEMETHODIMP GetState(IShellItemArray*, BOOL, EXPCMDSTATE* state) override {
    if (!state) return E_POINTER;
    *state = ECS_ENABLED;
    return S_OK;
  }
  IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
    std::vector<std::wstring> paths;
    HRESULT hr = items ? FromItems(items, paths) : FromSite(site_, paths);
    if (FAILED(hr)) return hr;
    return paths.empty() ? S_OK : Launch(paths);
  }
  IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
    if (!flags) return E_POINTER;
    *flags = ECF_DEFAULT;
    return S_OK;
  }
  IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** commands) override {
    if (commands) *commands = nullptr;
    return E_NOTIMPL;
  }

  // IObjectWithSite: Explorer's view, where an open folder's background is.
  IFACEMETHODIMP SetSite(IUnknown* site) override {
    if (site) site->AddRef();
    if (site_) site_->Release();
    site_ = site;
    return S_OK;
  }
  IFACEMETHODIMP GetSite(REFIID riid, void** ppv) override {
    if (!ppv) return E_POINTER;
    *ppv = nullptr;
    return site_ ? site_->QueryInterface(riid, ppv) : E_FAIL;
  }

 private:
  ~OpenWithGamma() {
    if (site_) site_->Release();
    InterlockedDecrement(&g_live);
  }

  LONG refs_ = 1;
  IUnknown* site_ = nullptr;
};

class Factory final : public IClassFactory {
 public:
  IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
    if (!ppv) return E_POINTER;
    if (riid == __uuidof(IUnknown) || riid == __uuidof(IClassFactory)) {
      *ppv = static_cast<IClassFactory*>(this);
      return S_OK;
    }
    *ppv = nullptr;
    return E_NOINTERFACE;
  }
  IFACEMETHODIMP_(ULONG) AddRef() override { return 2; }  // static: never freed
  IFACEMETHODIMP_(ULONG) Release() override { return 1; }

  IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
    if (!ppv) return E_POINTER;
    *ppv = nullptr;
    if (outer) return CLASS_E_NOAGGREGATION;
    OpenWithGamma* command = new (std::nothrow) OpenWithGamma();
    if (!command) return E_OUTOFMEMORY;
    HRESULT hr = command->QueryInterface(riid, ppv);
    command->Release();
    return hr;
  }
  IFACEMETHODIMP LockServer(BOOL lock) override {
    if (lock) {
      InterlockedIncrement(&g_live);
    } else {
      InterlockedDecrement(&g_live);
    }
    return S_OK;
  }
};

Factory g_factory;

}  // namespace

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, LPVOID* ppv) {
  if (!ppv) return E_POINTER;
  *ppv = nullptr;
  if (clsid != CLSID_OpenWithGamma) return CLASS_E_CLASSNOTAVAILABLE;
  return g_factory.QueryInterface(riid, ppv);
}

STDAPI DllCanUnloadNow() { return g_live == 0 ? S_OK : S_FALSE; }

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    g_module = instance;
    DisableThreadLibraryCalls(instance);
  }
  return TRUE;
}
