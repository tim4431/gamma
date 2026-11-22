; The NSIS installer's hooks (electron-builder.cjs, nsis.include): each of
; electron-builder's macros defined once, here. What Gamma registers in File
; Explorer is generated, openwith.nsh beside this file (scripts/openwith.cjs,
; `npm run openwith`); the uninstaller's question about Gamma's data is
; below (desktop/docs/architecture.md "Shell state").

!include "${__FILEDIR__}\openwith.nsh"

; The installer's File Explorer page, after the directory page.
!macro customPageAfterChangeDir
  !insertmacro gammaOpenWithPage
!macroend

!macro customInstall
  !insertmacro gammaOpenWithInstall
!macroend

; The uninstaller's page after its welcome: delete Gamma's data too, never
; ticked by itself. Gamma's data is Electron's userData, %APPDATA%\ plus
; package.json's name (APP_PACKAGE_NAME): servers.json, the logs, and every
; local server's library under the default storage folder. A storage folder
; chosen elsewhere (settings.dataRoot) and the synced folders on disk are
; the person's own and stay. An update runs the old uninstaller silently,
; with no page, so nothing is ever ticked there.
!macro customUnWelcomePage
  !insertmacro MUI_UNPAGE_WELCOME
  !include nsDialogs.nsh
  Var gammaDeleteData
  Var gammaDeleteDataBox

  UninstPage custom un.gammaDataShow un.gammaDataLeave

  Function un.gammaDataShow
    !insertmacro MUI_HEADER_TEXT "Gamma's data" "Choose whether your libraries and settings stay on this computer."
    nsDialogs::Create 1018
    Pop $0
    ${NSD_CreateCheckbox} 0 0 100% 14u "Also delete Gamma's data from this computer"
    Pop $gammaDeleteDataBox
    ${NSD_CreateLabel} 0 22u 100% 48u "Your settings, the servers you added and the libraries in Gamma's own storage folder (%APPDATA%\${APP_PACKAGE_NAME}) are deleted. Leave this unticked to keep them for a later install. A storage folder you chose elsewhere and your synced folders are never deleted."
    Pop $0
    nsDialogs::Show
  FunctionEnd

  Function un.gammaDataLeave
    ${NSD_GetState} $gammaDeleteDataBox $gammaDeleteData
  FunctionEnd
!macroend

!macro customUnInstall
  ${ifNot} ${isUpdated}
    !insertmacro gammaOpenWithUnInstall
    ; Gamma's data, when ticked: userData and the updater's downloads
    ; (updaterCacheDirName), per user whatever the install mode.
    ${if} $gammaDeleteData = 1
      SetShellVarContext current
      RMDir /r "$APPDATA\${APP_PACKAGE_NAME}"
      RMDir /r "$LOCALAPPDATA\${APP_PACKAGE_NAME}-updater"
      ${if} $installMode == "all"
        SetShellVarContext all
      ${endIf}
    ${endIf}
  ${endIf}
!macroend
