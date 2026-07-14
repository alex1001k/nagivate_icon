[пропуск] 268 id_pokaz из fact_pokazateli.csv нет в dim_pokazateli.csv -- выкинуто 2263 строк фактов. Примеры: ['1002102', '1002103', '1002169', 'AI.GenAI.Cred', 'AI.GigaChat']
[внимание] 15 из пропущенных id выглядят как обрывок текстового поля (длинные/с запятыми), а не как код показателя -- возможно, это не неполнота справочника, а съехавшие колонки при чтении CSV. Пример: 'POS. Проектное управление и клиентский опыт'
[пропуск] 21 id_pokaz/driver_id из driver_links.csv нет в dim_pokazateli.csv -- выкинуто 23 связей. Примеры: ['1002103', '1002169', 'AI.GenAI.Cred', 'BROM', 'Bio-Tech']
records: 3128 | deviations: 2524 | briefs: 2524
summary: {
  "total_metrics": 329,
  "meeting_plan": 44,
  "plan_base": 65,
  "total_with_target": 24,
  "on_track_count": 0,
  "total_important": 24,
  "important_with_deviation": 9,
  "deviations_recent_3m": 279,
  "with_forecast": 10,
  "top_deviation": {
    "id_pokaz": "1002109",
    "pokaz_name": "DAU/MAU СИ",
    "screen": "INVT",
    "date_": "2026-06-30",
    "headline": "DAU/MAU СИ: отклонение от плана +1077129.4%",
    "text": "• Факт: 2 693 тыс. (Выполнение плана: {color:ColorGreen}1077229%{color})\n• Динамика vs май 26: {color:ColorGreen}+3.3%{color}\n• Также растут: «DAU СИ» {color:ColorGreen}+5.6%{color} (расходится с планом), «MAU СИ» {color:ColorGreen}+2.2%{color} (расходится с планом)",
    "severity": "high"
  }
}
rule fire counts: {'mom': 2397, 'plan': 411, 'outlier': 346, 'iqr': 194, 'volatility': 16, 'streak': 106}
