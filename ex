"""
Находит семантически похожие названия показателей, используя локальную модель
эмбеддингов (BAAI/bge-m3), и сохраняет все пары со схожестью выше порога в CSV.

Источник данных — CSV или Excel (.xlsx/.xls), определяется автоматически по расширению.

Про юнит (--group-column): юнит НЕ ограничивает поиск — связи ищутся среди ВСЕХ
показателей, в том числе между разными юнитами (это важно, например, для связи
показателя уровня компании с показателями юнитов). Юнит используется как
КОНТЕКСТ при построении эмбеддинга — то есть модель видит "Юнит А: Маржа", а не
голое "Маржа" — это мягкий сигнал, который влияет на вектор, но не блокирует
сравнение через границы юнитов.

Запуск (CSV, без юнита):
    python find_metric_links.py --input data.csv --column metric_name --output links.csv

Запуск (Excel, с юнитом как контекстом):
    python find_metric_links.py --input data.xlsx --column metric_name --group-column unit_name --output links.csv

Параметры:
    --input         путь к исходному файлу — .csv, .xlsx или .xls
    --sheet         имя/номер листа Excel (по умолчанию: первый лист, для CSV не используется)
    --column        имя столбца, из которого берутся названия показателей
    --group-column  (опционально) имя столбца-группы (например, юнит) — добавляется
                     как контекст в текст для эмбеддинга, но НЕ ограничивает поиск связей
    --output        путь к результирующему CSV со связями
    --model         путь к локальной модели или её имя на Hugging Face
                    (по умолчанию: ./bge-m3-local)
    --threshold     минимальная косинусная схожесть для включения пары (по умолчанию: 0.7)
    --sep           разделитель входного CSV (по умолчанию: автоопределение ; или ,; не используется для Excel)
"""

import argparse
import csv
import os
import sys
from itertools import combinations

import numpy as np
import pandas as pd


def read_input_auto(path: str, sep, sheet):
    """Читает CSV или Excel по расширению файла."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=sheet if sheet is not None else 0)
    # CSV
    if sep:
        return pd.read_csv(path, sep=sep, encoding="utf-8-sig")
    for candidate in (";", ","):
        df = pd.read_csv(path, sep=candidate, encoding="utf-8-sig")
        if df.shape[1] > 1:
            return df
    return df


def load_model(model_path: str):
    from sentence_transformers import SentenceTransformer
    print(f"Загружаю модель из '{model_path}'...", file=sys.stderr)
    model = SentenceTransformer(model_path)
    print(f"Модель загружена, размерность вектора: {model.get_sentence_embedding_dimension()}",
          file=sys.stderr)
    return model


def find_links_global(rows, embeddings, threshold, has_group):
    """Ищет пары среди ВСЕХ строк (юнит не ограничивает поиск).

    rows: список (metric_name,) или (group, metric_name) в зависимости от has_group.
    Количество связей на показатель не ограничено.
    """
    n = len(rows)
    links = []
    for i, j in combinations(range(n), 2):
        sim = float(np.dot(embeddings[i], embeddings[j]))
        if sim >= threshold:
            if has_group:
                group_a, metric_a = rows[i]
                group_b, metric_b = rows[j]
                links.append((metric_a, group_a, metric_b, group_b, group_a == group_b, round(sim, 4)))
            else:
                links.append((rows[i][0], rows[j][0], round(sim, 4)))
    return links


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="путь к исходному файлу: .csv, .xlsx или .xls")
    parser.add_argument("--sheet", default=None, help="имя/номер листа Excel (по умолчанию первый лист)")
    parser.add_argument("--column", required=True, help="столбец с названиями показателей")
    parser.add_argument("--group-column", default=None,
                         help="столбец-группа (например, юнит) — добавляется как контекст в эмбеддинг, "
                              "но не ограничивает поиск связей")
    parser.add_argument("--output", required=True, help="путь к результирующему CSV со связями")
    parser.add_argument("--model", default="./bge-m3-local", help="путь к локальной модели или имя на HF")
    parser.add_argument("--threshold", type=float, default=0.7, help="порог косинусной схожести (по умолчанию 0.7)")
    parser.add_argument("--sep", default=None, help="разделитель входного CSV (не используется для Excel)")
    args = parser.parse_args()

    # ── 1. Читаем источник (CSV или Excel) ──
    sheet = args.sheet
    if sheet is not None and str(sheet).isdigit():
        sheet = int(sheet)
    df = read_input_auto(args.input, args.sep, sheet)

    if args.column not in df.columns:
        print(f"Ошибка: колонки '{args.column}' нет в файле. Доступные колонки: {list(df.columns)}",
              file=sys.stderr)
        sys.exit(1)
    if args.group_column and args.group_column not in df.columns:
        print(f"Ошибка: колонки '{args.group_column}' нет в файле. Доступные колонки: {list(df.columns)}",
              file=sys.stderr)
        sys.exit(1)

    # ── 2. Собираем уникальные строки ──
    if args.group_column:
        pairs_df = df[[args.group_column, args.column]].dropna()
        pairs_df = pairs_df.astype(str).apply(lambda s: s.str.strip())
        pairs_df = pairs_df[pairs_df[args.column] != ""]
        pairs_df = pairs_df.drop_duplicates().sort_values([args.group_column, args.column])
        rows = list(zip(pairs_df[args.group_column].tolist(), pairs_df[args.column].tolist()))
        # текст для эмбеддинга — юнит как контекст, не отдельный жёсткий фильтр
        texts = [f"{g}: {m}" for g, m in rows]
        print(f"Найдено уникальных пар (группа, показатель): {len(rows)}", file=sys.stderr)
    else:
        names = sorted({str(v).strip() for v in df[args.column].dropna() if str(v).strip()})
        rows = [(n,) for n in names]
        texts = names
        print(f"Найдено уникальных значений в '{args.column}': {len(names)}", file=sys.stderr)

    if len(rows) < 2:
        print("Недостаточно уникальных значений для сравнения (нужно минимум 2).", file=sys.stderr)
        sys.exit(1)

    # ── 3. Считаем эмбеддинги ──
    model = load_model(args.model)
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    # ── 4. Ищем пары выше порога (глобально, юнит не ограничивает) ──
    print(f"Ищу пары со схожестью >= {args.threshold}...", file=sys.stderr)
    links = find_links_global(rows, embeddings, args.threshold, has_group=bool(args.group_column))
    print(f"Найдено связей: {len(links)}", file=sys.stderr)

    # ── 5. Сохраняем результат ──
    links.sort(key=lambda x: -x[-1])
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        if args.group_column:
            writer.writerow(["metric_a", "group_a", "metric_b", "group_b", "same_group", "similarity"])
        else:
            writer.writerow(["metric_a", "metric_b", "similarity"])
        writer.writerows(links)

    print(f"Готово. Результат сохранён в '{args.output}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
