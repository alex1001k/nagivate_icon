#!/usr/bin/env python3
"""
analyze_query_lineage.py
=========================
Анализ связей между таблицами на основе реальных SQL-запросов пользователей.

Что делает:
  1. Читает CSV с колонкой query_text (запросы к витринам)
  2. Парсит каждый запрос через sqlglot
  3. Извлекает связи двух типов:
       - JOIN-связи (высокая уверенность) — таблицы, явно соединённые
         через JOIN ... ON, вместе с условием соединения
       - Co-occurrence связи (низкая уверенность) — таблицы, встретившиеся
         в одном запросе, но без явного ON (например через WHERE или
         неявный JOIN старого синтаксиса FROM a, b)
  4. Агрегирует связи по парам таблиц (частота, примеры условий)
  5. Строит граф и сохраняет:
       - edges.csv         — рёбра графа с весами и условиями (для Neo4j LOAD CSV)
       - nodes.csv          — узлы графа со степенью связности
       - graph.png          — статичная визуализация (matplotlib, офлайн)
       - graph.html         — интерактивная визуализация (pyvis, если установлен,
                              полностью офлайн — JS встроен в файл)
       - parse_errors.csv   — запросы, которые не удалось разобрать (для отладки)

Установка зависимостей:
    pip install sqlglot pandas networkx matplotlib --break-system-packages
    pip install pyvis --break-system-packages   # опционально, для интерактивного графа

Пример запуска:
    python analyze_query_lineage.py --input queries.csv --output ./result
    python analyze_query_lineage.py --input queries.csv --output ./result --dialect tsql
    python analyze_query_lineage.py --input queries.csv --output ./result --min-weight 3 --top-n 60

Ожидаемый формат входного CSV:
    Обязательная колонка: query_text (или укажите своё имя через --column)
    Остальные колонки игнорируются.
"""

import argparse
import itertools
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

try:
    import sqlglot
    from sqlglot import exp
except ImportError:
    sys.exit("Не найден sqlglot. Установите: pip install sqlglot --break-system-packages")


# ══════════════════════════════════════════════════════════════
# 1. РАЗБОР ОДНОГО ЗАПРОСА
# ══════════════════════════════════════════════════════════════

def build_table_map(statement):
    """
    Строит словарь alias/имя -> полное имя таблицы (schema.table) для запроса.
    Нужен чтобы понять на какую таблицу ссылается алиас в условии JOIN.
    """
    mapping = {}
    for t in statement.find_all(exp.Table):
        full_name = t.name
        if t.db:
            full_name = f"{t.db}.{full_name}"
        alias = t.alias_or_name
        if alias:
            mapping[alias] = full_name
        mapping[t.name] = full_name
    return mapping


def extract_join_edges(statement, table_map):
    """
    Извлекает связи из явных JOIN ... ON условий.
    Возвращает список: (frozenset({table_a, table_b}), условие_sql)
    """
    edges = []
    for join in statement.find_all(exp.Join):
        right_expr = join.this
        if not isinstance(right_expr, exp.Table):
            continue

        right_alias = right_expr.alias_or_name
        right_name = table_map.get(right_alias, right_expr.name)

        on_cond = join.args.get("on")
        if on_cond is None:
            continue

        referenced_tables = set()
        for col in on_cond.find_all(exp.Column):
            tbl_ref = col.table
            if tbl_ref:
                resolved = table_map.get(tbl_ref, tbl_ref)
                referenced_tables.add(resolved)

        referenced_tables.discard(right_name)

        try:
            cond_sql = on_cond.sql(pretty=False)
        except Exception:
            cond_sql = ""

        for other in referenced_tables:
            pair = frozenset({right_name, other})
            if len(pair) == 2:
                edges.append((pair, cond_sql))

    return edges


def extract_cooccurrence_edges(table_map):
    """
    Запасной вариант: связывает все таблицы, встретившиеся в одном запросе,
    когда явных JOIN...ON извлечь не удалось (неявные джойны, WHERE-условия и т.п.)
    Уверенность ниже — это просто "таблицы использовались вместе".
    """
    tables = sorted(set(table_map.values()))
    edges = []
    for a, b in itertools.combinations(tables, 2):
        edges.append((frozenset({a, b}), None))
    return edges


def parse_query(sql_text, dialect):
    """Парсит один текст запроса (может содержать несколько statement через ;)."""
    return sqlglot.parse(sql_text, read=dialect)


# ══════════════════════════════════════════════════════════════
# 2. ОСНОВНОЙ ПАЙПЛАЙН
# ══════════════════════════════════════════════════════════════

def analyze(df, column, dialect, verbose=True):
    edge_stats = defaultdict(lambda: {
        "join_count": 0,
        "cooc_count": 0,
        "conditions": Counter(),
    })
    node_query_count = Counter()

    parse_errors = []
    total = len(df)
    parsed_ok = 0

    for idx, row in df.iterrows():
        sql_text = row[column]
        if not isinstance(sql_text, str) or not sql_text.strip():
            continue

        try:
            statements = parse_query(sql_text, dialect)
        except Exception as e:
            parse_errors.append({"row_index": idx, "query_text": sql_text, "error": str(e)})
            continue

        row_had_valid_statement = False

        for stmt in statements:
            if stmt is None:
                continue
            try:
                table_map = build_table_map(stmt)
                if not table_map:
                    continue

                for t in set(table_map.values()):
                    node_query_count[t] += 1

                join_edges = extract_join_edges(stmt, table_map)

                if join_edges:
                    for pair, cond in join_edges:
                        key = tuple(sorted(pair))
                        edge_stats[key]["join_count"] += 1
                        if cond:
                            edge_stats[key]["conditions"][cond] += 1
                elif len(table_map) >= 2:
                    # запасной вариант — co-occurrence
                    cooc_edges = extract_cooccurrence_edges(table_map)
                    for pair, _ in cooc_edges:
                        key = tuple(sorted(pair))
                        edge_stats[key]["cooc_count"] += 1

                row_had_valid_statement = True

            except Exception as e:
                parse_errors.append({"row_index": idx, "query_text": sql_text, "error": f"extract error: {e}"})
                continue

        if row_had_valid_statement:
            parsed_ok += 1

    if verbose:
        print(f"Обработано строк: {total}")
        print(f"Успешно распознано: {parsed_ok}")
        print(f"Ошибок парсинга: {len(parse_errors)}")
        print(f"Уникальных таблиц: {len(node_query_count)}")
        print(f"Уникальных пар с связью: {len(edge_stats)}")

    return edge_stats, node_query_count, parse_errors


# ══════════════════════════════════════════════════════════════
# 3. ЭКСПОРТ РЕЗУЛЬТАТОВ
# ══════════════════════════════════════════════════════════════

def export_edges_csv(edge_stats, output_dir, min_weight=1, top_conditions_n=3):
    rows = []
    for (a, b), stats in edge_stats.items():
        total_weight = stats["join_count"] * 3 + stats["cooc_count"]  # join весит больше
        if total_weight < min_weight:
            continue

        edge_type = (
            "join" if stats["join_count"] and not stats["cooc_count"] else
            "cooccurrence" if stats["cooc_count"] and not stats["join_count"] else
            "both"
        )

        top_conditions = "; ".join(
            cond for cond, _ in stats["conditions"].most_common(top_conditions_n) if cond
        )

        rows.append({
            "table_a": a,
            "table_b": b,
            "join_query_count": stats["join_count"],
            "cooccurrence_query_count": stats["cooc_count"],
            "weight": total_weight,
            "edge_type": edge_type,
            "sample_join_conditions": top_conditions,
        })

    edges_df = pd.DataFrame(rows).sort_values("weight", ascending=False)
    edges_df.to_csv(output_dir / "edges.csv", index=False, encoding="utf-8-sig")
    return edges_df


def export_nodes_csv(edges_df, node_query_count, output_dir):
    degree = Counter()
    total_edge_weight = Counter()
    for _, r in edges_df.iterrows():
        degree[r["table_a"]] += 1
        degree[r["table_b"]] += 1
        total_edge_weight[r["table_a"]] += r["weight"]
        total_edge_weight[r["table_b"]] += r["weight"]

    all_tables = set(node_query_count.keys()) | set(degree.keys())
    rows = [{
        "table": t,
        "degree": degree.get(t, 0),
        "queries_using_table": node_query_count.get(t, 0),
        "total_edge_weight": total_edge_weight.get(t, 0),
    } for t in all_tables]

    nodes_df = pd.DataFrame(rows).sort_values("degree", ascending=False)
    nodes_df.to_csv(output_dir / "nodes.csv", index=False, encoding="utf-8-sig")
    return nodes_df


def export_errors_csv(parse_errors, output_dir):
    if parse_errors:
        pd.DataFrame(parse_errors).to_csv(
            output_dir / "parse_errors.csv", index=False, encoding="utf-8-sig"
        )


# ══════════════════════════════════════════════════════════════
# 4. ВИЗУАЛИЗАЦИЯ
# ══════════════════════════════════════════════════════════════

def draw_static_graph(edges_df, nodes_df, output_dir, top_n=80):
    import networkx as nx
    import matplotlib.pyplot as plt

    G = nx.Graph()

    top_tables = set(nodes_df.sort_values("degree", ascending=False).head(top_n)["table"])

    for _, r in edges_df.iterrows():
        if r["table_a"] in top_tables and r["table_b"] in top_tables:
            G.add_edge(r["table_a"], r["table_b"], weight=r["weight"], edge_type=r["edge_type"])

    if G.number_of_nodes() == 0:
        print("Граф пуст — нечего рисовать (проверьте min_weight).")
        return

    plt.figure(figsize=(20, 16))
    pos = nx.spring_layout(G, k=0.6, seed=42, iterations=60)

    degrees = dict(G.degree())
    node_sizes = [300 + degrees[n] * 120 for n in G.nodes()]

    join_edges = [(u, v) for u, v, d in G.edges(data=True) if d["edge_type"] in ("join", "both")]
    cooc_edges = [(u, v) for u, v, d in G.edges(data=True) if d["edge_type"] == "cooccurrence"]

    nx.draw_networkx_edges(G, pos, edgelist=join_edges, edge_color="#2E7D5E", width=1.6, alpha=0.7)
    nx.draw_networkx_edges(G, pos, edgelist=cooc_edges, edge_color="#B8860B", width=0.8,
                            alpha=0.35, style="dashed")

    nx.draw_networkx_nodes(G, pos, node_size=node_sizes, node_color="#FFFFFF",
                            edgecolors="#12352A", linewidths=1.3)
    nx.draw_networkx_labels(G, pos, font_size=8, font_family="sans-serif")

    plt.title(f"Связи таблиц по факту использования в запросах (топ {len(G.nodes())} таблиц)",
              fontsize=13)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_dir / "graph.png", dpi=150)
    plt.close()
    print(f"Сохранено: {output_dir / 'graph.png'}")


def draw_interactive_graph(edges_df, nodes_df, output_dir, top_n=150):
    try:
        from pyvis.network import Network
    except ImportError:
        print("pyvis не установлен — интерактивный HTML-граф пропущен.")
        print("Для установки: pip install pyvis --break-system-packages")
        return

    top_tables = set(nodes_df.sort_values("degree", ascending=False).head(top_n)["table"])

    # cdn_resources='in_line' встраивает JS прямо в файл — работает офлайн,
    # без обращения к внешним CDN (важно для закрытого контура)
    net = Network(height="850px", width="100%", bgcolor="#F7F6F2",
                   font_color="#1A211E", cdn_resources="in_line")
    net.barnes_hut(gravity=-4000, central_gravity=0.25, spring_length=140)

    degree = {}
    for _, r in edges_df.iterrows():
        degree[r["table_a"]] = degree.get(r["table_a"], 0) + 1
        degree[r["table_b"]] = degree.get(r["table_b"], 0) + 1

    added_nodes = set()
    for _, r in edges_df.iterrows():
        a, b = r["table_a"], r["table_b"]
        if a not in top_tables or b not in top_tables:
            continue
        for node in (a, b):
            if node not in added_nodes:
                size = 12 + degree.get(node, 1) * 3
                net.add_node(node, label=node, size=size,
                             color="#4F8EF7" if degree.get(node, 0) > 5 else "#A7C7EE")
                added_nodes.add(node)

        color = "#2E7D5E" if r["edge_type"] in ("join", "both") else "#C9A227"
        dashes = r["edge_type"] == "cooccurrence"
        title = r["sample_join_conditions"] or "co-occurrence (без явного ON)"
        net.add_edge(a, b, value=r["weight"], color=color, dashes=dashes, title=title)

    out_path = output_dir / "graph.html"
    net.write_html(str(out_path), open_browser=False, notebook=False)

    # pyvis жёстко прописывает 2 внешние ссылки на Bootstrap (только для косметики
    # панели кнопок, на сам граф vis-network не влияет). Для гарантии полностью
    # офлайн-работы в закрытом контуре — вырезаем их.
    html = out_path.read_text(encoding="utf-8")
    html = re.sub(
        r'<link\s+href="https://cdn\.jsdelivr\.net/npm/bootstrap[^>]*?/>',
        "", html, flags=re.DOTALL
    )
    html = re.sub(
        r'<script\s+src="https://cdn\.jsdelivr\.net/npm/bootstrap[^>]*?></script>',
        "", html, flags=re.DOTALL
    )
    out_path.write_text(html, encoding="utf-8")

    print(f"Сохранено: {out_path} (интерактивный, полностью офлайн)")


# ══════════════════════════════════════════════════════════════
# 5. CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Анализ связей между таблицами по SQL-запросам пользователей (sqlglot)."
    )
    parser.add_argument("--input", required=True, help="Путь к CSV с запросами")
    parser.add_argument("--output", default="./lineage_result", help="Папка для результатов")
    parser.add_argument("--column", default="query_text", help="Имя колонки с SQL-текстом")
    parser.add_argument("--dialect", default=None,
                         help="SQL-диалект для sqlglot: tsql, postgres, snowflake, oracle, "
                              "hive, spark, bigquery, mysql и т.д. По умолчанию — generic ANSI.")
    parser.add_argument("--min-weight", type=int, default=1,
                         help="Минимальный вес связи для включения в edges.csv/граф")
    parser.add_argument("--top-n", type=int, default=80,
                         help="Сколько самых связанных таблиц показать на статичном графе")
    parser.add_argument("--top-n-interactive", type=int, default=150,
                         help="Сколько таблиц показать на интерактивном графе")
    parser.add_argument("--no-viz", action="store_true", help="Не строить визуализации, только CSV")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        sys.exit(f"Файл не найден: {input_path}")

    print(f"Читаю: {input_path}")
    df = pd.read_csv(input_path)

    if args.column not in df.columns:
        sys.exit(f"Колонка '{args.column}' не найдена. Доступные колонки: {list(df.columns)}")

    edge_stats, node_query_count, parse_errors = analyze(df, args.column, args.dialect)

    edges_df = export_edges_csv(edge_stats, output_dir, min_weight=args.min_weight)
    nodes_df = export_nodes_csv(edges_df, node_query_count, output_dir)
    export_errors_csv(parse_errors, output_dir)

    print(f"\nСохранено: {output_dir / 'edges.csv'} ({len(edges_df)} связей)")
    print(f"Сохранено: {output_dir / 'nodes.csv'} ({len(nodes_df)} таблиц)")
    if parse_errors:
        print(f"Сохранено: {output_dir / 'parse_errors.csv'} ({len(parse_errors)} ошибок)")

    if not args.no_viz and len(edges_df) > 0:
        draw_static_graph(edges_df, nodes_df, output_dir, top_n=args.top_n)
        draw_interactive_graph(edges_df, nodes_df, output_dir, top_n=args.top_n_interactive)

    print("\nТоп-10 самых связанных таблиц:")
    print(nodes_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
