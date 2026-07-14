[пропуск] 268 id_pokaz из fact_pokazateli.csv нет в dim_pokazateli.csv -- выкинуто 2263 строк фактов. Примеры: ['1002102', '1002103', '1002169', 'AI.GenAI.Cred', 'AI.GigaChat']
[внимание] 15 из пропущенных id выглядят как обрывок текстового поля (длинные/с запятыми), а не как код показателя -- возможно, это не неполнота справочника, а съехавшие колонки при чтении CSV. Пример: 'POS. Проектное управление и клиентский опыт'
[пропуск] 21 id_pokaz/driver_id из driver_links.csv нет в dim_pokazateli.csv -- выкинуто 23 связей. Примеры: ['1002103', '1002169', 'AI.GenAI.Cred', 'BROM', 'Bio-Tech']
---------------------------------------------------------------------------
TypeError                                 Traceback (most recent call last)
/tmp/ipykernel_3036545/2425863945.py in <module>
    493         if r['plan'] is not None:
    494             completion = (r['fact'] / r['plan'] * 100) if r['plan'] else None
--> 495             fact_line += f" (Выполнение плана: {tag(f'{completion:.0f}%', good_for('plan', r, direction))})"
    496         lines.append(fact_line)
    497 

TypeError: unsupported format string passed to NoneType.__format__
