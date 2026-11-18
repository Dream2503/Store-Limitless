!macro NSIS_HOOK_POSTINSTALL
    DetailPrint "Installing Memurai Redis service..."

    ExecWait 'msiexec /quiet /i "$INSTDIR\resources\Memurai.msi" /norestart' $0

    ${If} $0 != 0
        MessageBox MB_ICONSTOP "Memurai installation failed. StoreLimitless cannot start without Redis. Error code: $0"
        Abort
    ${EndIf}

    DetailPrint "Memurai Redis service installed successfully."
!macroend