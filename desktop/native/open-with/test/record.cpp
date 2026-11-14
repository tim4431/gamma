// Stands in for Gamma.exe beside the folder verb's DLL in its tests
// (test/openWithDll.test.js, and a package registered by hand to try the
// verb in Explorer): writes the package it runs as and each argument it was
// started with, as CommandLineToArgvW reads them, to record.txt beside
// itself, one per line in UTF-8, the file's last line `end`.

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
#include <shellapi.h>

#include <string>

static std::string Utf8(const std::wstring& s) {
  int n = WideCharToMultiByte(CP_UTF8, 0, s.c_str(), -1, nullptr, 0, nullptr, nullptr);
  std::string out(n > 0 ? n - 1 : 0, '\0');
  if (n > 1) WideCharToMultiByte(CP_UTF8, 0, s.c_str(), -1, &out[0], n, nullptr, nullptr);
  return out;
}

int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int) {
  wchar_t self[MAX_PATH];
  DWORD n = GetModuleFileNameW(nullptr, self, MAX_PATH);
  std::wstring dir(self, n);
  dir = dir.substr(0, dir.find_last_of(L'\\'));

  using Current = LONG(WINAPI*)(UINT32*, PWSTR);
  auto current = reinterpret_cast<Current>(
      reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "GetCurrentPackageFamilyName")));
  wchar_t family[256];
  UINT32 length = 256;
  std::wstring package = current && current(&length, family) == ERROR_SUCCESS ? family : L"-";

  std::string text = "package=" + Utf8(package) + "\n";
  int argc = 0;
  LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
  for (int i = 1; argv && i < argc; i++) text += "arg=" + Utf8(argv[i]) + "\n";
  text += "end\n";
  LocalFree(argv);

  HANDLE file = CreateFileW((dir + L"\\record.txt").c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) return 1;
  DWORD written = 0;
  WriteFile(file, text.data(), static_cast<DWORD>(text.size()), &written, nullptr);
  CloseHandle(file);
  return 0;
}
