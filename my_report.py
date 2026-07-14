# -*- coding: utf-8 -*-
"""
Расчёт отклонений (инсайтов) по фактам и генерация текстовых брифов.

Статистические правила выявления отклонений (7 штук, применяются независимо
друг от друга к каждой паре показатель+месяц):

  1. plan       -- отклонение факта от плана текущего месяца, |Δ| >= 1%
  2. mom        -- динамика к предыдущему месяцу, |Δ| >= 1%
  3. outlier(z) -- z-score факта относительно скользящего окна (до 6 мес.), |z| >= 1.8
  4. iqr        -- робастный выброс по межквартильному размаху (окно до 12 мес.,
                   границы Q1-1.5*IQR / Q3+1.5*IQR) -- дополняет z-score и менее
                   чувствителен к тому, что в окне уже есть другой выброс
  5. yoy        -- отклонение год-к-году (тот же месяц предыдущего года), |Δ| >= 15%
  6. streak     -- устойчивый тренд: 3+ месяца подряд движения в "плохую" сторону
                   (порог шума 2% за шаг, чтобы не считать дрейф шумом)
  7. volatility -- скачок волатильности: коэффициент вариации за последние 3 мес.
                   вырос в >=1.8 раза относительно предыдущих 6 мес.

Читает fact_pokazateli.csv + dim_pokazateli.csv, пишет bundle.json для мокапа.
"""
import pandas as pd
import numpy as np
import json
import random

fact = pd.read_csv('fact_pokazateli.csv', parse_dates=['date_'])
dim = pd.read_csv('dim_pokazateli.csv')

# id_pokaz должен встречаться в dim_pokazateli.csv ровно один раз -- проверяем ДО
# set_index/to_dict('index'), потому что при дублях эти вызовы падают сами с невнятным
# ValueError ("index must be unique"), не успевая дойти до _validate_inputs() ниже
dup_ids = dim['id_pokaz'][dim['id_pokaz'].duplicated()].unique().tolist()
if dup_ids:
    dup_rows = dim[dim['id_pokaz'].isin(dup_ids)].sort_values('id_pokaz')
    raise SystemExit(
        f"В dim_pokazateli.csv id_pokaz повторяется {len(dup_ids)} раз(а): {dup_ids[:5]}\n"
        "Каждый показатель должен встречаться в справочнике ровно один раз -- иначе непонятно, "
        "какую строку метаданных (screen/category/direction/...) брать для расчёта.\n"
        "Частая причина -- показатель выгружен отдельной строкой на каждый экран/период, или "
        "файл склеен из нескольких выгрузок без дедупликации.\n"
        f"Дублирующиеся строки (первые {min(10, len(dup_rows))} из {len(dup_rows)}):\n"
        f"{dup_rows.head(10).to_string()}"
    )

dim_by_id = dim.set_index('id_pokaz').to_dict('index')

# direction -- на нём завязана вся раскраска (хорошо/плохо), опечатка здесь тихо
# перекрасит половину дашборда в неверный цвет -- это единственная "мягкая" проверка,
# которую всё же стоит останавливать намертво, а не пропускать молча
if 'direction' in dim.columns:
    bad_dir = sorted(set(dim['direction'].dropna().unique()) - {'up', 'down'})
    if bad_dir:
        raise SystemExit(
            f"В dim_pokazateli.csv есть direction не 'up'/'down': {bad_dir}\n"
            "Допустимые значения -- только 'up' (рост хорошо) или 'down' (снижение хорошо)."
        )


def _looks_garbled(id_value):
    """Эвристика "это похоже на съехавшую CSV-колонку, а не на реальный id": длинный текст
    с запятыми/пробелами внутри, а не короткий код. Не блокирует выполнение -- только
    добавляет предупреждение в лог, чтобы не проглядеть настоящий баг парсинга среди
    ожидаемо неполного справочника."""
    s = str(id_value)
    return len(s) > 40 or (',' in s and ' ' in s)


# id_pokaz, которых нет в dim_pokazateli.csv, -- ожидаемая ситуация при неполном справочнике
# (dim ведут вручную и не успевают за всеми метриками в fact/driver_links), а не ошибка --
# такие строки просто отбрасываем и пишем в лог, что и сколько выкинули, вместо падения
fact_missing = sorted(set(fact['id_pokaz']) - set(dim_by_id.keys()))
if fact_missing:
    before = len(fact)
    fact = fact[~fact['id_pokaz'].isin(fact_missing)].reset_index(drop=True)
    print(f"[пропуск] {len(fact_missing)} id_pokaz из fact_pokazateli.csv нет в dim_pokazateli.csv "
          f"-- выкинуто {before - len(fact)} строк фактов. Примеры: {fact_missing[:5]}")
    garbled = [x for x in fact_missing if _looks_garbled(x)]
    if garbled:
        print(f"[внимание] {len(garbled)} из пропущенных id выглядят как обрывок текстового поля "
              f"(длинные/с запятыми), а не как код показателя -- возможно, это не неполнота "
              f"справочника, а съехавшие колонки при чтении CSV. Пример: {garbled[0]!r}")

driver_links = pd.read_csv('driver_links.csv')
driver_missing = sorted(
    (set(driver_links['id_pokaz']) | set(driver_links['driver_id'])) - set(dim_by_id.keys())
) if len(driver_links) else []
if driver_missing:
    before = len(driver_links)
    driver_links = driver_links[
        driver_links['id_pokaz'].isin(dim_by_id.keys()) & driver_links['driver_id'].isin(dim_by_id.keys())
    ].reset_index(drop=True)
    print(f"[пропуск] {len(driver_missing)} id_pokaz/driver_id из driver_links.csv нет в dim_pokazateli.csv "
          f"-- выкинуто {before - len(driver_links)} связей. Примеры: {driver_missing[:5]}")

# связи "показатель <- драйвер" -- многие-ко-многим, отдельной таблицей: у показателя
# может быть 0, 1 или несколько драйверов, и один драйвер может кормить несколько показателей
drivers_by_metric = {}   # id_pokaz -> [driver_id, ...] (его драйверы)
children_by_metric = {}  # id_pokaz -> [dependent_id, ...] (кому он сам служит драйвером)
for _, lr in driver_links.iterrows():
    drivers_by_metric.setdefault(lr['id_pokaz'], []).append(lr['driver_id'])
    children_by_metric.setdefault(lr['driver_id'], []).append(lr['id_pokaz'])

fact = fact.sort_values(['id_pokaz', 'date_']).reset_index(drop=True)

MONTHS_ORDER = sorted(pd.Timestamp(d).strftime('%Y-%m-%d') for d in fact['date_'].unique())
LAST_MONTH = MONTHS_ORDER[-1]
RECENT_3 = MONTHS_ORDER[-3:]

PLAN_TH, MOM_TH, Z_TH, YOY_TH = 1.0, 1.0, 1.8, 15.0
STREAK_TH, STREAK_NOISE, VOL_RATIO_TH, VOL_FLOOR = 3, 2.0, 1.8, 0.03
PLAN_BAND, MOM_BAND = 4.0, 1.5  # нейтральная зона для раскраски (совпадает с фронтом)


def is_good(pct, direction):
    if pct is None:
        return None
    return (pct > 0) if direction == 'up' else (pct < 0)


def is_good_banded(pct, direction, band):
    """Как is_good, но с нейтральной зоной: небольшие отклонения не считаются 'плохими'
    (согласовано с раскраской в таблице на фронте)."""
    if pct is None:
        return None
    if abs(pct) < band:
        return None
    return is_good(pct, direction)


def score_of(reason, value):
    if reason in ('plan', 'mom', 'iqr', 'yoy'):
        return abs(value)
    if reason == 'outlier':
        return abs(value) * 10
    if reason == 'streak':
        return 15 + value * 4
    if reason == 'volatility':
        return min(40, value * 8)
    return 0


records = []

for pid, g in fact.groupby('id_pokaz'):
    g = g.sort_values('date_').reset_index(drop=True)
    direction = dim_by_id[pid]['direction']
    facts_hist = []
    dates_hist = []
    streak = 0

    for i, row in g.iterrows():
        cur = row['fact']
        prev_fact = facts_hist[-1] if facts_hist else None
        prev_date = dates_hist[-1] if dates_hist else None

        mom_pct = None
        if prev_fact not in (None, 0):
            mom_pct = (cur - prev_fact) / abs(prev_fact) * 100

        plan_pct = None
        if pd.notna(row['plan']) and row['plan'] != 0:
            plan_pct = (cur / row['plan'] - 1) * 100

        # --- 3. z-score относительно скользящего окна (до 6 мес.) ---
        window6 = facts_hist[-6:]
        z, z_rel = None, None
        if len(window6) >= 4:
            mu, sd = np.mean(window6), np.std(window6)
            sd_floor = max(sd, 0.01 * abs(mu))
            if sd_floor > 1e-9:
                z = (cur - mu) / sd_floor
            if abs(mu) > 1e-9:
                z_rel = (cur - mu) / abs(mu) * 100

        # --- 4. IQR-выброс (окно до 12 мес.) ---
        window12 = facts_hist[-12:]
        iqr_rel, iqr_lo, iqr_hi, iqr_med = None, None, None, None
        if len(window12) >= 6:
            q1, med, q3 = np.percentile(window12, [25, 50, 75])
            iqr = q3 - q1
            iqr_med = med
            if iqr > 1e-9:
                iqr_lo, iqr_hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                if (cur < iqr_lo or cur > iqr_hi) and abs(med) > 1e-9:
                    iqr_rel = (cur - med) / abs(med) * 100

        # --- 5. YoY (тот же месяц год назад) ---
        yoy_pct, yoy_val = None, None
        if len(facts_hist) >= 12:
            yoy_val = facts_hist[-12]
            if yoy_val not in (None, 0):
                yoy_pct = (cur - yoy_val) / abs(yoy_val) * 100

        # --- 6. Streak: подряд идущие месяцы движения в "плохую" сторону ---
        bad_move = False
        if mom_pct is not None:
            bad_move = (mom_pct <= -STREAK_NOISE) if direction == 'up' else (mom_pct >= STREAK_NOISE)
        streak = streak + 1 if bad_move else 0

        # --- 7. Волатильность: CV(последние 3) vs CV(предыдущие 6) ---
        vol_ratio = None
        recent3 = (facts_hist[-2:] + [cur]) if len(facts_hist) >= 2 else None
        hist6 = facts_hist[-8:-2] if len(facts_hist) >= 8 else None
        if recent3 and hist6 and len(hist6) >= 6:
            m_r, m_h = np.mean(recent3), np.mean(hist6)
            cv_r = np.std(recent3) / abs(m_r) if abs(m_r) > 1e-9 else 0
            cv_h = np.std(hist6) / abs(m_h) if abs(m_h) > 1e-9 else 0
            if cv_h > 1e-6 and cv_r >= VOL_RATIO_TH * cv_h and cv_r > VOL_FLOOR:
                vol_ratio = cv_r / cv_h

        reasons = []
        if plan_pct is not None and abs(plan_pct) >= PLAN_TH:
            reasons.append(('plan', plan_pct, is_good(plan_pct, direction)))
        if mom_pct is not None and abs(mom_pct) >= MOM_TH:
            reasons.append(('mom', mom_pct, is_good(mom_pct, direction)))
        if z is not None and abs(z) >= Z_TH:
            reasons.append(('outlier', z, is_good(z, direction)))
        if iqr_rel is not None and abs(iqr_rel) >= 1:
            reasons.append(('iqr', iqr_rel, is_good(iqr_rel, direction)))
        if yoy_pct is not None and abs(yoy_pct) >= YOY_TH:
            reasons.append(('yoy', yoy_pct, is_good(yoy_pct, direction)))
        if streak >= STREAK_TH:
            reasons.append(('streak', streak, False))
        if vol_ratio is not None:
            reasons.append(('volatility', vol_ratio, None))
        if row['prognoz_flg'] == 1:
            reasons.append(('prognoz', None, None))

        real_reasons = [r for r in reasons if r[0] != 'prognoz']
        has_deviation = len(real_reasons) > 0
        primary = max(real_reasons, key=lambda r: score_of(r[0], r[1])) if real_reasons else None
        magnitude = score_of(primary[0], primary[1]) if primary else 0

        if magnitude >= 25:
            severity = 'high'
        elif magnitude >= 12:
            severity = 'medium'
        elif magnitude > 0:
            severity = 'low'
        else:
            severity = None

        records.append({
            'id_pokaz': pid,
            'date_': row['date_'].strftime('%Y-%m-%d'),
            'fact': cur,
            'prev_fact': prev_fact,
            'prev_date': prev_date,
            'plan': None if pd.isna(row['plan']) else row['plan'],
            'prognoz_flg': int(row['prognoz_flg']),
            'mom_pct': None if mom_pct is None else round(mom_pct, 1),
            'plan_pct': None if plan_pct is None else round(plan_pct, 1),
            'z': None if z is None else round(float(z), 2),
            'z_rel': None if z_rel is None else round(float(z_rel), 1),
            'iqr_rel': None if iqr_rel is None else round(float(iqr_rel), 1),
            'iqr_lo': None if iqr_lo is None else round(float(iqr_lo), 2),
            'iqr_hi': None if iqr_hi is None else round(float(iqr_hi), 2),
            'iqr_med': None if iqr_med is None else round(float(iqr_med), 2),
            'yoy_pct': None if yoy_pct is None else round(yoy_pct, 1),
            'yoy_val': yoy_val,
            'streak': streak,
            'vol_ratio': None if vol_ratio is None else round(float(vol_ratio), 2),
            'has_deviation': has_deviation,
            'severity': severity,
            'primary_reason': primary[0] if primary else None,
            'primary_value': round(primary[1], 2) if primary else None,
            'primary_good': primary[2] if primary else None,
            'reasons': [r[0] for r in reasons],
        })

        facts_hist.append(cur)
        dates_hist.append(row['date_'].strftime('%Y-%m-%d'))

rec_df = pd.DataFrame(records)
rec_by_key = {(r['id_pokaz'], r['date_']): r for r in records}

# ---------------------------------------------------------------------------
# Брифы -- простой текст для топ-менеджеров: без статистических терминов,
# с цветовой разметкой {color:ColorWarningRed}...{color} / {color:ColorGreen}...{color}
# Формат текста -- plain text с "\n" и буллетами "• ", чтобы без изменений
# грузиться в BI (никакого HTML в CSV).
# ---------------------------------------------------------------------------

MONTH_NAMES = {1: 'январе', 2: 'феврале', 3: 'марте', 4: 'апреле', 5: 'мае', 6: 'июне', 7: 'июле',
               8: 'августе', 9: 'сентябре', 10: 'октябре', 11: 'ноябре', 12: 'декабре'}
MONTH_SHORT = {1: 'янв', 2: 'фев', 3: 'мар', 4: 'апр', 5: 'май', 6: 'июн', 7: 'июл',
               8: 'авг', 9: 'сен', 10: 'окт', 11: 'ноя', 12: 'дек'}
MONTH_FULL_RU = {1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель', 5: 'Май', 6: 'Июнь', 7: 'Июль',
                 8: 'Август', 9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'}

COLOR_BAD = 'ColorWarningRed'
COLOR_GOOD = 'ColorGreen'


def short_label(date_str):
    dt = pd.Timestamp(date_str)
    return f"{MONTH_SHORT[dt.month]} {str(dt.year)[2:]}"


def full_label(date_str):
    dt = pd.Timestamp(date_str)
    return f"{MONTH_FULL_RU[dt.month]} {dt.year}"


def fmt_v(v, unit):
    if v is None:
        return '—'
    if unit == '%':
        return f"{v:.1f}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(',', ' ')
    return f"{v:.1f}"


def fmt_pct(v):
    if v is None:
        return '—'
    return f"{v:+.1f}%"


def vu(v, unit):
    """Значение с единицей измерения, без задвоения '%'."""
    if v is None:
        return '—'
    if unit == '%':
        return f"{v:.1f}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(',', ' ') + ' ' + unit
    return f"{v:.1f} {unit}"


def tag(text, good):
    """Оборачивает текст в цветовую разметку по смыслу (good=True/False), либо
    возвращает как есть, если смысл нейтральный (good is None)."""
    if good is True:
        return '{color:' + COLOR_GOOD + '}' + text + '{color}'
    if good is False:
        return '{color:' + COLOR_BAD + '}' + text + '{color}'
    return text


def good_for(reason, r, direction):
    if reason == 'plan':
        return is_good_banded(r['plan_pct'], direction, PLAN_BAND)
    if reason == 'mom':
        return is_good_banded(r['mom_pct'], direction, MOM_BAND)
    if reason == 'outlier':
        return is_good(r['z'], direction)
    if reason == 'iqr':
        return is_good(r['iqr_rel'], direction)
    if reason == 'yoy':
        return is_good(r['yoy_pct'], direction)
    if reason == 'streak':
        return False
    return None  # volatility -- нейтрально, это про нестабильность, не про хорошо/плохо


def plain_phrase(reason, r):
    """Простая формулировка без статистических терминов (z-score/IQR/CV наружу не идут)."""
    if reason == 'plan':
        return f"Отклонение от плана: {fmt_pct(r['plan_pct'])}"
    if reason == 'mom':
        return f"Изменение к предыдущему месяцу: {fmt_pct(r['mom_pct'])}"
    if reason in ('outlier', 'iqr'):
        rel = r['iqr_rel'] if r['iqr_rel'] is not None else r['z_rel']
        if rel is not None:
            return f"Значение отличается от обычного уровня последних месяцев на {fmt_pct(rel)}"
        return "Значение выходит за пределы обычных колебаний последних месяцев"
    if reason == 'yoy':
        return f"По сравнению с этим же месяцем год назад: {fmt_pct(r['yoy_pct'])}"
    if reason == 'streak':
        return f"Показатель ухудшается {int(r['streak'])}-й месяц подряд"
    if reason == 'volatility':
        return "Показатель стал нестабильным — сильные колебания от месяца к месяцу"
    return ""


def _outlier_headline(r):
    rel = r['iqr_rel'] if r['iqr_rel'] is not None else r['z_rel']
    return f"нетипичное значение ({fmt_pct(rel)})" if rel is not None else "нетипичное значение"


HEADLINE_SHORT = {
    'plan': lambda r: f"отклонение от плана {fmt_pct(r['plan_pct'])}",
    'mom': lambda r: f"{fmt_pct(r['mom_pct'])} к пред. месяцу",
    'outlier': _outlier_headline,
    'iqr': _outlier_headline,
    'yoy': lambda r: f"{fmt_pct(r['yoy_pct'])} год-к-году",
    'streak': lambda r: f"{int(r['streak'])} мес. подряд ухудшения",
    'volatility': lambda r: "рост нестабильности",
}

EXTRA_VALUE = {
    'outlier': lambda r: r['z'] or 0,
    'iqr': lambda r: r['iqr_rel'] or 0,
    'yoy': lambda r: r['yoy_pct'] or 0,
    'streak': lambda r: r['streak'] or 0,
    'volatility': lambda r: r['vol_ratio'] or 0,
}

def related_character(row):
    """Короткая пометка о характере отклонения у связанного показателя, если оно есть --
    приоритет более "весомым" сигналам (устойчивый тренд/волатильность важнее разовых)."""
    rs = row['reasons']
    if 'streak' in rs:
        return f"устойчиво, {int(row['streak'])}-й мес. подряд"
    if 'volatility' in rs:
        return "нестабилен, скачок волатильности"
    if 'outlier' in rs or 'iqr' in rs:
        return "разовый выброс"
    if 'yoy' in rs:
        return "аномалия год-к-году"
    if 'plan' in rs:
        return "расходится с планом"
    return None


def related_lines(ids, date_):
    """Связанные показатели (драйверы и зависимые вместе, без разделения на 'драйвер'/
    'зависимый показатель' -- заказчику не нужна эта терминология в тексте) -- группируем
    просто по направлению движения: кто растёт, кто снижается."""
    if not ids:
        return []

    items, no_data, seen = [], [], set()
    for rid in ids:
        if rid in seen:
            continue
        seen.add(rid)
        rrow = rec_by_key.get((rid, date_))
        rname = dim_by_id[rid]['pokaz_name']
        if not rrow or rrow['mom_pct'] is None:
            no_data.append(rname)
            continue
        items.append((rid, rname, rrow))

    if not items and not no_data:
        return []

    up = sorted((x for x in items if x[2]['mom_pct'] >= MOM_BAND), key=lambda x: -x[2]['mom_pct'])
    down = sorted((x for x in items if x[2]['mom_pct'] <= -MOM_BAND), key=lambda x: x[2]['mom_pct'])
    flat = [x for x in items if x not in up and x not in down]

    def fmt_item(rid, rname, rrow):
        rdir = dim_by_id[rid]['direction']
        chunk = f"«{rname}» {tag(fmt_pct(rrow['mom_pct']), good_for('mom', rrow, rdir))}"
        char = related_character(rrow)
        return f"{chunk} ({char})" if char else chunk

    out = []
    if up:
        extra = f", и ещё {len(up) - 3}" if len(up) > 3 else ""
        out.append("Также растут: " + ", ".join(fmt_item(*x) for x in up[:3]) + extra)
    if down:
        extra = f", и ещё {len(down) - 3}" if len(down) > 3 else ""
        out.append("Снижаются: " + ", ".join(fmt_item(*x) for x in down[:3]) + extra)
    if flat and not (up or down):
        out.append("Без выраженной динамики: " + ", ".join(f"«{n}» {fmt_pct(rr['mom_pct'])}" for _, n, rr in flat[:3]))
    if no_data and not items:
        out.append("Нет данных за месяц: " + ", ".join(no_data[:3]))
    return out


briefs = []
brief_id = 1

for r in records:
    if not (r['has_deviation'] or r['prognoz_flg'] == 1):
        continue
    if not r['has_deviation'] and r['date_'] not in RECENT_3:
        continue

    dim_row = dim_by_id[r['id_pokaz']]
    unit = dim_row['unit']
    direction = dim_row['direction']
    dt = pd.Timestamp(r['date_'])
    month_label = f"{MONTH_NAMES[dt.month]} {dt.year}"

    lines = []

    if r['prognoz_flg'] == 1:
        value_label = f"Прогноз на {MONTH_FULL_RU[dt.month].lower()}"
    else:
        value_label = 'Факт'

    if r['has_deviation']:
        reason = r['primary_reason']
        headline = f"{dim_row['pokaz_name']}: {HEADLINE_SHORT[reason](r)}"

        # 1. факт/прогноз + выполнение плана в скобках -- одной строкой
        fact_line = f"{value_label}: {vu(r['fact'], unit)}"
        if r['plan'] is not None:
            completion = (r['fact'] / r['plan'] * 100) if r['plan'] else None
            fact_line += f" (Выполнение плана: {tag(f'{completion:.0f}%', good_for('plan', r, direction))})"
        lines.append(fact_line)

        # 2. динамика к пред. месяцу + YoY -- тоже одной строкой, через запятую
        dyn_parts = []
        if r['prev_fact'] is not None:
            prev_label = short_label(r['prev_date']) if r['prev_date'] else 'пред. мес.'
            dyn_parts.append(f"Динамика vs {prev_label}: {tag(fmt_pct(r['mom_pct']), good_for('mom', r, direction))}")
        if r['yoy_pct'] is not None:
            dyn_parts.append(f"YoY: {tag(fmt_pct(r['yoy_pct']), good_for('yoy', r, direction))}")
        if dyn_parts:
            lines.append(', '.join(dyn_parts))

        # 3-4. связанные показатели (драйверы + зависимые вместе, без этих терминов в тексте) --
        # сгруппированы по направлению движения: "Также растут" / "Снижаются"
        related_ids = drivers_by_metric.get(r['id_pokaz'], []) + children_by_metric.get(r['id_pokaz'], [])
        lines.extend(related_lines(related_ids, r['date_']))
    else:
        headline = f"{dim_row['pokaz_name']}: без отклонений"
        if r['prognoz_flg'] == 1:
            lines.append(f"{value_label}: {vu(r['fact'], unit)}")
        else:
            lines.append(f"{value_label} за {month_label}: {vu(r['fact'], unit)}")
        lines.append(tag("Существенных отклонений нет", True))

    text_plain = '\n'.join('• ' + ln for ln in lines)

    briefs.append({
        'brief_id': f"B{brief_id:04d}",
        'id_pokaz': r['id_pokaz'],
        'pokaz_name': dim_row['pokaz_name'],
        'screen': dim_row['screen'],
        'category': dim_row['category'],
        'date_': r['date_'],
        'headline': headline,
        'text': text_plain,
        'severity': r['severity'] or 'info',
        'good': r['primary_good'],
        'rules_fired': ','.join(x for x in r['reasons'] if x != 'prognoz'),
    })
    brief_id += 1

briefs_df = pd.DataFrame(briefs)
briefs_df.to_csv('briefs_pokazateli.csv', index=False, encoding='utf-8-sig')

# ---------------------------------------------------------------------------
# Строки таблицы: факты за 4 фиксированных календарных месяца (мес-3..текущий) +
# прогноз за последние 2 из них + динамика/план/выполнение по самому свежему
# доступному месяцу (закрытому или ещё нет)
# ---------------------------------------------------------------------------

TABLE_MONTHS = MONTHS_ORDER[-4:]        # [мес-3, мес-2, мес-1, текущий]
FORECAST_MONTHS = TABLE_MONTHS[-2:]     # [мес-1, текущий]

table_rows = []
briefs_by_metric = {}
for br in briefs:
    briefs_by_metric.setdefault(br['id_pokaz'], []).append(br)

for pid, d in dim_by_id.items():
    g = [r for r in records if r['id_pokaz'] == pid]
    g_sorted = sorted(g, key=lambda r: r['date_'])
    closed = [r for r in g_sorted if r['prognoz_flg'] == 0]

    latest = g_sorted[-1] if g_sorted else None

    metric_briefs = sorted(briefs_by_metric.get(pid, []), key=lambda b: b['date_'], reverse=True)
    latest_dev = next((r for r in reversed(g_sorted) if r['has_deviation']), None)

    # "На пути к цели" -- прогноз по темпу роста последних 3 закрытых месяцев на дату цели
    target_value = d.get('target_value')
    on_track = None
    if target_value is not None and pd.notna(target_value) and len(closed) >= 4:
        base = closed[-4]
        last_c = closed[-1]
        growth_per_month = (last_c['fact'] - base['fact']) / 3
        months_remaining = (pd.Timestamp(LAST_MONTH).to_period('M') - pd.Timestamp(last_c['date_']).to_period('M')).n
        projected = last_c['fact'] + growth_per_month * months_remaining
        gap_pct = (projected - target_value) / abs(target_value) * 100 if target_value else 0
        on_track = (gap_pct >= -5) if d['direction'] == 'up' else (gap_pct <= 5)

    facts_by_month = {}
    forecast_by_month = {}
    for m in TABLE_MONTHS:
        rr = rec_by_key.get((pid, m))
        if rr and rr['prognoz_flg'] == 0:
            facts_by_month[m] = rr['fact']
    for m in FORECAST_MONTHS:
        rr = rec_by_key.get((pid, m))
        if rr and rr['prognoz_flg'] == 1:
            forecast_by_month[m] = rr['fact']

    table_rows.append({
        'id_pokaz': pid,
        'pokaz_name': d['pokaz_name'],
        'screen': d['screen'],
        'category': d['category'],
        'unit': d['unit'],
        'direction': d['direction'],
        'is_important': bool(d['is_important']),
        'has_target': bool(d['has_target']),
        'target_value': None if pd.isna(target_value) else target_value,
        'on_track': on_track,
        'driver_ids': drivers_by_metric.get(pid, []),
        'facts_by_month': [facts_by_month.get(m) for m in TABLE_MONTHS],
        'forecast_by_month': [forecast_by_month.get(m) for m in FORECAST_MONTHS],
        'latest_date': latest['date_'] if latest else None,
        'latest_is_prognoz': bool(latest['prognoz_flg']) if latest else None,
        'plan_last': latest['plan'] if latest else None,
        'mom_pct': latest['mom_pct'] if latest else None,
        'plan_pct': latest['plan_pct'] if latest else None,
        'brief_ids': [b['brief_id'] for b in metric_briefs],
        'latest_deviation_severity': latest_dev['severity'] if latest_dev else None,
    })

# ---------------------------------------------------------------------------
# Summary для правой панели: пары "всего / из них ..."
# ---------------------------------------------------------------------------

total_metrics = len(table_rows)

rows_with_plan = [r for r in table_rows if r['plan_pct'] is not None]
meeting_plan = sum(1 for r in rows_with_plan if is_good_banded(r['plan_pct'], r['direction'], 4) is not False)  # good или нейтрально

total_with_target = sum(1 for r in table_rows if r['has_target'])
on_track_count = sum(1 for r in table_rows if r['on_track'] is True)

total_important = sum(1 for r in table_rows if r['is_important'])
important_with_deviation = sum(
    1 for r in table_rows if r['is_important'] and r['latest_deviation_severity'] in ('high', 'medium')
)

deviations_recent = sum(1 for r in records if r['has_deviation'] and r['date_'] in RECENT_3)
with_forecast = sum(1 for r in table_rows if any(v is not None for v in r['forecast_by_month']))

# главное отклонение: самое сильное среди последних 3 месяцев -- только по закрытым
# фактам (не по прогнозным), чтобы не выносить в заголовок ещё не подтверждённую цифру
closed_recent = [r for r in records if r['has_deviation'] and r['date_'] in RECENT_3 and r['prognoz_flg'] == 0]
candidates = [r for r in closed_recent if r['severity'] == 'high']
if not candidates:
    candidates = closed_recent

top = max(candidates, key=lambda r: score_of(r['primary_reason'], r['primary_value'])) if candidates else None
top_deviation = None
if top:
    top_brief = next((b for b in briefs if b['id_pokaz'] == top['id_pokaz'] and b['date_'] == top['date_']), None)
    top_deviation = {
        'id_pokaz': top['id_pokaz'],
        'pokaz_name': dim_by_id[top['id_pokaz']]['pokaz_name'],
        'screen': dim_by_id[top['id_pokaz']]['screen'],
        'date_': top['date_'],
        'headline': top_brief['headline'] if top_brief else '',
        'text': top_brief['text'] if top_brief else '',
        'severity': top['severity'],
    }

summary = {
    'total_metrics': total_metrics,
    'meeting_plan': meeting_plan,
    'plan_base': len(rows_with_plan),
    'total_with_target': total_with_target,
    'on_track_count': on_track_count,
    'total_important': total_important,
    'important_with_deviation': important_with_deviation,
    'deviations_recent_3m': deviations_recent,
    'with_forecast': with_forecast,
    'top_deviation': top_deviation,
}

RULES_META = [
    {'key': 'plan', 'label': 'Отклонение от плана', 'rule': f'|факт/план - 1| ≥ {PLAN_TH:.0f}%'},
    {'key': 'mom', 'label': 'Динамика к пред. месяцу', 'rule': f'|МоМ| ≥ {MOM_TH:.0f}%'},
    {'key': 'outlier', 'label': 'Выброс (z-score)', 'rule': f'|z| ≥ {Z_TH:.1f} к среднему за 6 мес.'},
    {'key': 'iqr', 'label': 'Выброс по IQR', 'rule': 'вне [Q1-1.5·IQR; Q3+1.5·IQR] за 12 мес.'},
    {'key': 'yoy', 'label': 'Год-к-году', 'rule': f'|Δ к тому же месяцу год назад| ≥ {YOY_TH:.0f}%'},
    {'key': 'streak', 'label': 'Устойчивый тренд', 'rule': f'{STREAK_TH}+ мес. подряд ухудшения (шаг ≥ {STREAK_NOISE:.0f}%)'},
    {'key': 'volatility', 'label': 'Рост волатильности', 'rule': f'CV(3 мес.) ≥ {VOL_RATIO_TH:.1f}× CV(пред. 6 мес.)'},
]

# ---------------------------------------------------------------------------
# Бандл для HTML
# ---------------------------------------------------------------------------

dim_list = dim.to_dict('records')
for d in dim_list:
    d['driver_ids'] = drivers_by_metric.get(d['id_pokaz'], [])

table_months_meta = [{'date_': m, 'label': short_label(m), 'full': full_label(m)} for m in TABLE_MONTHS]
forecast_months_meta = [{'date_': m, 'label': short_label(m), 'full': full_label(m)} for m in FORECAST_MONTHS]

bundle = {
    'dim': dim_list,
    'table_rows': table_rows,
    'facts': rec_df.to_dict('records'),
    'briefs': briefs,
    'months': MONTHS_ORDER,
    'table_months': table_months_meta,
    'forecast_months': forecast_months_meta,
    'last_month': LAST_MONTH,
    'recent_3': RECENT_3,
    'summary': summary,
    'rules_meta': RULES_META,
}


def clean_nans(obj):
    if isinstance(obj, dict):
        return {k: clean_nans(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_nans(v) for v in obj]
    if isinstance(obj, float) and np.isnan(obj):
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


bundle = clean_nans(bundle)

with open('bundle.json', 'w', encoding='utf-8') as f:
    json.dump(bundle, f, ensure_ascii=False, default=str)

print('records:', len(records), '| deviations:', rec_df['has_deviation'].sum(), '| briefs:', len(briefs))
print('summary:', json.dumps(summary, ensure_ascii=False, indent=2, default=str)[:1200])
rule_counts = {}
for r in records:
    for x in r['reasons']:
        if x != 'prognoz':
            rule_counts[x] = rule_counts.get(x, 0) + 1
print('rule fire counts:', rule_counts)
