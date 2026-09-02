Attribute VB_Name = "DashExport"
Option Explicit

' ===================== НАСТРОЙКИ =====================
Const SHEET_NAME As String = "Справочник"   ' лист с колонками Дэш / Экран / Ссылка
Const HEADER_ROW As Long = 1                 ' строка с заголовками колонок
Const WAIT_SECONDS As Long = 20              ' пауза на прогрузку дашборда
Const AFTER_OPEN_PAUSE As Long = 2000        ' мс, доп. пауза сразу после открытия ссылки
Const KEY_PAUSE As Long = 400                ' мс, пауза между Ctrl+A и Ctrl+C

' Windows API для точных пауз (Application.Wait даёт только секунды)
#If VBA7 Then
    Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
#Else
    Private Declare Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
#End If

' ===================== ОСНОВНОЙ МАКРОС =====================
Sub ExportDashboards()
    Dim ws As Worksheet
    Dim lastRow As Long, r As Long
    Dim colDash As Long, colScreen As Long, colUrl As Long
    Dim dash As String, screen As String, url As String
    Dim folderPath As String, filePath As String
    Dim clipText As String
    Dim errCount As Long
    Dim errLog As String

    Set ws = ThisWorkbook.Sheets(SHEET_NAME)

    ' находим нужные колонки по заголовкам (устойчиво к порядку колонок)
    colDash = FindColumn(ws, "Дэш")
    colScreen = FindColumn(ws, "Экран")
    colUrl = FindColumn(ws, "Ссылка")
    If colDash = 0 Or colScreen = 0 Or colUrl = 0 Then
        MsgBox "Не нашёл одну из колонок: Дэш / Экран / Ссылка на листе '" & SHEET_NAME & "'", vbCritical
        Exit Sub
    End If

    ' папка txt/ГГГГ-ММ-ДД рядом с файлом
    folderPath = ThisWorkbook.Path & "\txt\" & Format(Date, "yyyy-mm-dd")
    EnsureFolderExists ThisWorkbook.Path & "\txt"
    EnsureFolderExists folderPath

    lastRow = ws.Cells(ws.Rows.Count, colDash).End(xlUp).Row

    MsgBox "Через 5 секунд начнётся сбор. Не трогайте мышь и клавиатуру во время работы!", vbInformation
    Sleep 5000

    For r = HEADER_ROW + 1 To lastRow
        dash = Trim(ws.Cells(r, colDash).Value)
        screen = Trim(ws.Cells(r, colScreen).Value)
        url = Trim(ws.Cells(r, colUrl).Value)
        If dash = "" And screen = "" And url = "" Then GoTo NextRow

        filePath = folderPath & "\" & SafeFileName(dash) & "_" & SafeFileName(screen) & ".txt"

        Application.StatusBar = "[" & (r - HEADER_ROW) & "/" & (lastRow - HEADER_ROW) & "] " & dash & " / " & screen

        On Error GoTo RowError
        ThisWorkbook.FollowHyperlink Address:=url
        Sleep AFTER_OPEN_PAUSE
        Sleep WAIT_SECONDS * 1000

        clipText = CopyCurrentPageText()

        If Len(Trim(clipText)) = 0 Then
            errLog = errLog & "Пусто: " & dash & " / " & screen & " -> " & url & vbCrLf
        End If

        WriteTextUtf8 filePath, clipText
        SendKeys "^w"
        Sleep 500
        On Error GoTo 0
        GoTo NextRow

RowError:
        errCount = errCount + 1
        errLog = errLog & "Ошибка (" & dash & " / " & screen & "): " & Err.Description & vbCrLf
        Resume NextRow

NextRow:
    Next r

    Application.StatusBar = False
    If errLog <> "" Then
        MsgBox "Готово с замечаниями (" & errCount & " ошибок):" & vbCrLf & errLog, vbExclamation
    Else
        MsgBox "Готово! Файлы сохранены в:" & vbCrLf & folderPath, vbInformation
    End If
End Sub

' ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================

Function CopyCurrentPageText() As String
    Dim data As MSForms.DataObject
    Set data = New MSForms.DataObject

    ' на всякий случай чистим буфер, чтобы не спутать со старым содержимым
    data.SetText ""
    data.PutInClipboard

    SendKeys "^a", True
    Sleep KEY_PAUSE
    SendKeys "^c", True
    Sleep KEY_PAUSE

    On Error Resume Next
    data.GetFromClipboard
    CopyCurrentPageText = data.GetText(1)
    On Error GoTo 0

    ' иногда буфер не успевает обновиться - пробуем ещё раз
    If Len(Trim(CopyCurrentPageText)) = 0 Then
        Sleep 1000
        On Error Resume Next
        data.GetFromClipboard
        CopyCurrentPageText = data.GetText(1)
        On Error GoTo 0
    End If
End Function

Sub WriteTextUtf8(filePath As String, content As String)
    ' обычный VBA Print пишет не в UTF-8, а нам нужна кириллица без потерь -
    ' используем ADODB.Stream, чтобы гарантированно сохранить в UTF-8
    Dim stream As Object
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2 ' текстовый режим
    stream.Charset = "utf-8"
    stream.Open
    stream.WriteText content
    stream.SaveToFile filePath, 2 ' 2 = adSaveCreateOverWrite
    stream.Close
End Sub

Function SafeFileName(name As String) As String
    Dim bad As Variant, i As Integer
    Dim result As String
    result = name
    bad = Array("\", "/", "*", "?", ":", """", "<", ">", "|")
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

Function FindColumn(ws As Worksheet, headerText As String) As Long
    Dim c As Long
    Dim lastCol As Long
    lastCol = ws.Cells(HEADER_ROW, ws.Columns.Count).End(xlToLeft).Column
    For c = 1 To lastCol
        If Trim(ws.Cells(HEADER_ROW, c).Value) = headerText Then
            FindColumn = c
            Exit Function
        End If
    Next c
    FindColumn = 0
End Function
