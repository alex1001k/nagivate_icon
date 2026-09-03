"""
Конвертирует JSON вида:

{
  "dashboards": [
    {
      "id": ..., "name": ...,
      "screens": [
        {
          "id": ..., "name": ...,
          "widget": [ {"id": ..., "name": ...}, ... ]   # или один словарь, или ключ "widgets"
        },
        ...
      ]
    },
    ...
  ]
}

в Excel-таблицу с колонками: dashboard_id, dashboard_name, widget_id, widget_name.

Запуск:
    python json_to_excel.py path/to/file.json [path/to/output.xlsx]

Если путь к выходному файлу не указан - результат сохранится рядом с исходным JSON,
с тем же именем и расширением .xlsx.
"""

import json
import sys
from pathlib import Path

import pandas as pd


def as_list(value):
    """Приводит значение к списку: None -> [], словарь -> [словарь], список -> как есть."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def get_widgets(screen: dict):
    """Виджеты экрана могут лежать под ключом 'widget' или 'widgets', как список или как один объект."""
    for key in ("widget", "widgets"):
        if key in screen:
            return as_list(screen[key])
    return []


def get_screens(dashboard: dict):
    """Экраны дашборда - под ключом 'screens' (или 'screen', на всякий случай)."""
    for key in ("screens", "screen"):
        if key in dashboard:
            return as_list(dashboard[key])
    return []


def get_dashboards(data):
    """Верхний уровень - под ключом 'dashboards', либо сам JSON уже список дашбордов."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("dashboards", "dashboard"):
            if key in data:
                return as_list(data[key])
    return []


def extract_rows(data):
    rows = []
    dashboards = get_dashboards(data)

    if not dashboards:
        print("⚠️  Не нашёл ключ 'dashboards' на верхнем уровне JSON - проверьте структуру файла.")
        return rows

    for dash in dashboards:
        if not isinstance(dash, dict):
            continue
        dash_id = dash.get("id")
        dash_name = dash.get("name")

        screens = get_screens(dash)
        if not screens:
            # у дашборда вообще нет экранов/виджетов - всё равно даём знать, что дашборд был
            continue

        for screen in screens:
            if not isinstance(screen, dict):
                continue
            widgets = get_widgets(screen)
            for widget in widgets:
                if not isinstance(widget, dict):
                    continue
                rows.append({
                    "dashboard_id": dash_id,
                    "dashboard_name": dash_name,
                    "widget_id": widget.get("id"),
                    "widget_name": widget.get("name"),
                })

    return rows


def main():
    if len(sys.argv) < 2:
        print("Использование: python json_to_excel.py path/to/file.json [path/to/output.xlsx]")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"Файл не найден: {json_path}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2])
    else:
        output_path = json_path.with_suffix(".xlsx")

    print(f"Читаю: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = extract_rows(data)
    print(f"Найдено строк (dashboard x widget): {len(rows)}")

    if not rows:
        print("Ничего не нашлось - результат сохранён не будет. Проверьте структуру JSON.")
        sys.exit(1)

    df = pd.DataFrame(rows, columns=["dashboard_id", "dashboard_name", "widget_id", "widget_name"])
    df.to_excel(output_path, index=False)
    print(f"Сохранено: {output_path.resolve()}")


if __name__ == "__main__":
    main()
