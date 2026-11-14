// Written by scripts/openwith.cjs from tests/shared/openwith.json: edit those, then `npm run openwith`.
// What the Store package's folder verb (open_with.cpp) shares with the
// package's manifest (assets/appx-extensions.xml) and electron-builder.cjs.
#pragma once

// {a5ac2231-021b-477c-846c-b5a2a3ef67c2}: the class the manifest's surrogate server and verbs name.
static const GUID CLSID_OpenWithGamma = {0xa5ac2231, 0x021b, 0x477c, {0x84, 0x6c, 0xb5, 0xa2, 0xa3, 0xef, 0x67, 0xc2}};

// The verb's words (& marks the accelerator), the package's application id
// and its executable, beside this DLL in the package's app\ directory.
#define GAMMA_VERB_TITLE L"Open with &Gamma"
#define GAMMA_APP_ID L"Gamma"
#define GAMMA_EXE L"Gamma.exe"
