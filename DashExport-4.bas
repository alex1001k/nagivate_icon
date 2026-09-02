Attribute VB_Name = "DashExport"
Option Explicit

' ===================== SETTINGS =====================
' Sheet with the reference table.
Const USE_SHEET_INDEX As Boolean = True     ' True = use SHEET_INDEX below, False = use SHEET_NAME below
Const SHEET_INDEX As Long = 1                ' 1 = first sheet in the workbook
Const SHEET_NAME As String = "Sheet1"        ' used only if USE_SHEET_INDEX = False

Const HEADER_ROW As Long = 1                 ' row with column headers
Const COL_DASH As String = "A"               ' column letter for "Dash" (Dash column)
Const COL_SCREEN As String = "B"             ' column letter for "Screen" (Screen column)
Const COL_URL As String = "C"                ' column letter for "Link" (Link column)
Const COL_RUN_FLAG As String = "D"           ' column letter for the run marker (1 = process, 0 = skip)
Const STATUS_CELL As String = "F1"           ' cell where live progress is written (visible even if Excel is not the active window, via taskbar caption too)

Const WAIT_SECONDS As Long = 20              ' pause to let the dashboard load
Const AFTER_OPEN_PAUSE As Long = 2000        ' ms, extra pause right after opening the link
Const KEY_PAUSE As Long = 400                ' ms, pause between Ctrl+A and Ctrl+C
Const CLOSE_TAB_AFTER_COPY As Boolean = True ' True = send Ctrl+W after each link (closes tabs as it goes)

#If VBA7 Then
    Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
    Private Declare PtrSafe Function OpenClipboard Lib "user32" (ByVal hwnd As LongPtr) As Long
    Private Declare PtrSafe Function CloseClipboard Lib "user32" () As Long
    Private Declare PtrSafe Function EmptyClipboard Lib "user32" () As Long
    Private Declare PtrSafe Function GetClipboardData Lib "user32" (ByVal wFormat As Long) As LongPtr
    Private Declare PtrSafe Function GlobalLock Lib "kernel32" (ByVal hMem As LongPtr) As LongPtr
    Private Declare PtrSafe Function GlobalUnlock Lib "kernel32" (ByVal hMem As LongPtr) As Long
    Private Declare PtrSafe Function lstrlenW Lib "kernel32" (ByVal lpString As LongPtr) As Long
    Private Declare PtrSafe Sub CopyMemory Lib "kernel32" Alias "RtlMoveMemory" (ByVal Destination As LongPtr, ByVal Source As LongPtr, ByVal Length As Long)
#Else
    Private Declare Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
    Private Declare Function OpenClipboard Lib "user32" (ByVal hwnd As Long) As Long
    Private Declare Function CloseClipboard Lib "user32" () As Long
    Private Declare Function EmptyClipboard Lib "user32" () As Long
    Private Declare Function GetClipboardData Lib "user32" (ByVal wFormat As Long) As Long
    Private Declare Function GlobalLock Lib "kernel32" (ByVal hMem As Long) As Long
    Private Declare Function GlobalUnlock Lib "kernel32" (ByVal hMem As Long) As Long
    Private Declare Function lstrlenW Lib "kernel32" (ByVal lpString As Long) As Long
    Private Declare Sub CopyMemory Lib "kernel32" Alias "RtlMoveMemory" (ByVal Destination As Long, ByVal Source As Long, ByVal Length As Long)
#End If

Const CF_UNICODETEXT As Long = 13

#If VBA7 Then
    Private Declare PtrSafe Function GetForegroundWindow Lib "user32" () As LongPtr
    Private Declare PtrSafe Function SetForegroundWindow Lib "user32" (ByVal hwnd As LongPtr) As Long
    Private Declare PtrSafe Function BringWindowToTop Lib "user32" (ByVal hwnd As LongPtr) As Long
    Private Declare PtrSafe Function GetWindowThreadProcessId Lib "user32" (ByVal hwnd As LongPtr, ByRef lpdwProcessId As Long) As Long
    Private Declare PtrSafe Function GetCurrentThreadId Lib "kernel32" () As Long
    Private Declare PtrSafe Function AttachThreadInput Lib "user32" (ByVal idAttach As Long, ByVal idAttachTo As Long, ByVal fAttach As Long) As Long
#Else
    Private Declare Function GetForegroundWindow Lib "user32" () As Long
    Private Declare Function SetForegroundWindow Lib "user32" (ByVal hwnd As Long) As Long
    Private Declare Function BringWindowToTop Lib "user32" (ByVal hwnd As Long) As Long
    Private Declare Function GetWindowThreadProcessId Lib "user32" (ByVal hwnd As Long, ByRef lpdwProcessId As Long) As Long
    Private Declare Function GetCurrentThreadId Lib "kernel32" () As Long
    Private Declare Function AttachThreadInput Lib "user32" (ByVal idAttach As Long, ByVal idAttachTo As Long, ByVal fAttach As Long) As Long
#End If

' Re-asserts foreground/focus on whatever window Windows currently considers foreground
' (expected to be the browser at this point in the flow). This works around Windows'
' anti-focus-stealing protection, which can otherwise keep SendKeys landing on Excel
' instead of the newly opened browser tab.
Sub ForceFocusOnForegroundWindow()
    Dim hwndTarget As LongPtr
    Dim thisThread As Long, targetThread As Long
    Dim pid As Long

    hwndTarget = GetForegroundWindow()
    If hwndTarget = 0 Then Exit Sub

    thisThread = GetCurrentThreadId()
    targetThread = GetWindowThreadProcessId(hwndTarget, pid)

    If targetThread <> 0 And targetThread <> thisThread Then
        AttachThreadInput thisThread, targetThread, True
        BringWindowToTop hwndTarget
        SetForegroundWindow hwndTarget
        AttachThreadInput thisThread, targetThread, False
    Else
        SetForegroundWindow hwndTarget
    End If
    Sleep 150
End Sub

' ===================== MAIN MACRO =====================
Sub ExportDashboards()
    Dim ws As Worksheet
    Dim lastRow As Long, r As Long
    Dim colDashNum As Long, colScreenNum As Long, colUrlNum As Long, colRunFlagNum As Long
    Dim dashVal As String, screenVal As String, urlVal As String
    Dim runFlagVal As String
    Dim skippedCount As Long
    Dim totalToProcess As Long, doneCount As Long
    Dim startTime As Date, elapsedSec As Double, avgSecPerItem As Double, etaSec As Double
    Dim folderPath As String, filePath As String
    Dim clipText As String
    Dim errCount As Long
    Dim errLog As String

    If USE_SHEET_INDEX Then
        Set ws = ThisWorkbook.Sheets(SHEET_INDEX)
    Else
        Set ws = ThisWorkbook.Sheets(SHEET_NAME)
    End If

    colDashNum = ws.Range(COL_DASH & "1").Column
    colScreenNum = ws.Range(COL_SCREEN & "1").Column
    colUrlNum = ws.Range(COL_URL & "1").Column
    colRunFlagNum = ws.Range(COL_RUN_FLAG & "1").Column

    folderPath = ThisWorkbook.Path & "\txt\" & Format(Date, "yyyy-mm-dd")
    EnsureFolderExists ThisWorkbook.Path & "\txt"
    EnsureFolderExists folderPath

    lastRow = ws.Cells(ws.Rows.Count, colDashNum).End(xlUp).Row

    ' pre-count how many rows will actually be processed (flag = 1), so we can show "N of M" and an ETA
    totalToProcess = 0
    For r = HEADER_ROW + 1 To lastRow
        If Trim(ws.Cells(r, colRunFlagNum).Value) = "1" Then totalToProcess = totalToProcess + 1
    Next r

    MsgBox "Starting in 5 seconds. Do not touch mouse/keyboard while it runs!" & vbCrLf & _
           "Rows to process: " & totalToProcess, vbInformation
    Sleep 5000
    startTime = Now

    For r = HEADER_ROW + 1 To lastRow
        dashVal = Trim(ws.Cells(r, colDashNum).Value)
        screenVal = Trim(ws.Cells(r, colScreenNum).Value)
        urlVal = Trim(ws.Cells(r, colUrlNum).Value)
        runFlagVal = Trim(ws.Cells(r, colRunFlagNum).Value)
        If dashVal = "" And screenVal = "" And urlVal = "" Then GoTo NextRow

        If runFlagVal <> "1" Then
            skippedCount = skippedCount + 1
            GoTo NextRow
        End If

        filePath = folderPath & "\" & SafeFileName(dashVal) & "_" & SafeFileName(screenVal) & ".txt"
        doneCount = doneCount + 1

        On Error GoTo RowError
        ThisWorkbook.FollowHyperlink Address:=urlVal
        Sleep AFTER_OPEN_PAUSE
        Sleep WAIT_SECONDS * 1000

        clipText = CopyCurrentPageText()

        If Len(Trim(clipText)) = 0 Then
            errLog = errLog & "Empty: " & dashVal & " / " & screenVal & " -> " & urlVal & vbCrLf
        End If

        WriteTextUtf8 filePath, clipText
        If CLOSE_TAB_AFTER_COPY Then
            ForceFocusOnForegroundWindow
            SendKeys "^w"
            Sleep 500
        End If
        On Error GoTo 0

        ' progress update happens AFTER the browser interaction is fully done for this row,
        ' so it never touches Excel's UI right before we open the next link
        elapsedSec = (Now - startTime) * 86400
        If doneCount > 1 Then
            avgSecPerItem = elapsedSec / doneCount
            etaSec = avgSecPerItem * (totalToProcess - doneCount)
        Else
            etaSec = (WAIT_SECONDS + 3) * (totalToProcess - 1)
        End If
        UpdateProgress ws, doneCount, totalToProcess, dashVal, screenVal, etaSec
        Application.StatusBar = "[" & (r - HEADER_ROW) & "/" & (lastRow - HEADER_ROW) & "] " & dashVal & " / " & screenVal

        GoTo NextRow

RowError:
        errCount = errCount + 1
        errLog = errLog & "Error (" & dashVal & " / " & screenVal & "): " & Err.Description & vbCrLf
        Resume NextRow

NextRow:
    Next r

    Application.StatusBar = False
    Application.Caption = ""
    ws.Range(STATUS_CELL).Value = "Done. Processed: " & doneCount & ", skipped: " & skippedCount
    If errLog <> "" Then
        MsgBox "Done with " & errCount & " issue(s), " & skippedCount & " skipped (flag=0):" & vbCrLf & errLog, vbExclamation
    Else
        MsgBox "Done! " & skippedCount & " skipped (flag=0). Files saved to:" & vbCrLf & folderPath, vbInformation
    End If
End Sub

' ===================== HELPERS =====================

Function CopyCurrentPageText() As String
    ' clear the clipboard first so we don't accidentally read stale content
    If OpenClipboard(0) <> 0 Then
        EmptyClipboard
        CloseClipboard
    End If

    ForceFocusOnForegroundWindow
    SendKeys "^a", True
    Sleep KEY_PAUSE
    SendKeys "^c", True
    Sleep KEY_PAUSE

    CopyCurrentPageText = GetClipboardTextAPI()

    If Len(Trim(CopyCurrentPageText)) = 0 Then
        Sleep 1000
        CopyCurrentPageText = GetClipboardTextAPI()
    End If
End Function

Function GetClipboardTextAPI() As String
    Dim hMem As LongPtr
    Dim ptr As LongPtr
    Dim length As Long
    Dim result As String

    GetClipboardTextAPI = ""

    If OpenClipboard(0) = 0 Then Exit Function

    hMem = GetClipboardData(CF_UNICODETEXT)
    If hMem = 0 Then
        CloseClipboard
        Exit Function
    End If

    ptr = GlobalLock(hMem)
    If ptr <> 0 Then
        length = lstrlenW(ptr)
        If length > 0 Then
            result = Space$(length)
            CopyMemory StrPtr(result), ptr, length * 2
        End If
        GlobalUnlock hMem
    End If

    CloseClipboard
    GetClipboardTextAPI = result
End Function

Sub WriteTextUtf8(filePath As String, content As String)
    Dim stream As Object
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2
    stream.Charset = "utf-8"
    stream.Open
    stream.WriteText content
    stream.SaveToFile filePath, 2 ' adSaveCreateOverWrite
    stream.Close
End Sub

Function SafeFileName(nameVal As String) As String
    Dim bad As Variant, i As Integer
    Dim result As String
    result = nameVal
    bad = Array("\", "/", "*", "?", ":", Chr(34), "<", ">", "|")
    For i = LBound(bad) To UBound(bad)
        result = Replace(result, bad(i), "_")
    Next i
    SafeFileName = result
End Function

Sub EnsureFolderExists(path As String)
    If Dir(path, vbDirectory) = "" Then
        MkDir path
    End If
End Sub

Sub UpdateProgress(ws As Worksheet, doneCount As Long, totalToProcess As Long, dashVal As String, screenVal As String, etaSec As Double)
    Dim remaining As Long
    Dim etaMin As Long, etaRemSec As Long
    Dim statusText As String

    remaining = totalToProcess - doneCount
    etaMin = Int(etaSec / 60)
    etaRemSec = etaSec - etaMin * 60

    statusText = doneCount & " of " & totalToProcess & " (" & remaining & " left) - " & dashVal & " / " & screenVal & _
                 " - ETA ~" & etaMin & "m " & etaRemSec & "s"

    ws.Range(STATUS_CELL).Value = statusText
    Application.Caption = "Excel - " & doneCount & "/" & totalToProcess & " (" & remaining & " left)"
End Sub
