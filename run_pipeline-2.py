"""
Оркестратор: даёте один источник данных — дальше вся цепочка отрабатывает
автоматически, без ручного указания названий столбцов на каждом шаге.

РУЧНЫХ шага в целевой картине остаётся только два (пока нет API до вашей LLM):
  1. Один раз на новый источник — определить роли колонок через profile_columns.py
     (копипаст в чат LLM).
  2. Каждый прогон — написать текст брифа (копипаст в чат LLM).
Всё, что между ними — связи, аномалии, сборка промпта — запускается этим
скриптом одной командой, без повторного указания --column/--date-column и т.д.

ПОШАГОВЫЙ ПРОЦЕСС:

Шаг 1 (один раз на источник, вручную через чат LLM):
    python profile_columns.py --input facts.csv --save-prompt prompt_profile.txt --dry-run
    -> скопировать prompt_profile.txt в чат LLM -> сохранить ответ в response_profile.txt
    python profile_columns.py --input facts.csv --response-file response_profile.txt --output column_roles.csv

Шаг 2 (полностью автоматически, эта команда):
    python run_pipeline.py --input facts.csv --column-roles column_roles.csv --anomaly-method chronos

    Это само вызовет по цепочке:
      find_metric_links.py    -> links.csv
      find_chronos_anomalies.py (или find_var_anomalies.py) -> anomalies.csv
      generate_brief.py --dry-run --save-prompt -> prompt_for_chat.txt

Шаг 3 (вручную через чат LLM, каждый прогон):
    -> скопировать prompt_for_chat.txt в чат LLM -> сохранить ответ в response_brief.txt
    python generate_brief.py --anomalies anomalies.csv --links links.csv \\
        --output brief.md --response-file response_brief.txt

Параметры:
    --input           источник данных (CSV/Excel)
    --column-roles    CSV от profile_columns.py с ролями колонок (обязателен)
    --anomaly-method  chronos | var (по умолчанию chronos)
    --embed-model     путь/имя модели эмбеддингов
                       (по умолчанию /home/datalab/nfs/embed/bge-m3-local)
    --chronos-model   путь/имя модели Chronos
                       (по умолчанию /home/datalab/nfs/chronos/autogluon_chronos-2)
    --context-length  сколько предыдущих периодов видит Chronos при прогнозе (по умолчанию 6)
    --check-last-n    проверять только последние N периодов каждого показателя (по умолчанию 3)
    --work-dir        папка для промежуточных файлов (по умолчанию текущая)
    --sep             разделитель CSV источника (по умолчанию автоопределение)
"""

import argparse
import os
import subprocess
import sys

import pandas as pd


def load_column_roles(path: str) -> dict:
    """Читает результат profile_columns.py, возвращает словарь роль -> первая подходящая колонка."""
    df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
    roles = {}
    for _, row in df.iterrows():
        role = row["role"]
        if role not in roles:  # берём первую найденную колонку для каждой роли
            roles[role] = row["column"]
    return roles


def run_step(description: str, cmd: list):
    """Запускает один шаг пайплайна как подпроцесс, печатает прогресс, останавливается при ошибке."""
    print(f"\n{'='*70}\n{description}\n{'='*70}", file=sys.stderr)
    print("Команда:", " ".join(cmd), file=sys.stderr)
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(f"\nОШИБКА: шаг '{description}' завершился с кодом {result.returncode}. "
              f"Пайплайн остановлен.", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="источник данных (CSV/Excel)")
    parser.add_argument("--column-roles", required=True,
                         help="CSV от profile_columns.py с ролями колонок (см. Шаг 1 в описании)")
    parser.add_argument("--anomaly-method", choices=["chronos", "var"], default="chronos")
    parser.add_argument("--embed-model", default="/home/datalab/nfs/embed/bge-m3-local")
    parser.add_argument("--chronos-model", default="/home/datalab/nfs/chronos/autogluon_chronos-2")
    parser.add_argument("--context-length", type=int, default=6,
                         help="сколько предыдущих периодов видит Chronos при прогнозе (по умолчанию 6)")
    parser.add_argument("--check-last-n", type=int, default=3,
                         help="проверять только последние N периодов каждого показателя (по умолчанию 3)")
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--sep", default=None)
    args = parser.parse_args()

    if not os.path.exists(args.column_roles):
        print(f"Ошибка: файл ролей колонок '{args.column_roles}' не найден.\n\n"
              f"Сначала выполните Шаг 1 (один раз на этот источник):\n"
              f"  python profile_columns.py --input {args.input} --save-prompt prompt_profile.txt --dry-run\n"
              f"  (скопируйте промпт в чат LLM, ответ сохраните в response_profile.txt)\n"
              f"  python profile_columns.py --input {args.input} --response-file response_profile.txt "
              f"--output {args.column_roles}\n", file=sys.stderr)
        sys.exit(1)

    roles = load_column_roles(args.column_roles)
    print(f"Загружены роли колонок: {roles}", file=sys.stderr)

    required = ["metric_name", "date", "fact_value"]
    missing_roles = [r for r in required if r not in roles]
    if missing_roles:
        print(f"Ошибка: в '{args.column_roles}' не хватает ролей: {missing_roles}. "
              f"Проверьте результат profile_columns.py — возможно, LLM не смогла их определить, "
              f"или их нужно поправить руками в CSV перед повторным запуском.", file=sys.stderr)
        sys.exit(1)

    metric_col = roles["metric_name"]
    date_col = roles["date"]
    value_col = roles["fact_value"]
    unit_col = roles.get("unit")  # опционально, может отсутствовать

    links_path = os.path.join(args.work_dir, "links.csv")
    anomalies_path = os.path.join(args.work_dir, "anomalies.csv")
    prompt_path = os.path.join(args.work_dir, "prompt_for_chat.txt")
    brief_path = os.path.join(args.work_dir, "brief.md")

    py = sys.executable

    # ── Шаг автоматический 1: поиск связей между показателями ──
    cmd = [py, "find_metric_links.py", "--input", args.input, "--column", metric_col,
           "--output", links_path, "--model", args.embed_model]
    if unit_col:
        cmd += ["--group-column", unit_col]
    if args.sep:
        cmd += ["--sep", args.sep]
    run_step("Шаг 1/3: поиск связей между показателями (find_metric_links.py)", cmd)

    # ── Шаг автоматический 2: поиск аномалий ──
    if args.anomaly_method == "chronos":
        script = "find_chronos_anomalies.py"
        extra = ["--model", args.chronos_model,
                  "--context-length", str(args.context_length),
                  "--check-last-n", str(args.check_last_n)]
    else:
        script = "find_var_anomalies.py"
        extra = []

    cmd = [py, script, "--input", args.input,
           "--metric-column", metric_col, "--date-column", date_col, "--value-column", value_col,
           "--links-file", links_path, "--output", anomalies_path] + extra
    if unit_col:
        cmd += ["--unit-column", unit_col]
    if args.sep:
        cmd += ["--sep", args.sep]
    run_step(f"Шаг 2/3: поиск аномалий ({script})", cmd)

    # ── Шаг автоматический 3: сборка промпта для брифа (без вызова LLM - см. Шаг 3 в описании) ──
    cmd = [py, "generate_brief.py", "--anomalies", anomalies_path, "--links", links_path,
           "--output", brief_path, "--dry-run", "--save-prompt", prompt_path]
    run_step("Шаг 3/3: сборка промпта для брифа (generate_brief.py --dry-run)", cmd)

    print(f"\n{'='*70}\nАВТОМАТИЧЕСКАЯ ЧАСТЬ ЗАВЕРШЕНА.\n{'='*70}", file=sys.stderr)
    print(f"Готовые промежуточные файлы: {links_path}, {anomalies_path}", file=sys.stderr)
    print(f"\nОсталось руками (Шаг 3 из описания):\n"
          f"  1. Откройте '{prompt_path}', скопируйте в чат вашей LLM\n"
          f"  2. Ответ модели сохраните в response_brief.txt\n"
          f"  3. Запустите:\n"
          f"     python generate_brief.py --anomalies {anomalies_path} --links {links_path} "
          f"--output {brief_path} --response-file response_brief.txt\n", file=sys.stderr)


if __name__ == "__main__":
    main()
