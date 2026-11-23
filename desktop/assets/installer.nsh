; The NSIS installer's hooks (electron-builder.cjs, nsis.include): each of
; electron-builder's macros defined once, here. What Gamma registers in File
; Explorer is generated, openwith.nsh beside this file (scripts/openwith.cjs,
; `npm run openwith`); the uninstaller's question about Gamma's data is
; below (desktop/docs/architecture.md "Shell state").

!include "${__FILEDIR__}\openwith.nsh"

; The install folder's longest path. Windows reaches no path longer than
; 259 characters while long paths are off, as they are by default: the
; installer's copy skips a file past that, and the frozen server cannot load
; one (a server that cannot find pydantic_core's extension is the symptom).
; The app's deepest file sits about 120 characters below the folder, so the
; folder's own path is held to this; the build fails when the app outgrows
; it (scripts/install-depth.cjs, run by afterPack). gammaInstallFolderLimit
; turns the directory page's Next off past it.
!define GAMMA_INSTDIR_MAX 120

; The directory page's check: $INSTDIR as typed, plus the \Gamma that
; electron-builder's instFilesPre adds to a path not naming it. Past the
; limit, Next is off and the page's top text (IDC_INTROTEXT, 1006) says why,
; in Chinese for a Simplified Chinese install (LANGID 2052) and English
; otherwise; the page's own words, in the installer's language, come back
; once the folder fits. An update or a silent install (/D=) keeps the
; folder it is given. Inserted from customPageAfterChangeDir, after
; electron-builder includes StrContains.nsh.
!macro gammaInstallFolderLimit
  Var gammaDirTop
  Function .onVerifyInstDir
    ${If} ${Silent}
    ${OrIf} ${isUpdated}
      Return
    ${EndIf}
    Push $0
    Push $1
    StrLen $0 $INSTDIR
    ${StrContains} $1 "${APP_FILENAME}" $INSTDIR
    ${If} $1 == ""
      StrLen $1 "\${APP_FILENAME}"
      IntOp $0 $0 + $1
    ${EndIf}
    FindWindow $1 "#32770" "" $HWNDPARENT
    GetDlgItem $1 $1 1006
    ${If} $0 > ${GAMMA_INSTDIR_MAX}
      ${If} $gammaDirTop == ""
        System::Call 'user32::GetWindowText(p r1, t .s, i ${NSIS_MAX_STRLEN})'
        Pop $gammaDirTop
      ${EndIf}
      ${If} $LANGUAGE == 2052
        SendMessage $1 ${WM_SETTEXT} 0 "STR:请选择路径更短的文件夹。Gamma 的安装路径最多 ${GAMMA_INSTDIR_MAX} 个字符，其中的文件才不会超出 Windows 260 个字符的路径长度限制；当前为 $0 个字符。"
      ${Else}
        SendMessage $1 ${WM_SETTEXT} 0 "STR:Choose a folder with a shorter path. Gamma's folder can have at most ${GAMMA_INSTDIR_MAX} characters, so that its files stay within Windows' 260-character path limit; this one would have $0."
      ${EndIf}
      Pop $1
      Pop $0
      Abort
    ${EndIf}
    ${If} $gammaDirTop != ""
      SendMessage $1 ${WM_SETTEXT} 0 "STR:$gammaDirTop"
      StrCpy $gammaDirTop ""
    ${EndIf}
    Pop $1
    Pop $0
  FunctionEnd
!macroend

; The install folder's check, and the File Explorer page after the
; directory page.
!macro customPageAfterChangeDir
  !insertmacro gammaInstallFolderLimit
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
