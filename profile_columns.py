"""
Отдаёт LLM компактное описание вашей таблицы (названия колонок, типы, примеры
значений, число уникальных) и просит определить роль каждой колонки: дата,
категория, юнит/группа, название показателя, значение факта, идентификатор,
другое. Работает в РУЧНОМ режиме — без API, промпт копируется в чат LLM,
ответ вставляется обратно в файл — тот же принцип, что в generate_brief.py.

Запуск, шаг 1 — собрать профиль и сохранить промпт для копирования в чат:
    python profile_columns.py --input fact_pokazateli.csv --save-prompt prompt_profile.txt --dry-run

Запуск, шаг 2 — после того как вставили ответ LLM в файл response_profile.txt:
    python profile_columns.py --input fact_pokazateli.csv --response-file response_profile.txt --output column_roles.csv

Параметры:
    --input          CSV/Excel с вашими данными
    --sheet          лист Excel (не используется для CSV)
    --sample-size    сколько примеров значений показывать LLM на колонку (по умолчанию 5)
    --save-prompt    сохранить промпт в файл для копирования в чат
    --dry-run        не парсить ответ, только показать промпт
    --response-file  путь к файлу с вставленным ответом LLM
    --output         путь к результирующему CSV с ролями колонок
    --sep            разделитель CSV (по умолчанию автоопределение)
"""

import argparse
import json
import os
import re
import sys

import pandas as pd


def read_text_auto_encoding(path: str) -> str:
    """Читает текстовый файл, пробуя несколько кодировок по очереди (см. пояснение
    в generate_brief.py — та же проблема с файлами ответа LLM, сохранёнными не в UTF-8)."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Не удалось прочитать '{path}' ни в одной из известных кодировок "
                      f"(utf-8-sig, utf-8, cp1251, latin-1). Пересохраните файл в UTF-8.")


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


def build_column_profile(df: pd.DataFrame, sample_size: int):
    """Компактное описание каждой колонки — то, что реально нужно LLM для решения,
    а не вся таблица целиком (экономит контекст, особенно при многих колонках)."""
    profile = []
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        n_unique = non_null.nunique()
        pct_missing = round(100 * series.isna().mean(), 1)

        samples = non_null.drop_duplicates().head(sample_size).tolist()
        samples = [str(s) for s in samples]

        profile.append({
            "column": str(col),
            "dtype": str(series.dtype),
            "n_unique": int(n_unique),
            "pct_missing": pct_missing,
            "sample_values": samples,
        })
    return profile


PROMPT_TEMPLATE = """Ты помощник по анализу данных. Ниже — компактное описание колонок таблицы
(название, тип данных pandas, число уникальных значений, % пропусков, примеры значений).
Твоя задача — определить РОЛЬ каждой колонки.

Возможные роли:
- "date"         — дата или период
- "unit"         — юнит/подразделение/группа
- "metric_name"  — название показателя/метрики
- "fact_value"   — числовое значение факта/показателя
- "plan_value"   — плановое/целевое значение показателя
- "id"           — идентификатор записи, не несущий содержательного смысла
- "category"     — прочая категориальная классификация
- "other"        — не подходит ни под одну из категорий выше

КАК ОТЛИЧИТЬ "fact_value" ОТ "plan_value" (это частая ошибка, будь внимателен):
- Название колонки — САМЫЙ СИЛЬНЫЙ сигнал, сильнее статистики. Если в названии колонки есть
  "план", "plan", "target", "цель", "budget", "бюджет", "норматив", "kpi_target" — это "plan_value",
  ДАЖЕ ЕСЛИ статистически колонка выглядит так же, как обычное числовое значение.
- Если в названии есть "факт", "fact", "актуал", "actual", "результат" — это "fact_value".
- Если в таблице есть ДВЕ похожие по типу числовые колонки рядом — это почти всегда пара
  факт+план, не путай их местами и не назначай обеим одну и ту же роль.
- Не полагайся только на то, что колонка "просто числовая" — числовой может быть и факт,
  и план, и вообще любая другая метрика. Смотри в первую очередь на название.

ПРИМЕР (как это должно выглядеть на практике):
Дано:
  {{"column": "revenue_fact", "dtype": "float64", "sample_values": ["100.5", "203.1"]}}
  {{"column": "revenue_plan", "dtype": "float64", "sample_values": ["100.0", "200.0"]}}
Правильный ответ для этих двух колонок:
  {{"column": "revenue_fact", "role": "fact_value", "confidence": "high", "reasoning": "суффикс _fact в названии"}}
  {{"column": "revenue_plan", "role": "plan_value", "confidence": "high", "reasoning": "суффикс _plan в названии"}}

ПРАВИЛА:
1. Используй ТОЛЬКО названия колонок из списка ниже, ничего не придумывай.
2. Одна и та же роль может подойти нескольким колонкам, если это оправдано данными.
3. Верни ТОЛЬКО валидный JSON как обычный текстовый (plain text) ответ:
   - без markdown-разметки и без блоков ```;
   - без пояснений, комментариев или заголовков до или после JSON;
   - первый символ твоего ответа должен быть "{{", последний — "}}";
   - без невидимых служебных символов в начале (например, BOM/zero-width space).
4. Этот ответ будет сохранён пользователем в файл response_profile.json — отвечай так,
   как будто ты формируешь содержимое именно этого файла, а не сообщение в чате.

Формат ответа:
{{
  "columns": [
    {{"column": "название_колонки", "role": "одна из ролей выше", "confidence": "high/medium/low", "reasoning": "одно предложение почему"}}
  ]
}}

Колонки:
{profile_json}
"""


def build_prompt(profile: list) -> str:
    return PROMPT_TEMPLATE.format(profile_json=json.dumps(profile, ensure_ascii=False, indent=2))


def parse_llm_response(raw_response: str, valid_columns: set):
    cleaned = re.sub(r"^```(json)?|```$", "", raw_response.strip(), flags=re.MULTILINE).strip()
    cleaned = cleaned.lstrip("\ufeff")  # BOM не убирается обычным .strip(), убираем явно
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM вернула невалидный JSON: {e}\nОтвет был:\n{raw_response}")

    valid, rejected = [], []
    for item in data.get("columns", []):
        if item.get("column") in valid_columns:
            valid.append(item)
        else:
            rejected.append(item)
    return valid, rejected


def call_llm(prompt: str, api_base: str, api_key: str = None, model_name: str = "default",
             temperature: float = 0.3, timeout: int = 120) -> str:
    """Тот же принцип вызова, что в generate_brief.py — OpenAI-совместимый эндпоинт."""
    import requests

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = api_base.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    resp = requests.post(
        url, headers=headers,
        json={"model": model_name, "messages": [{"role": "user", "content": prompt}], "temperature": temperature},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True)
    parser.add_argument("--sheet", default=0)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--save-prompt", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--response-file", default=None)
    parser.add_argument("--api-base", default=None,
                         help="адрес API вашей LLM — включает прямой вызов вместо ручного копипаста")
    parser.add_argument("--api-key", default=None, help="API-ключ/токен, если требуется")
    parser.add_argument("--model-name", default="default")
    parser.add_argument("--output", default=None)
    parser.add_argument("--sep", default=None)
    args = parser.parse_args()

    df = read_input_auto(args.input, args.sep, args.sheet)
    profile = build_column_profile(df, args.sample_size)

    print(f"Колонок в таблице: {len(profile)}", file=sys.stderr)
    for p in profile:
        print(f"  {p['column']}: {p['dtype']}, уникальных={p['n_unique']}, "
              f"пропусков={p['pct_missing']}%, примеры={p['sample_values'][:3]}", file=sys.stderr)

    prompt = build_prompt(profile)

    if args.save_prompt:
        with open(args.save_prompt, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"\nПромпт сохранён в '{args.save_prompt}' — скопируйте его в чат вашей LLM.", file=sys.stderr)

    if args.dry_run:
        print("\n──── ПРОМПТ (dry-run) ────\n", file=sys.stderr)
        print(prompt)
        return

    if args.response_file:
        raw_response = read_text_auto_encoding(args.response_file)
    elif args.api_base:
        print(f"Вызываю LLM напрямую через {args.api_base}...", file=sys.stderr)
        raw_response = call_llm(prompt, api_base=args.api_base, api_key=args.api_key, model_name=args.model_name)
    else:
        print("Ошибка: укажите --response-file (ручной режим), --api-base (прямой вызов) "
              "или --dry-run.", file=sys.stderr)
        sys.exit(1)

    valid_columns = {p["column"] for p in profile}
    valid, rejected = parse_llm_response(raw_response, valid_columns)


    if rejected:
        print(f"ВНИМАНИЕ: LLM сослалась на несуществующие колонки, отклонено: {rejected}", file=sys.stderr)

    result_df = pd.DataFrame(valid)
    print("\n──── Роли колонок ────", file=sys.stderr)
    for _, row in result_df.iterrows():
        print(f"  {row['column']:20s} -> {row['role']:12s} ({row['confidence']}): {row['reasoning']}",
              file=sys.stderr)

    if args.output:
        result_df.to_csv(args.output, sep=";", index=False, encoding="utf-8-sig")
        print(f"\nСохранено в '{args.output}'.", file=sys.stderr)

    # ── Бонус: готовые флаги для find_metric_links.py / find_var_anomalies.py ──
    by_role = {}
    for _, row in result_df.iterrows():
        by_role.setdefault(row["role"], []).append(row["column"])

    print("\n──── Подсказка для CLI-флагов (проверьте перед использованием) ────", file=sys.stderr)
    if by_role.get("metric_name"):
        print(f"  --column {by_role['metric_name'][0]}", file=sys.stderr)
    if by_role.get("date"):
        print(f"  --date-column {by_role['date'][0]}", file=sys.stderr)
    if by_role.get("fact_value"):
        print(f"  --value-column {by_role['fact_value'][0]}", file=sys.stderr)
    if by_role.get("plan_value"):
        print(f"  --plan-column {by_role['plan_value'][0]}", file=sys.stderr)
    if by_role.get("unit"):
        print(f"  --unit-column / --group-column {by_role['unit'][0]}", file=sys.stderr)


if __name__ == "__main__":
    main()
