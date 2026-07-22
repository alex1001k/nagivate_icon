"""
Ищет аномалии в показателях с помощью прогноза модели Chronos-2 (Amazon) —
предобученная нейросеть для временных рядов, не требует обучения на ваших
данных (zero-shot).

Логика: для каждого показателя, скользящим окном по истории, модель строит
прогноз с доверительным интервалом (квантили) на основе предыдущих периодов.
Если реальное значение вышло за пределы интервала — это аномалия.

В отличие от VAR, здесь прогноз ПО КАЖДОМУ показателю строится отдельно
(univariate) — группировка из --links-file используется только для того,
чтобы пометить в выводе, какие показатели связаны между собой, для контекста.

Установка перед использованием:
    pip install chronos-forecasting "pandas[pyarrow]"

Запуск:
    python find_chronos_anomalies.py --input fact_pokazateli.csv \\
        --metric-column pokaz_name --date-column date_ --value-column fact \\
        --links-file output_links.csv --output anomalies_chronos.csv

Параметры:
    --input            CSV/Excel с фактами
    --sheet            лист Excel (не используется для CSV)
    --unit-column       (опционально) колонка юнита — если юнита нет, не указывайте
    --metric-column    колонка с названием показателя
    --date-column      колонка с датой/периодом
    --value-column     колонка со значением
    --links-file       CSV из find_metric_links.py — для группировки в выводе
    --model            имя модели на Hugging Face или локальный путь (по умолчанию amazon/chronos-2)
    --context-length   сколько предыдущих периодов видит модель при каждом прогнозе (по умолчанию 8)
    --min-history      минимальное число периодов истории для показателя, чтобы его анализировать (по умолчанию 12)
    --quantile-low      нижний квантиль интервала (по умолчанию 0.1)
    --quantile-high     верхний квантиль интервала (по умолчанию 0.9)
    --sep              разделитель CSV (по умолчанию автоопределение)
"""

import argparse
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd


def read_input_auto(path: str, sep, sheet=0):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=sheet)
    if sep:
        return pd.read_csv(path, sep=sep, encoding="utf-8-sig")
    for candidate in (";", ","):
        df = pd.read_csv(path, sep=candidate, encoding="utf-8-sig")
        if df.shape[1] > 1:
            return df
    return df


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_clusters_from_links(links_path: str):
    links = read_input_auto(links_path, None)
    uf = UnionFind()
    has_group = "group_a" in links.columns and "group_b" in links.columns

    def node_id(row, side):
        if has_group:
            return (str(row[f"group_{side}"]).strip(), str(row[f"metric_{side}"]).strip())
        return (None, str(row[f"metric_{side}"]).strip())

    nodes = set()
    for _, row in links.iterrows():
        a, b = node_id(row, "a"), node_id(row, "b")
        nodes.add(a)
        nodes.add(b)
        uf.union(a, b)

    groups = defaultdict(list)
    for node in nodes:
        groups[uf.find(node)].append(node)
    return list(groups.values())


def load_chronos_pipeline(model_name: str):
    from chronos import Chronos2Pipeline
    print(f"Загружаю модель '{model_name}'...", file=sys.stderr)
    pipeline = Chronos2Pipeline.from_pretrained(model_name, device_map="cpu")
    print("Модель загружена.", file=sys.stderr)
    return pipeline


def forecast_quantiles(pipeline, history_values: list, q_low: float, q_high: float):
    """Прогноз на 1 период вперёд по истории. Возвращает (low, median, high).

    ВАЖНО: эта функция — единственное место, где вызывается сама модель.
    Если вы захотите использовать другую модель вместо Chronos (например,
    Moirai) — достаточно переписать только эту функцию, остальной код
    (группировка, скользящее окно, сравнение с фактом) не изменится.
    """
    ts_df = pd.DataFrame({
        "id": ["series"] * len(history_values),
        "timestamp": pd.RangeIndex(len(history_values)),
        "target": history_values,
    })
    pred = pipeline.predict_df(
        ts_df, prediction_length=1,
        quantile_levels=[q_low, 0.5, q_high],
        id_column="id", timestamp_column="timestamp", target="target",
    )
    row = pred.iloc[0]
    return float(row[str(q_low)]), float(row["0.5"]), float(row[str(q_high)])


def rolling_backtest(dates, values, forecast_fn, context_length, min_history, check_last_n=None):
    """Идёт по ряду периодами, на каждом шаге прогнозирует следующий период
    по истории ДО него (не подглядывая в будущее), сравнивает с фактом.

    check_last_n: если задано, проверяются ТОЛЬКО последние N периодов ряда
    (а не вся история от min_history до конца) — резко сокращает число
    вызовов модели при большом числе показателей.
    """
    start = min_history
    if check_last_n is not None:
        start = max(min_history, len(values) - check_last_n)

    records = []
    for t in range(start, len(values)):
        history = values[max(0, t - context_length):t]
        try:
            low, median, high = forecast_fn(history)
        except Exception as e:
            print(f"  Предупреждение: прогноз не удался на периоде {dates[t]}: {e}", file=sys.stderr)
            continue

        actual = values[t]
        is_anomaly = not (low <= actual <= high)
        records.append({
            "date": dates[t], "actual": actual,
            "forecast_low": round(low, 4), "forecast_median": round(median, 4),
            "forecast_high": round(high, 4), "is_anomaly": is_anomaly,
        })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True)
    parser.add_argument("--sheet", default=0)
    parser.add_argument("--unit-column", default=None)
    parser.add_argument("--metric-column", default="metric_name")
    parser.add_argument("--date-column", default="d_date")
    parser.add_argument("--value-column", default="fact_value")
    parser.add_argument("--links-file", default=None, help="CSV из find_metric_links.py — для группировки в выводе")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="amazon/chronos-2")
    parser.add_argument("--context-length", type=int, default=8)
    parser.add_argument("--min-history", type=int, default=12)
    parser.add_argument("--check-last-n", type=int, default=None,
                         help="проверять только последние N периодов каждого показателя "
                              "(резко ускоряет работу при большом числе показателей; "
                              "по умолчанию проверяется вся доступная история)")
    parser.add_argument("--quantile-low", type=float, default=0.1)
    parser.add_argument("--quantile-high", type=float, default=0.9)
    parser.add_argument("--sep", default=None)
    args = parser.parse_args()

    df = read_input_auto(args.input, args.sep)

    rename_map = {
        args.metric_column: "metric_name",
        args.date_column: "d_date",
        args.value_column: "fact_value",
    }
    for user_col in rename_map:
        if user_col not in df.columns:
            print(f"Ошибка: колонки '{user_col}' нет в файле. Доступные: {list(df.columns)}", file=sys.stderr)
            sys.exit(1)
    df = df.rename(columns=rename_map)

    if args.unit_column:
        if args.unit_column not in df.columns:
            print(f"Ошибка: колонки '{args.unit_column}' нет в файле.", file=sys.stderr)
            sys.exit(1)
        df = df.rename(columns={args.unit_column: "unit_name"})
    else:
        df["unit_name"] = "_all_"

    df["metric_name"] = df["metric_name"].astype(str).str.strip()
    df["unit_name"] = df["unit_name"].astype(str).str.strip()
    df["d_date"] = df["d_date"].astype(str).str.strip()
    df["fact_value"] = pd.to_numeric(df["fact_value"], errors="coerce")

    cluster_of = {}
    cluster_members = {}
    if args.links_file:
        clusters = build_clusters_from_links(args.links_file)
        for i, cluster in enumerate(clusters, start=1):
            labels = [f"{u}:{m}" if u else m for u, m in cluster]
            for u, m in cluster:
                cluster_of[(u or "_all_", m)] = i
            cluster_members[i] = ", ".join(labels)
        print(f"Найдено групп из links-file: {len(clusters)}", file=sys.stderr)

    pipeline = load_chronos_pipeline(args.model)

    all_results = []
    unique_series = df[["unit_name", "metric_name"]].drop_duplicates().values.tolist()
    print(f"Всего рядов для анализа: {len(unique_series)}", file=sys.stderr)

    for unit, metric in unique_series:
        sub = df[(df["unit_name"] == unit) & (df["metric_name"] == metric)]
        sub = sub.groupby("d_date")["fact_value"].sum().sort_index()
        dates, values = sub.index.tolist(), sub.values.tolist()

        if len(values) < args.min_history + 1:
            print(f"  Пропущен '{metric}' ({unit}): {len(values)} периодов "
                  f"< минимума {args.min_history + 1}.", file=sys.stderr)
            continue

        label = f"{unit}:{metric}" if unit != "_all_" else metric
        print(f"Обрабатываю '{label}' ({len(values)} периодов)...", file=sys.stderr)

        records = rolling_backtest(
            dates, values,
            forecast_fn=lambda h: forecast_quantiles(pipeline, h, args.quantile_low, args.quantile_high),
            context_length=args.context_length, min_history=args.min_history,
            check_last_n=args.check_last_n,
        )
        for r in records:
            r["series"] = label
            r["cluster_id"] = cluster_of.get((unit, metric), None)
            r["cluster_members"] = cluster_members.get(cluster_of.get((unit, metric)), "")
        all_results.extend(records)

    if not all_results:
        print("Не удалось получить ни одного прогноза (недостаточно истории?).", file=sys.stderr)
        sys.exit(1)

    result_df = pd.DataFrame(all_results)
    result_df = result_df.sort_values("is_anomaly", ascending=False)
    result_df.to_csv(args.output, sep=";", index=False, encoding="utf-8-sig")

    n_anom = int(result_df["is_anomaly"].sum())
    print(f"Готово. Аномальных точек: {n_anom} из {len(result_df)}. Результат: '{args.output}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
