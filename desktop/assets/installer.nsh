; The NSIS installer's PDF registration (electron-builder.cjs, nsis.include):
; Gamma in Explorer's "Open with" and among the PDF apps of Settings ->
; Default apps, but never .pdf's handler by itself - the user chooses it
; (docs/architecture.md "Opening PDF files"). SHELL_CONTEXT is HKCU for a
; per-user install, HKLM for one for all users. An update keeps the keys,
; so Gamma stays the default where the user made it so.

!define GAMMA_PROGID "Gamma.pdf"
!define GAMMA_CAPABILITIES "Software\${APP_ID}\Capabilities"

!macro customInstall
  WriteRegStr SHELL_CONTEXT "Software\Classes\${GAMMA_PROGID}" "" "PDF document"
  WriteRegStr SHELL_CONTEXT "Software\Classes\${GAMMA_PROGID}\DefaultIcon" "" "$appExe,0"
  WriteRegStr SHELL_CONTEXT "Software\Classes\${GAMMA_PROGID}\shell\open\command" "" '"$appExe" "%1"'
  WriteRegNone SHELL_CONTEXT "Software\Classes\.pdf\OpenWithProgids" "${GAMMA_PROGID}"
  WriteRegStr SHELL_CONTEXT "${GAMMA_CAPABILITIES}" "ApplicationName" "${PRODUCT_NAME}"
  WriteRegStr SHELL_CONTEXT "${GAMMA_CAPABILITIES}" "ApplicationDescription" "Annotate PDFs and keep notes beside them."
  WriteRegStr SHELL_CONTEXT "${GAMMA_CAPABILITIES}\FileAssociations" ".pdf" "${GAMMA_PROGID}"
  WriteRegStr SHELL_CONTEXT "Software\RegisteredApplications" "${PRODUCT_NAME}" "${GAMMA_CAPABILITIES}"
  ; SHCNE_ASSOCCHANGED, SHCNF_FLUSH: Explorer rereads the associations
  System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"
!macroend

!macro customUnInstall
  ${ifNot} ${isUpdated}
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.pdf\OpenWithProgids" "${GAMMA_PROGID}"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\${GAMMA_PROGID}"
    DeleteRegValue SHELL_CONTEXT "Software\RegisteredApplications" "${PRODUCT_NAME}"
    DeleteRegKey SHELL_CONTEXT "Software\${APP_ID}"
    System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"
  ${endIf}
!macroend
