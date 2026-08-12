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
    !insertmacro mindspacePreserveInstallHome Upgrade
  !endif
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

!macro mindspacePreserveInstallHome LABEL_SUFFIX
  ; NSIS removes $INSTDIR during an upgrade or a normal uninstall. Preserve
  ; the mutable portion beside it first, so the next installation can restore
  ; it without depending on LocalAppData or changing the selected Home.
  IfFileExists "$INSTDIR\environment\*" mindspacePreserveStart_${LABEL_SUFFIX} 0
  IfFileExists "$INSTDIR\models\*" mindspacePreserveStart_${LABEL_SUFFIX} 0
  IfFileExists "$INSTDIR\data\*" mindspacePreserveStart_${LABEL_SUFFIX} 0
  IfFileExists "$INSTDIR\downloads\*" mindspacePreserveStart_${LABEL_SUFFIX} 0
  IfFileExists "$INSTDIR\logs\*" mindspacePreserveStart_${LABEL_SUFFIX} 0
  IfFileExists "$INSTDIR\backups\*" mindspacePreserveStart_${LABEL_SUFFIX} mindspacePreserveDone_${LABEL_SUFFIX}
  mindspacePreserveStart_${LABEL_SUFFIX}:
    IfFileExists "$INSTDIR.mindspace-preserve\*" 0 mindspacePreserveCreate_${LABEL_SUFFIX}
      Abort "检测到上次安装保留的 Mindspace 数据。为避免覆盖，请先重新运行该安装包完成恢复。"
    mindspacePreserveCreate_${LABEL_SUFFIX}:
      CreateDirectory "$INSTDIR.mindspace-preserve"
      IfFileExists "$INSTDIR\environment\*" 0 mindspacePreserveModels_${LABEL_SUFFIX}
        Rename "$INSTDIR\environment" "$INSTDIR.mindspace-preserve\environment"
      mindspacePreserveModels_${LABEL_SUFFIX}:
      IfFileExists "$INSTDIR\models\*" 0 mindspacePreserveData_${LABEL_SUFFIX}
        Rename "$INSTDIR\models" "$INSTDIR.mindspace-preserve\models"
      mindspacePreserveData_${LABEL_SUFFIX}:
      IfFileExists "$INSTDIR\data\*" 0 mindspacePreserveDownloads_${LABEL_SUFFIX}
        Rename "$INSTDIR\data" "$INSTDIR.mindspace-preserve\data"
      mindspacePreserveDownloads_${LABEL_SUFFIX}:
      IfFileExists "$INSTDIR\downloads\*" 0 mindspacePreserveLogs_${LABEL_SUFFIX}
        Rename "$INSTDIR\downloads" "$INSTDIR.mindspace-preserve\downloads"
      mindspacePreserveLogs_${LABEL_SUFFIX}:
      IfFileExists "$INSTDIR\logs\*" 0 mindspacePreserveBackups_${LABEL_SUFFIX}
        Rename "$INSTDIR\logs" "$INSTDIR.mindspace-preserve\logs"
      mindspacePreserveBackups_${LABEL_SUFFIX}:
      IfFileExists "$INSTDIR\backups\*" 0 mindspacePreserveDone_${LABEL_SUFFIX}
        Rename "$INSTDIR\backups" "$INSTDIR.mindspace-preserve\backups"
  mindspacePreserveDone_${LABEL_SUFFIX}:
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
  ; Packaged Home now lives inside $INSTDIR, never under LocalAppData.
  IfFileExists "$INSTDIR\environment.upgrade-preserve\*" 0 restoreEnvironmentDone
  IfFileExists "$INSTDIR\environment\*" restoreEnvironmentDone 0
  Rename "$INSTDIR\environment.upgrade-preserve" "$INSTDIR\environment"
  IfErrors 0 restoreEnvironmentDone
    Abort "Mindspace 应用已更新，但私有环境恢复失败。请重新运行安装器执行修复。"
  restoreEnvironmentDone:
  ; Restore whole directories only when the new package did not create a
  ; destination. If it did, desktop/storage-location.cjs imports the preserved
  ; files one by one and records every conflict without overwriting either side.
  IfFileExists "$INSTDIR.mindspace-preserve\environment\*" 0 restoreModelsDone
  IfFileExists "$INSTDIR\environment\*" restoreModelsDone 0
  Rename "$INSTDIR.mindspace-preserve\environment" "$INSTDIR\environment"
  restoreModelsDone:
  IfFileExists "$INSTDIR.mindspace-preserve\models\*" 0 restoreDataDone
  IfFileExists "$INSTDIR\models\*" restoreDataDone 0
  Rename "$INSTDIR.mindspace-preserve\models" "$INSTDIR\models"
  restoreDataDone:
  IfFileExists "$INSTDIR.mindspace-preserve\data\*" 0 restoreDownloadsDone
  IfFileExists "$INSTDIR\data\*" restoreDownloadsDone 0
  Rename "$INSTDIR.mindspace-preserve\data" "$INSTDIR\data"
  restoreDownloadsDone:
  IfFileExists "$INSTDIR.mindspace-preserve\downloads\*" 0 restoreLogsDone
  IfFileExists "$INSTDIR\downloads\*" restoreLogsDone 0
  Rename "$INSTDIR.mindspace-preserve\downloads" "$INSTDIR\downloads"
  restoreLogsDone:
  IfFileExists "$INSTDIR.mindspace-preserve\logs\*" 0 restoreBackupsDone
  IfFileExists "$INSTDIR\logs\*" restoreBackupsDone 0
  Rename "$INSTDIR.mindspace-preserve\logs" "$INSTDIR\logs"
  restoreBackupsDone:
  IfFileExists "$INSTDIR.mindspace-preserve\backups\*" 0 restorePreservedDone
  IfFileExists "$INSTDIR\backups\*" restorePreservedDone 0
  Rename "$INSTDIR.mindspace-preserve\backups" "$INSTDIR\backups"
  restorePreservedDone:
  RMDir "$INSTDIR.mindspace-preserve"
!macroend

!macro customUnInstall
  !insertmacro mindspacePreserveInstallHome Uninstall
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
