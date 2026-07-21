"""
Ищет аномалии в СОВМЕСТНОЙ динамике связанных показателей с помощью модели
VAR (Vector Autoregression, statsmodels) — не нейросеть, классическая статистика,
считается на лету на ваших данных, ничего скачивать/переносить в контур не нужно.

Идея: модель учится предсказывать каждый показатель по истории ВСЕХ связанных
показателей вместе. Если реальное значение сильно отличается от того, что
ожидалось с учётом совместного движения группы — это и есть аномалия.

Ожидаемый формат входного CSV/Excel с фактами (как в my_report.py):
    unit_name;metric_name;d_date;fact_value

ДВА СПОСОБА ЗАДАТЬ ГРУППЫ СВЯЗАННЫХ ПОКАЗАТЕЛЕЙ:

1) Автоматически, из результата find_metric_links.py (РЕКОМЕНДУЕТСЯ,
   не нужно ничего перечислять руками):
    python find_var_anomalies.py --input facts.csv --links-file links.csv --output anomalies.csv

   Скрипт сам построит группы связанных показателей (по принципу "если A связано
   с B, а B связано с C — все трое в одной группе") и прогонит VAR по каждой
   группе из 2+ показателей отдельно.

2) Вручную, если хотите проверить конкретный набор показателей:
    python find_var_anomalies.py --input facts.csv --series "Юнит А:Маржа,Юнит А:Выручка" --output anomalies.csv

Параметры:
    --input            путь к CSV/Excel с фактами (unit_name, metric_name, d_date, fact_value)
    --sheet            имя/номер листа Excel (не используется для CSV)
    --links-file       CSV из find_metric_links.py — автоматическая группировка связей
    --series           ручной список через запятую (см. способ 2 выше) — альтернатива --links-file
    --min-cluster-size минимальный размер группы для анализа (по умолчанию 2)
    --max-cluster-size максимальный размер группы — более крупные пропускаются с предупреждением
                       (по умолчанию 8, чтобы не собрать всё в одну гигантскую группу через цепочку связей)
    --output           путь к результирующему CSV с аномалиями
    --maxlags          максимальное число лагов для перебора (по умолчанию 3)
    --no-diff          не брать разности (по умолчанию берутся, чтобы убрать тренд)
    --threshold        порог |z-score| остатка (по умолчанию 2.5)
    --sep              разделитель CSV (по умолчанию автоопределение, не используется для Excel)
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


def parse_series_spec(spec: str):
    """'Юнит:Показатель' -> (unit, metric); 'Показатель' -> (None, metric)."""
    items = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            unit, metric = part.split(":", 1)
            items.append((unit.strip(), metric.strip()))
        else:
            items.append((None, part.strip()))
    return items


class UnionFind:
    """Простое объединение множеств — чтобы собрать связанные показатели в группы:
    если A-B связаны и B-C связаны, все трое попадают в одну группу."""

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
    """Строит группы связанных показателей из результата find_metric_links.py.

    Поддерживает оба формата вывода этого скрипта:
      - без юнита: metric_a;metric_b;similarity
      - с юнитом:  metric_a;group_a;metric_b;group_b;same_group;similarity

    Возвращает список кластеров, каждый — список (unit_or_None, metric_name).
    """
    links = read_input_auto(links_path, None)
    uf = UnionFind()

    has_group = "group_a" in links.columns and "group_b" in links.columns

    def node_id(row, side):
        if has_group:
            unit = str(row[f"group_{side}"]).strip()
            metric = str(row[f"metric_{side}"]).strip()
            return (unit, metric)
        else:
            return (None, str(row[f"metric_{side}"]).strip())

    nodes = set()
    for _, row in links.iterrows():
        a = node_id(row, "a")
        b = node_id(row, "b")
        nodes.add(a)
        nodes.add(b)
        uf.union(a, b)

    groups = defaultdict(list)
    for node in nodes:
        root = uf.find(node)
        groups[root].append(node)

    return list(groups.values())


def build_wide_matrix(df: pd.DataFrame, series_specs):
    """Собирает широкую таблицу: строки - даты, колонки - запрошенные ряды.

    Если unit не задан для ряда - значения суммируются по всем юнитам за дату.
    """
    df = df.copy()
    df["unit_name"] = df["unit_name"].astype(str).str.strip()
    df["metric_name"] = df["metric_name"].astype(str).str.strip()
    df["d_date"] = df["d_date"].astype(str).str.strip()
    df["fact_value"] = pd.to_numeric(df["fact_value"], errors="coerce")

    columns = {}
    labels = []
    for unit, metric in series_specs:
        label = f"{unit}:{metric}" if unit else metric
        if unit:
            sub = df[(df["unit_name"] == unit) & (df["metric_name"] == metric)]
        else:
            sub = df[df["metric_name"] == metric]
        if sub.empty:
            print(f"Предупреждение: для ряда '{label}' не найдено ни одной строки в данных.",
                  file=sys.stderr)
            continue
        series = sub.groupby("d_date")["fact_value"].sum()
        columns[label] = series
        labels.append(label)

    if not columns:
        return None

    wide = pd.DataFrame(columns).sort_index()
    return wide[labels]


def fit_var_and_find_anomalies(wide: pd.DataFrame, maxlags: int, use_diff: bool, threshold: float):
    from statsmodels.tsa.api import VAR

    work = wide.diff().dropna() if use_diff else wide.dropna()
    if len(work) < maxlags + 5:
        return None, None, f"недостаточно общих периодов ({len(work)}) для лагов={maxlags}"

    model = VAR(work)
    try:
        order_result = model.select_order(maxlags=maxlags)
        best_lag = order_result.aic if order_result.aic and order_result.aic > 0 else 1
    except Exception:
        best_lag = 1

    fitted = model.fit(maxlags=best_lag)
    residuals = fitted.resid
    z = (residuals - residuals.mean()) / residuals.std(ddof=0)

    records = []
    for date, row in z.iterrows():
        for col in z.columns:
            zscore = row[col]
            if pd.notna(zscore):
                records.append({
                    "date": date,
                    "series": col,
                    "residual": round(float(residuals.loc[date, col]), 4),
                    "zscore": round(float(zscore), 3),
                    "is_anomaly": abs(zscore) >= threshold,
                })

    return pd.DataFrame(records), best_lag, None


def process_cluster(df, cluster, cluster_id, args):
    """Прогоняет VAR по одному кластеру связанных показателей, возвращает DataFrame с аномалиями."""
    labels = [f"{u}:{m}" if u else m for u, m in cluster]
    print(f"[Кластер {cluster_id}] {len(cluster)} показателей: {', '.join(labels)}", file=sys.stderr)

    wide = build_wide_matrix(df, cluster)
    if wide is None or wide.shape[1] < 2:
        print(f"[Кластер {cluster_id}] пропущен: меньше 2 рядов с данными.", file=sys.stderr)
        return None

    result, best_lag, error = fit_var_and_find_anomalies(wide, args.maxlags, not args.no_diff, args.threshold)
    if error:
        print(f"[Кластер {cluster_id}] пропущен: {error}.", file=sys.stderr)
        return None

    result["cluster_id"] = cluster_id
    result["cluster_members"] = ", ".join(labels)
    n_anom = int(result["is_anomaly"].sum())
    print(f"[Кластер {cluster_id}] лагов={best_lag}, аномалий: {n_anom} из {len(result)}", file=sys.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="CSV/Excel с фактами")
    parser.add_argument("--sheet", default=0, help="лист Excel (не используется для CSV)")
    parser.add_argument("--unit-column", default=None,
                         help="название колонки с юнитом в вашем файле. Если юнита нет вообще — не указывайте, "
                              "все данные будут считаться одним общим 'юнитом'")
    parser.add_argument("--metric-column", default="metric_name",
                         help="название колонки с показателем в вашем файле (по умолчанию metric_name)")
    parser.add_argument("--date-column", default="d_date",
                         help="название колонки с датой/периодом в вашем файле (по умолчанию d_date)")
    parser.add_argument("--value-column", default="fact_value",
                         help="название колонки со значением в вашем файле (по умолчанию fact_value)")
    parser.add_argument("--links-file", default=None,
                         help="CSV из find_metric_links.py — автоматическая группировка связей")
    parser.add_argument("--series", default=None,
                         help="ручной список через запятую: 'Показатель' или 'Юнит:Показатель'")
    parser.add_argument("--min-cluster-size", type=int, default=2)
    parser.add_argument("--max-cluster-size", type=int, default=8)
    parser.add_argument("--output", required=True, help="путь к результирующему CSV")
    parser.add_argument("--maxlags", type=int, default=3)
    parser.add_argument("--no-diff", action="store_true")
    parser.add_argument("--threshold", type=float, default=2.5)
    parser.add_argument("--sep", default=None)
    args = parser.parse_args()

    if not args.links_file and not args.series:
        print("Ошибка: укажите либо --links-file (автоматически), либо --series (вручную).", file=sys.stderr)
        sys.exit(1)

    df = read_input_auto(args.input, args.sep)

    # ── Приводим колонки пользователя к внутренним именам unit_name/metric_name/d_date/fact_value ──
    rename_map = {}
    for user_col, internal_name in [
        (args.metric_column, "metric_name"),
        (args.date_column, "d_date"),
        (args.value_column, "fact_value"),
    ]:
        if user_col not in df.columns:
            print(f"Ошибка: колонки '{user_col}' нет в файле. Доступные колонки: {list(df.columns)}",
                  file=sys.stderr)
            sys.exit(1)
        rename_map[user_col] = internal_name
    df = df.rename(columns=rename_map)

    if args.unit_column:
        if args.unit_column not in df.columns:
            print(f"Ошибка: колонки '{args.unit_column}' нет в файле. Доступные колонки: {list(df.columns)}",
                  file=sys.stderr)
            sys.exit(1)
        df = df.rename(columns={args.unit_column: "unit_name"})
    else:
        df["unit_name"] = "_all_"  # юнита нет - считаем все данные одним общим "юнитом"
        print("Юнит не указан (--unit-column не задан) — все данные считаются одной общей группой.",
              file=sys.stderr)

    # ── Собираем список кластеров для обработки ──
    if args.links_file:
        clusters = build_clusters_from_links(args.links_file)
        clusters = [c for c in clusters if args.min_cluster_size <= len(c) <= args.max_cluster_size]
        skipped_large = [c for c in build_clusters_from_links(args.links_file) if len(c) > args.max_cluster_size]
        if skipped_large:
            print(f"Пропущено слишком крупных групп (>{args.max_cluster_size} показателей): "
                  f"{len(skipped_large)} — вероятно, связи объединились в одну большую цепочку. "
                  f"Проверьте --threshold в find_metric_links.py.", file=sys.stderr)
        print(f"Найдено групп для анализа: {len(clusters)}", file=sys.stderr)
    else:
        clusters = [parse_series_spec(args.series)]
        if len(clusters[0]) < 2:
            print("Ошибка: для VAR нужно минимум 2 связанных показателя.", file=sys.stderr)
            sys.exit(1)

    if not clusters:
        print("Не найдено ни одной группы подходящего размера для анализа.", file=sys.stderr)
        sys.exit(1)

    all_results = []
    for i, cluster in enumerate(clusters, start=1):
        res = process_cluster(df, cluster, i, args)
        if res is not None:
            all_results.append(res)

    if not all_results:
        print("Ни по одной группе не удалось построить модель.", file=sys.stderr)
        sys.exit(1)

    final = pd.concat(all_results, ignore_index=True)
    final = final.sort_values("zscore", key=lambda s: s.abs(), ascending=False)
    final.to_csv(args.output, sep=";", index=False, encoding="utf-8-sig")

    total_anom = int(final["is_anomaly"].sum())
    print(f"Готово. Всего аномальных точек: {total_anom} из {len(final)}, "
          f"по {len(all_results)} группам. Результат: '{args.output}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
