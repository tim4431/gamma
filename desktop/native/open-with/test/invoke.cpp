// The folder verb's DLL as Explorer drives it, for test/openWithDll.test.js:
// loads the DLL, takes the class from DllGetClassObject, prints what the menu
// would show, and invokes it on the paths given, as a selection of folders.
// Beside the DLL, record.exe stands in for Gamma.exe and writes down what it
// was started with. With --packaged the class comes from a registered
// package instead, out of process, as Explorer gets it: COM runs the DLL in
// the package's surrogate, so the verb starts the package's app.
//
//   invoke.exe <dll> <path>...          prints title=, icon=, name=, state=, invoke=, unload=
//   invoke.exe --packaged <path>...     the same but unload=

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
#include <shlobj.h>
#include <shobjidl.h>

#include <cstdio>
#include <cwchar>
#include <string>
#include <vector>

#include "../verb.h"

static void Print(const char* key, const std::wstring& value) {
  int n = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, nullptr, 0, nullptr, nullptr);
  std::string utf8(n > 0 ? n - 1 : 0, '\0');
  if (n > 1) WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, &utf8[0], n, nullptr, nullptr);
  printf("%s=%s\n", key, utf8.c_str());
}

static std::wstring Hex(HRESULT hr) {
  wchar_t buf[16];
  swprintf(buf, 16, L"0x%08lx", static_cast<unsigned long>(hr));
  return buf;
}

int main() {
  int argc = 0;
  LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
  if (!argv || argc < 2) {
    fprintf(stderr, "usage: invoke.exe <dll> <path>...\n");
    return 2;
  }
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  using CanUnloadNow = HRESULT(STDAPICALLTYPE*)();
  CanUnloadNow canUnloadNow = nullptr;
  IExplorerCommand* command = nullptr;
  HRESULT hr = S_OK;
  if (wcscmp(argv[1], L"--packaged") == 0) {
    hr = CoCreateInstance(CLSID_OpenWithGamma, nullptr, CLSCTX_LOCAL_SERVER, IID_PPV_ARGS(&command));
    if (FAILED(hr)) {
      Print("error", L"CoCreateInstance " + Hex(hr));
      return 1;
    }
  } else {
    HMODULE dll = LoadLibraryW(argv[1]);
    if (!dll) {
      Print("error", L"LoadLibrary failed");
      return 1;
    }
    using GetClassObject = HRESULT(STDAPICALLTYPE*)(REFCLSID, REFIID, void**);
    auto getClassObject = reinterpret_cast<GetClassObject>(reinterpret_cast<void*>(GetProcAddress(dll, "DllGetClassObject")));
    canUnloadNow = reinterpret_cast<CanUnloadNow>(reinterpret_cast<void*>(GetProcAddress(dll, "DllCanUnloadNow")));
    if (!getClassObject || !canUnloadNow) {
      Print("error", L"exports missing");
      return 1;
    }
    IClassFactory* factory = nullptr;
    hr = getClassObject(CLSID_OpenWithGamma, IID_PPV_ARGS(&factory));
    if (FAILED(hr)) {
      Print("error", L"DllGetClassObject " + Hex(hr));
      return 1;
    }
    hr = factory->CreateInstance(nullptr, IID_PPV_ARGS(&command));
    factory->Release();
    if (FAILED(hr)) {
      Print("error", L"CreateInstance " + Hex(hr));
      return 1;
    }
  }

  LPWSTR text = nullptr;
  if (SUCCEEDED(command->GetTitle(nullptr, &text))) {
    Print("title", text);
    CoTaskMemFree(text);
  }
  if (SUCCEEDED(command->GetIcon(nullptr, &text))) {
    Print("icon", text);
    CoTaskMemFree(text);
  }
  GUID name = {};
  command->GetCanonicalName(&name);
  Print("name", IsEqualGUID(name, CLSID_OpenWithGamma) ? L"clsid" : L"other");
  EXPCMDSTATE state = ECS_HIDDEN;
  command->GetState(nullptr, TRUE, &state);
  Print("state", state == ECS_ENABLED ? L"enabled" : L"not enabled");

  std::vector<PIDLIST_ABSOLUTE> pidls;
  for (int i = 2; i < argc; i++) {
    PIDLIST_ABSOLUTE pidl = nullptr;
    if (SUCCEEDED(SHParseDisplayName(argv[i], nullptr, &pidl, 0, nullptr))) pidls.push_back(pidl);
  }
  std::vector<PCIDLIST_ABSOLUTE> list(pidls.begin(), pidls.end());
  IShellItemArray* items = nullptr;
  hr = SHCreateShellItemArrayFromIDLists(static_cast<UINT>(list.size()), list.data(), &items);
  if (SUCCEEDED(hr)) {
    hr = command->Invoke(items, nullptr);
    items->Release();
  }
  Print("invoke", Hex(hr));
  for (PIDLIST_ABSOLUTE pidl : pidls) CoTaskMemFree(pidl);

  command->Release();
  if (canUnloadNow) Print("unload", canUnloadNow() == S_OK ? L"yes" : L"no");
  CoUninitialize();
  LocalFree(argv);
  return 0;
}
