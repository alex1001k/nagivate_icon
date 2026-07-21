"""
Находит семантически похожие названия показателей в CSV-файле,
используя локальную модель эмбеддингов (BAAI/bge-m3), и сохраняет
все пары со схожестью выше порога в отдельный CSV.

Запуск:
    python find_metric_links.py --input data.csv --column metric_name --output links.csv

Параметры:
    --input       путь к исходному CSV
    --column      имя столбца, из которого берутся названия показателей
    --output      путь к результирующему CSV со связями
    --model       путь к локальной модели или её имя на Hugging Face
                  (по умолчанию: ./bge-m3-local)
    --threshold   минимальная косинусная схожесть для включения пары (по умолчанию: 0.7)
    --sep         разделитель во входном CSV (по умолчанию: автоопределение ; или ,)
"""

import argparse
import csv
import sys
from itertools import combinations

import numpy as np
import pandas as pd


def read_csv_auto_sep(path: str, sep: str | None):
    """Читает CSV, при sep=None пробует ';' и ',' и берёт тот, что даёт >1 колонки."""
    if sep:
        return pd.read_csv(path, sep=sep, encoding="utf-8-sig")
    for candidate in (";", ","):
        df = pd.read_csv(path, sep=candidate, encoding="utf-8-sig")
        if df.shape[1] > 1:
            return df
    # если ни один разделитель не дал >1 колонки — вернём последний вариант как есть
    return df


def load_model(model_path: str):
    from sentence_transformers import SentenceTransformer
    print(f"Загружаю модель из '{model_path}'...", file=sys.stderr)
    model = SentenceTransformer(model_path)
    print(f"Модель загружена, размерность вектора: {model.get_sentence_embedding_dimension()}",
          file=sys.stderr)
    return model


def find_links(names: list[str], embeddings: np.ndarray, threshold: float):
    """Возвращает список пар (name_a, name_b, similarity) со схожестью >= threshold.

    Перебираются все уникальные неупорядоченные пары (i < j), поэтому:
    - каждая пара встречается ровно один раз (не дублируется как A-B и B-A)
    - количество связей на один показатель НЕ ограничено — если показатель
      похож на 10 других выше порога, все 10 пар попадут в результат
    """
    n = len(names)
    links = []
    for i, j in combinations(range(n), 2):
        sim = float(np.dot(embeddings[i], embeddings[j]))
        if sim >= threshold:
            links.append((names[i], names[j], round(sim, 4)))
    return links


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="путь к исходному CSV")
    parser.add_argument("--column", required=True, help="столбец с названиями показателей")
    parser.add_argument("--output", required=True, help="путь к результирующему CSV со связями")
    parser.add_argument("--model", default="./bge-m3-local", help="путь к локальной модели или имя на HF")
    parser.add_argument("--threshold", type=float, default=0.7, help="порог косинусной схожести (по умолчанию 0.7)")
    parser.add_argument("--sep", default=None, help="разделитель входного CSV (по умолчанию автоопределение)")
    args = parser.parse_args()

    # ── 1. Читаем CSV и берём уникальные значения из указанной колонки ──
    df = read_csv_auto_sep(args.input, args.sep)
    if args.column not in df.columns:
        print(f"Ошибка: колонки '{args.column}' нет в файле. Доступные колонки: {list(df.columns)}",
              file=sys.stderr)
        sys.exit(1)

    names = sorted({str(v).strip() for v in df[args.column].dropna() if str(v).strip()})
    print(f"Найдено уникальных значений в '{args.column}': {len(names)}", file=sys.stderr)

    if len(names) < 2:
        print("Недостаточно уникальных значений для сравнения (нужно минимум 2).", file=sys.stderr)
        sys.exit(1)

    # ── 2. Считаем эмбеддинги ──
    model = load_model(args.model)
    embeddings = model.encode(names, normalize_embeddings=True, show_progress_bar=True)

    # ── 3. Ищем пары выше порога ──
    print(f"Ищу пары со схожестью >= {args.threshold}...", file=sys.stderr)
    links = find_links(names, embeddings, args.threshold)
    print(f"Найдено связей: {len(links)}", file=sys.stderr)

    # ── 4. Сохраняем результат ──
    links.sort(key=lambda x: -x[2])  # сначала самые уверенные связи
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["metric_a", "metric_b", "similarity"])
        writer.writerows(links)

    print(f"Готово. Результат сохранён в '{args.output}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
