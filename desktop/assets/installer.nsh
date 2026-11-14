; Written by scripts/openwith.cjs from tests/shared/openwith.json: edit those, then `npm run openwith`.
;
; The NSIS installer's registration (electron-builder.cjs, nsis.include):
; Gamma in Explorer's "Open with" and Settings -> Default apps for each
; file type of the table, never a type's handler by itself - the user
; chooses it - and "Open with Gamma" on a folder and on the background of
; an open one (on Windows 11 under "Show more options"; not on a drive, a
; drive root is refused, and not on Folder, which also matches Control
; Panel, Libraries and zip folders). SHELL_CONTEXT is HKCU for a per-user
; install, HKLM for one for all users. An update keeps every key and
; customInstall rewrites them, so Gamma stays the default where the user
; made it so; an uninstall removes exactly what customInstall writes.
; An extension dropped from the table goes in `dropped` of the generator:
; customInstall of the version that drops it deletes its value and then
; both keys /ifempty, so an update leaves nothing behind.

!macro customInstall
  ; A ProgID per kind: its name, its icon, how it opens a file.
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
  ; Open with and Default apps per extension; never the extension's own default.
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
  ; The folder verb, on a folder and on the background of an open one.
  WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma" "" "Open with &Gamma"
  WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma" "Icon" "$appExe,0"
  WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma\command" "" '"$appExe" "%V"'
  WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma" "" "Open with &Gamma"
  WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma" "Icon" "$appExe,0"
  WriteRegStr SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma\command" "" '"$appExe" "%V"'
  ; SHCNE_ASSOCCHANGED, SHCNF_FLUSH: Explorer rereads the associations
  System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"
!macroend

!macro customUnInstall
  ${ifNot} ${isUpdated}
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.pdf\OpenWithProgids" "Gamma.pdf"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.pdf\OpenWithProgids"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.pdf"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.md\OpenWithProgids" "Gamma.markdown"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.md\OpenWithProgids"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.md"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.markdown\OpenWithProgids" "Gamma.markdown"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.markdown\OpenWithProgids"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.markdown"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.tex\OpenWithProgids" "Gamma.latex"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.tex\OpenWithProgids"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.tex"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.bib\OpenWithProgids" "Gamma.bibtex"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.bib\OpenWithProgids"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.bib"
    DeleteRegValue SHELL_CONTEXT "Software\Classes\.typ\OpenWithProgids" "Gamma.typst"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.typ\OpenWithProgids"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\Classes\.typ"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.pdf"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.markdown"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.latex"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.bibtex"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Gamma.typst"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Directory\shell\Gamma"
    DeleteRegKey SHELL_CONTEXT "Software\Classes\Directory\Background\shell\Gamma"
    DeleteRegValue SHELL_CONTEXT "Software\RegisteredApplications" "${PRODUCT_NAME}"
    DeleteRegKey SHELL_CONTEXT "Software\${APP_ID}"
    System::Call "shell32::SHChangeNotify(i,i,i,i) (0x08000000, 0x1000, 0, 0)"
  ${endIf}
!macroend
