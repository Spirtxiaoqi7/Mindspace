!macro preInit
  SetRegView 64
!macroend

!macro customCheckAppRunning
  ; The user may move a new version to another drive. The stock check only
  ; searches below $INSTDIR, so it misses an older Mindspace on the old drive.
  ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
  ${if} $R0 == 0
    MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "安装需要先退出正在运行的 Mindspace。选择“确定”将保存现有数据并关闭应用进程。" /SD IDOK IDOK closeMindspaceForInstall
    Quit
    closeMindspaceForInstall:
      DetailPrint "正在关闭旧版 Mindspace…"
      nsExec::ExecToStack `"$SYSDIR\taskkill.exe" /F /T /IM "${APP_EXECUTABLE_FILENAME}"`
      Pop $R1
      Pop $R2
      ; Electron may first close the visible window and then unwind its tray,
      ; GPU and child-process trees. A fixed 600ms sleep made the installer
      ; randomly report a false failure and look stuck on slower machines.
      ; Wait for a bounded 10 seconds and offer an explicit retry/cancel path.
      StrCpy $R3 0
      mindspaceCloseWait:
      Sleep 500
      ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
      ${if} $R0 == 0
        IntOp $R3 $R3 + 1
        ${if} $R3 < 20
          Goto mindspaceCloseWait
        ${endIf}
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Mindspace 仍在退出。请从托盘选择“退出”，然后点击“重试”。安装不会修改个人数据。" /SD IDCANCEL IDRETRY mindspaceRetryClose
        Abort "已取消安装；请退出旧版 Mindspace 后重新运行安装器。"
        mindspaceRetryClose:
          nsExec::ExecToStack `"$SYSDIR\taskkill.exe" /F /T /IM "${APP_EXECUTABLE_FILENAME}"`
          Pop $R1
          Pop $R2
          StrCpy $R3 0
          Goto mindspaceCloseWait
      ${endIf}
      ; The process can disappear slightly before Windows releases the mapped
      ; executable and ASAR handles. Give Defender/indexer a bounded quiet
      ; period, then verify that the app did not respawn from its tray.
      Sleep 2000
      ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
      ${if} $R0 == 0
        StrCpy $R3 0
        Goto mindspaceCloseWait
      ${endIf}
  ${endIf}
  !ifndef BUILD_UNINSTALLER
    ; electron-builder's --updated removal performs an atomic directory rename.
    ; On real user machines that path can spend about a minute retrying a file
    ; handle retained by Defender/indexers. Our mutable data is outside
    ; $INSTDIR, so run the old uninstaller once in normal keep-data mode before
    ; the stock upgrade hook. The new installer recreates shortcuts and registry
    ; entries immediately afterwards.
    IfFileExists "$INSTDIR\${UNINSTALL_FILENAME}" 0 mindspacePreUpgradeDone
      InitPluginsDir
      CopyFiles /SILENT "$INSTDIR\${UNINSTALL_FILENAME}" "$PLUGINSDIR\mindspace-preupgrade-uninstaller.exe"
      ExecWait '"$PLUGINSDIR\mindspace-preupgrade-uninstaller.exe" /S /KEEP_APP_DATA /currentuser _?=$INSTDIR' $R4
      ${if} $R4 != 0
        DetailPrint "兼容卸载未完成（退出码 $R4），将转入标准升级恢复。"
      ${endIf}
    mindspacePreUpgradeDone:
  !endif
!macroend

!macro mindspaceRetryOldUninstall LABEL_SUFFIX
  ; electron-builder normally retries the old uninstaller five times. On some
  ; Windows machines Defender retains a newly installed Electron executable
  ; longer than that. Its stock final MessageBox has no silent default and
  ; therefore leaves /S upgrades waiting forever. Do one bounded retry and
  ; always return a process exit code in silent mode.
  ${if} $R0 != 0
    DetailPrint "旧版文件仍被占用，正在等待系统释放（退出码 $R0）…"
      Sleep 1000
      ExecWait '"$PLUGINSDIR\old-uninstaller.exe" /S /KEEP_APP_DATA $0 _?=$INSTDIR' $R0
      ${if} $R0 == 0
        Goto mindspaceOldUninstallReady_${LABEL_SUFFIX}
      ${endIf}
      ; The application has already been stopped and all mutable data lives
      ; outside the install directory. Fall back to a normal silent uninstall:
      ; it removes stale shortcuts/registry entries and lets the new installer
      ; recreate them, while /KEEP_APP_DATA preserves every user-owned file.
      DetailPrint "升级卸载持续失败，正在使用保留数据的兼容卸载路径…"
      ExecWait '"$PLUGINSDIR\old-uninstaller.exe" /S /KEEP_APP_DATA /currentuser _?=$INSTDIR' $R0
      ${if} $R0 == 0
        Goto mindspaceOldUninstallReady_${LABEL_SUFFIX}
      ${endIf}
      MessageBox MB_OK|MB_ICONEXCLAMATION "旧版 Mindspace 文件仍被占用，安装已安全停止。请关闭文件管理器中的应用目录并重新运行安装包；人物卡、会话和模型未被删除。" /SD IDOK
      SetErrorLevel 2
      Quit
    mindspaceOldUninstallReady_${LABEL_SUFFIX}:
  ${endIf}
!macroend

!macro customUnInstallCheckCurrentUser
  !insertmacro mindspaceRetryOldUninstall CurrentUser
!macroend

!macro customUnInstallCheck
  !insertmacro mindspaceRetryOldUninstall ShellContext
!macroend

!macro customInstall
  ; Recovery for installers released before 0.4.7 that could leave the private
  ; runtime in environment.upgrade-preserve after a failed finalization step.
  IfFileExists "$LOCALAPPDATA\Mindspace\environment.upgrade-preserve\*" 0 restoreEnvironmentDone
  IfFileExists "$LOCALAPPDATA\Mindspace\environment\*" restoreEnvironmentDone 0
  Rename "$LOCALAPPDATA\Mindspace\environment.upgrade-preserve" "$LOCALAPPDATA\Mindspace\environment"
  IfErrors 0 restoreEnvironmentDone
    Abort "Mindspace 应用已更新，但私有环境恢复失败。请重新运行安装器执行修复。"
  restoreEnvironmentDone:
!macroend

!macro customUnInstall
  ${ifNot} ${isUpdated}
    ; The application directory and shortcuts are owned by NSIS, but the
    ; Mindspace Home is user-selected and may contain conversations, profiles,
    ; RAG indexes, model weights, reference audio and resumable downloads.
    ; An uninstaller cannot safely infer that ownership from $LOCALAPPDATA,
    ; especially after storage migration. Keep the whole Home intact. Data
    ; deletion belongs to an explicit in-app operation with the resolved path
    ; shown to the user, never to a silent uninstall or an upgrade.
    ${ifNot} ${Silent}
      MessageBox MB_OK|MB_ICONINFORMATION "Mindspace 应用程序已卸载。会话、人物卡、配置、知识库、模型和下载断点均已保留，可在重新安装后继续使用。"
    ${endif}
  ${endif}
!macroend
