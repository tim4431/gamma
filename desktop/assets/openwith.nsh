; Written by scripts/openwith.cjs from tests/shared/openwith.json: edit those, then `npm run openwith`.
;
; The NSIS installer's registration, macros that assets/installer.nsh
; (electron-builder.cjs, nsis.include) puts in electron-builder's hooks,
; in the two parts of scripts/openwith.cjs parts(): Gamma in Explorer's
; "Open with" and Settings -> Default apps for each file type of the
; table, never a type's handler by itself - the user chooses it - and
; "Open with Gamma" on a folder and on the background of an open one. The
; page (gammaOpenWithPage, after the directory page) offers both, ticked
; as this install's last one recorded them (Software\${APP_ID}:
; OpenWithFiles, OpenWithFolders), both on for a first install.
; gammaOpenWithInstall writes a part that is on and removes one that is
; off, then records them, so an update (silent, no page) and the
; launcher's switches (lib/openWith.js, the same entries) keep what the
; person chose. SHELL_CONTEXT is HKCU for a per-user install, HKLM for one
; for all users. An update rewrites the keys of a part that is on, so
; Gamma stays the default where the user made it so;
; gammaOpenWithUnInstall removes exactly what the install writes. An
; extension dropped from the table goes in `dropped` of the generator: the
; install of the version that drops it deletes its value and then both
; keys once empty, so an update leaves nothing behind.

; A key deleted only once it holds no value, its default included, and no
; subkey: DeleteRegKey /ifempty looks at subkeys alone, and an extension's
; key or its OpenWithProgids may hold another app's entries.
!macro gammaDeleteKeyIfEmpty KEY
  Push $R8
  Push $R9
  ClearErrors
  EnumRegValue $R8 SHELL_CONTEXT "${KEY}" 0
  ${if} ${Errors}
    EnumRegKey $R9 SHELL_CONTEXT "${KEY}" 0
    ${if} $R9 == ""
      DeleteRegKey SHELL_CONTEXT "${KEY}"
    ${endIf}
  ${endIf}
  ClearErrors
  Pop $R9
  Pop $R8
!macroend

; The page, put in after the directory page, where MUI2, LogicLib and
; electron-builder's own macros are defined. nsis.oneClick stays false: a
; one-click installer has no pages, and gammaOpenWithInstall reads these
; variables. An update (--updated, run silently) skips it.
!macro gammaOpenWithPage
  !include nsDialogs.nsh
  Var gammaChosen
  Var gammaFiles
  Var gammaFolders
  Var gammaFilesBox
  Var gammaFoldersBox

  Page custom gammaOpenWithShow gammaOpenWithLeave

  ; The parts as this install's last one recorded them, else on.
  Function gammaReadChoice
    ClearErrors
    ReadRegDWORD $gammaFiles SHELL_CONTEXT "Software\${APP_ID}" "OpenWithFiles"
    ${if} ${Errors}
      StrCpy $gammaFiles 1
    ${endIf}
    ClearErrors
    ReadRegDWORD $gammaFolders SHELL_CONTEXT "Software\${APP_ID}" "OpenWithFolders"
    ${if} ${Errors}
      StrCpy $gammaFolders 1
    ${endIf}
    ClearErrors
  FunctionEnd

  Function gammaOpenWithShow
    ${if} ${isUpdated}
      Abort
    ${endIf}
    Call gammaReadChoice
    !insertmacro MUI_HEADER_TEXT "File Explorer" "Choose where Gamma appears in File Explorer."
    nsDialogs::Create 1018
    Pop $0
    ${NSD_CreateCheckbox} 0 0 100% 14u "List Gamma under Open with for PDF, Markdown, LaTeX, BibTeX and Typst files"
    Pop $gammaFilesBox
    ${if} $gammaFiles = 1
      ${NSD_Check} $gammaFilesBox
    ${endIf}
    ${NSD_CreateCheckbox} 0 20u 100% 14u "Add Open with Gamma to the right-click menu of folders"
    Pop $gammaFoldersBox
    ${if} $gammaFolders = 1
      ${NSD_Check} $gammaFoldersBox
    ${endIf}
    ${NSD_CreateLabel} 0 48u 100% 24u "Gamma never makes itself a file type's default app. Both can be changed later in Gamma's launcher, under Settings."
    Pop $0
    nsDialogs::Show
  FunctionEnd

  Function gammaOpenWithLeave
    ${NSD_GetState} $gammaFilesBox $gammaFiles
    ${NSD_GetState} $gammaFoldersBox $gammaFolders
    StrCpy $gammaChosen 1
  FunctionEnd
!macroend

!macro gammaOpenWithInstall
  ; The parts: as ticked on the page, else (an update, a silent install) as recorded, else on.
  ${if} $gammaChosen != 1
    Call gammaReadChoice
  ${endIf}
  WriteRegDWORD SHELL_CONTEXT "Software\${APP_ID}" "OpenWithFiles" $gammaFiles
  WriteRegDWORD SHELL_CONTEXT "Software\${APP_ID}" "OpenWithFolders" $gammaFolders
  ; Gamma in Open with and Default apps: a ProgID per kind, each extension's Open with, the capabilities.
  ${if} $gammaFiles = 1
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.pdf" "" "PDF document"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.pdf\DefaultIcon" "" "$appExe,0"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.pdf\shell\open\command" "" '"$appExe" "%1"'
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.markdown" "" "Markdown document"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.markdown\DefaultIcon" "" "$appExe,0"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.markdown\shell\open\command" "" '"$appExe" "%1"'
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.latex" "" "LaTeX source"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.latex\DefaultIcon" "" "$appExe,0"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.latex\shell\open\command" "" '"$appExe" "%1"'
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.bibtex" "" "BibTeX database"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.bibtex\DefaultIcon" "" "$appExe,0"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.bibtex\shell\open\command" "" '"$appExe" "%1"'
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.typst" "" "Typst source"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.typst\DefaultIcon" "" "$appExe,0"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Gamma.typst\shell\open\command" "" '"$appExe" "%1"'
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities" "ApplicationName" "${PRODUCT_NAME}"
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities" "ApplicationDescription" "Annotate PDFs and keep notes beside them."
    WriteRegNone SHELL_CONTEXT "Software\Classes\.pdf\OpenWithProgids" "Gamma.pdf"
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities\FileAssociations" ".pdf" "Gamma.pdf"
    WriteRegNone SHELL_CONTEXT "Software\Classes\.md\OpenWithProgids" "Gamma.markdown"
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities\FileAssociations" ".md" "Gamma.markdown"
    WriteRegNone SHELL_CONTEXT "Software\Classes\.markdown\OpenWithProgids" "Gamma.markdown"
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities\FileAssociations" ".markdown" "Gamma.markdown"
    WriteRegNone SHELL_CONTEXT "Software\Classes\.tex\OpenWithProgids" "Gamma.latex"
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities\FileAssociations" ".tex" "Gamma.latex"
    WriteRegNone SHELL_CONTEXT "Software\Classes\.bib\OpenWithProgids" "Gamma.bibtex"
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities\FileAssociations" ".bib" "Gamma.bibtex"
    WriteRegNone SHELL_CONTEXT "Software\Classes\.typ\OpenWithProgids" "Gamma.typst"
    WriteRegStr SHELL_CONTEXT "Software\${APP_ID}\Capabilities\FileAssociations" ".typ" "Gamma.typst"
    WriteRegStr SHELL_CONTEXT "Software\RegisteredApplications" "${PRODUCT_NAME}" "Software\${APP_ID}\Capabilities"
  ${else}
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.pdf\OpenWithProgids" "Gamma.pdf"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.pdf\OpenWithProgids"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.pdf"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.md\OpenWithProgids" "Gamma.markdown"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.md\OpenWithProgids"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.md"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.markdown\OpenWithProgids" "Gamma.markdown"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.markdown\OpenWithProgids"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.markdown"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.tex\OpenWithProgids" "Gamma.latex"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.tex\OpenWithProgids"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.tex"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.bib\OpenWithProgids" "Gamma.bibtex"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.bib\OpenWithProgids"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.bib"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.typ\OpenWithProgids" "Gamma.typst"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.typ\OpenWithProgids"
    !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.typ"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.pdf"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.markdown"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.latex"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.bibtex"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.typst"
    DeleteRegValue SHELL_CONTEXT "Software\RegisteredApplications" "${PRODUCT_NAME}"
    DeleteRegKey SHELL_CONTEXT "Software\${APP_ID}\Capabilities"
  ${endIf}
  ; Open with Gamma on a folder and on the background of an open one.
  ${if} $gammaFolders = 1
    WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma" "" "Open with &Gamma"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma" "Icon" "$appExe,0"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma\command" "" '"$appExe" "%V"'
    WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma" "" "Open with &Gamma"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma" "Icon" "$appExe,0"
    WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma\command" "" '"$appExe" "%V"'
  ${else}
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma"
  ${endIf}
  ; SHCNE_ASSOCCHANGED, SHCNF_FLUSH: Explorer rereads the associations
  System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"
!macroend

; Not on an update: the hook runs it only for an uninstall.
!macro gammaOpenWithUnInstall
  DeleteRegValue SHELL_CONTEXT "Software\Classes\.pdf\OpenWithProgids" "Gamma.pdf"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.pdf\OpenWithProgids"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.pdf"
  DeleteRegValue SHELL_CONTEXT "Software\Classes\.md\OpenWithProgids" "Gamma.markdown"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.md\OpenWithProgids"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.md"
  DeleteRegValue SHELL_CONTEXT "Software\Classes\.markdown\OpenWithProgids" "Gamma.markdown"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.markdown\OpenWithProgids"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.markdown"
  DeleteRegValue SHELL_CONTEXT "Software\Classes\.tex\OpenWithProgids" "Gamma.latex"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.tex\OpenWithProgids"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.tex"
  DeleteRegValue SHELL_CONTEXT "Software\Classes\.bib\OpenWithProgids" "Gamma.bibtex"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.bib\OpenWithProgids"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.bib"
  DeleteRegValue SHELL_CONTEXT "Software\Classes\.typ\OpenWithProgids" "Gamma.typst"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.typ\OpenWithProgids"
  !insertmacro gammaDeleteKeyIfEmpty "Software\Classes\.typ"
  DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.pdf"
  DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.markdown"
  DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.latex"
  DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.bibtex"
  DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.typst"
  DeleteRegValue SHELL_CONTEXT "Software\RegisteredApplications" "${PRODUCT_NAME}"
  DeleteRegKey SHELL_CONTEXT "Software\${APP_ID}\Capabilities"
  DeleteRegKey SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma"
  DeleteRegKey SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma"
  DeleteRegKey SHELL_CONTEXT "Software\${APP_ID}"
  System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"
!macroend
