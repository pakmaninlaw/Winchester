"""
ВИНЧЕСТЕРЪ | Оружейная колбасная гильдия. Финальный монолит. Редакция 10.
СЛОИ: 1 домен | 2 подстановка нейрофото + заглушка | 3 контроллеры
      | 4 представление | 5 авто-QA и точка входа.
Бизнес-логика только в слое 1; фронтенд лишь перерисовывает ответ сервера.
РЕД. 10 (решение СА): оверлеи добавок поверх нейрофото УДАЛЕНЫ ПОЛНОСТЬЮ --
кадр всегда чистый; добавки влияют только на цену, граммы и корзину.
Цепочка кадра: файл пары (мясо,технология) -> байты в браузер; нет файла -> заглушка.
"""
import io
import json
import math
import os
import re
import sys
import threading
import traceback
import webbrowser
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from threading import Timer

from flask import Flask, render_template_string, request, jsonify, send_file, abort
from PIL import Image, ImageDraw

app = Flask(__name__)

BUILD = "ред. 10 (финальный монолит)"
PORT = 5000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # деплой: пути от файла, не от cwd
def _asset(name): return os.path.join(BASE_DIR, name)
ORDERS_FILE = _asset("winchester_orders.json")
BANNER_FILE = _asset("banner.jpg")
BANNER_URL = "https://lh3.googleusercontent.com/d/1GDlW_Cvmf2vQGLptNfH_XkvtPTmY3g6I"

# Нейрофото срезов: базовые пары мясо+технология. Нет файла -> заглушка.
PHOTOS = {
    ("beef", "cured"): "slice_beef_cured.png",
    ("beef", "smoked"): "slice_beef_smoked.png",
    ("beef", "halfsmoked"): "slice_beef_halfsmoked.png",
    ("beef", "boiled"): "slice_beef_boiled.png",
    ("pork", "cured"): "slice_pork_cured.png",
    ("pork", "smoked"): "slice_pork_smoked.png",
    ("pork", "halfsmoked"): "slice_pork_halfsmoked.png",
    ("pork", "boiled"): "slice_pork_boiled.png",
    ("turkey", "cured"): "slice_turkey_cured.png",
    ("turkey", "smoked"): "slice_turkey_smoked.png",
    ("turkey", "halfsmoked"): "slice_turkey_halfsmoked.png",
    ("turkey", "boiled"): "slice_turkey_boiled.png",
}
# Эталоны гастрономических наборов под напитки (пресеты).
SETS = {
    "red_wine": "set_red_wine.png",
    "white_wine": "set_white_wine.png",
    "cognac": "set_cognac.png",
    "pepper_vodka": "set_pepper_vodka.png",
}


# ============================================================================
# СЛОЙ 1. ДОМЕН И БИЗНЕС-ПРАВИЛА
# ============================================================================
MEATS = {
    "beef":   {"label": "Говядина Black Angus", "price_per_kg": 2900, "flesh": (90, 22, 30)},
    "pork":   {"label": "Фермерская свинина",   "price_per_kg": 2200, "flesh": (142, 57, 66)},
    "turkey": {"label": "Фермерская индейка",   "price_per_kg": 2400, "flesh": (196, 121, 128)},
}
# icon_w/icon_h -- горизонтальная миниатюра патрона, пропорционально калибру.
CALIBERS = {
    "410": {"label": "Калибр .410 (Дегустационный)", "weight_g": 500,  "days_bonus": 0,  "icon_w": 26, "icon_h": 9},
    "20":  {"label": "20 калибр (Ходовой патрон)",   "weight_g": 1200, "days_bonus": 0,  "icon_w": 30, "icon_h": 12},
    "16":  {"label": "16 калибр (Двойной заряд)",    "weight_g": 2000, "days_bonus": 5,  "icon_w": 32, "icon_h": 14},
    "12":  {"label": "12 калибр (Магнум)",           "weight_g": 3000, "days_bonus": 10, "icon_w": 34, "icon_h": 16},
}
TECHS = {
    "cured":      {"label": "Сыровяленая в благородной плесени", "base_days": 45, "markup_per_kg": 450},
    "smoked":     {"label": "Сырокопченая на ольховой щепе",     "base_days": 30, "markup_per_kg": 300},
    "halfsmoked": {"label": "Охотничья варено-копченая",         "base_days": 5,  "markup_per_kg": 150},
    "boiled":     {"label": "Деликатесная вареная",              "base_days": 2,  "markup_per_kg": 0},
}
# price -- цена добавки при эталонной партии 1.2 кг; share -- доля в техкарте.
ADDONS = {
    "pistachio": {"label": "Сицилийская цельная фисташка", "price": 250, "share": 0.05},
    "truffle":   {"label": "Стружка черного трюфеля",      "price": 450, "share": 0.02},
    "cheese":    {"label": "Выдержанный пармезан 24 мес.", "price": 300, "share": 0.06},
    "tomato":    {"label": "Вяленые томаты с розмарином",  "price": 200, "share": 0.04},
}
REF_WEIGHT_KG = 1.2     # точка фиксации цен добавок (правило СА)
TUBE_PRICE = 650        # тубус -- физический предмет: цена фиксирована
SALT_SHARE = 0.022      # БИЗНЕС-ПРАВИЛО: соль = ровно 2.2% базовой массы
ADDON_KEYS = tuple(ADDONS.keys())
PHONE_RE = re.compile(r"^[0-9+()\- ]{6,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


@dataclass(frozen=True)
class Config:
    """Неизменяемый снимок конфигурации партии (frozen dataclass, а не dict:
    защита от мутаций посреди запроса)."""
    meat: str = "beef"
    caliber: str = "20"
    tech: str = "cured"
    fat: int = 20
    spice: float = 1.5
    pistachio: bool = False
    truffle: bool = False
    cheese: bool = False
    tomato: bool = False
    tube: bool = False


DEFAULT = Config()


def _to_bool(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _clamp_int(raw, lo, hi, default) -> int:
    """БИЗНЕС-ПРАВИЛО: граница диапазона; мусор в query -> default."""
    try:
        value = int(float(str(raw)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _clamp_float(raw, lo, hi, default, nd=1) -> float:
    try:
        value = round(float(str(raw)), nd)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def parse_config(args) -> Config:
    """ЕДИНАЯ точка парсинга: срез и смета не могут разъехаться."""
    meat = args.get("meat", DEFAULT.meat)
    if meat not in MEATS:
        meat = DEFAULT.meat
    caliber = args.get("caliber", DEFAULT.caliber)
    if caliber not in CALIBERS:
        caliber = DEFAULT.caliber
    tech = args.get("tech", DEFAULT.tech)
    if tech not in TECHS:
        tech = DEFAULT.tech
    flags = {k: _to_bool(args.get(k, False)) for k in ADDON_KEYS + ("tube",)}
    return Config(meat=meat, caliber=caliber, tech=tech,
                  fat=_clamp_int(args.get("fat", DEFAULT.fat), 0, 30, DEFAULT.fat),
                  spice=_clamp_float(args.get("spice", DEFAULT.spice), 0.5, 3.0, DEFAULT.spice),
                  **flags)


def fmt_money(value: float) -> str:
    """Правило СА: премиальный прайс без копеек -- целые рубли с пробелами."""
    return f"{int(round(value)):,} ₽".replace(",", " ")


def fmt_g(value: float) -> str:
    return f"{value:,.1f} г".replace(",", " ")


def addon_price(key: str, weight_kg: float) -> float:
    """БИЗНЕС-ПРАВИЛО: цена добавки пропорциональна массе (фиксация на 1.2 кг),
    округление до ЦЕЛОГО рубля."""
    return float(round(ADDONS[key]["price"] * weight_kg / REF_WEIGHT_KG))


def verify_price(cfg: Config, weight_kg: float, lines, total: float) -> dict:
    """Серверный аудит сметы (из UI убран по правке СА, остался в QA и /api/quote)."""
    direct = weight_kg * (MEATS[cfg.meat]["price_per_kg"] + TECHS[cfg.tech]["markup_per_kg"])
    direct += sum(addon_price(k, weight_kg) for k in ADDON_KEYS if getattr(cfg, k))
    direct += TUBE_PRICE if cfg.tube else 0
    summed = sum(lines)
    diff = abs(round(summed, 2) - round(direct, 2))
    ok = math.isclose(summed, direct, abs_tol=0.01)
    message = ("[OK] Смета верифицирована. Расхождение: 0.00 руб." if ok
               else f"[РАСХОЖДЕНИЕ] Расхождение: {diff:.2f} руб. Требуется ручная сверка.")
    return {"ok": ok, "diff": round(diff, 2), "message": message}


def build_quote(cfg: Config) -> dict:
    """Полный расчет: техкарта, сроки, смета, проверка."""
    weight_g = CALIBERS[cfg.caliber]["weight_g"]
    weight_kg = weight_g / 1000.0
    # ТЕХКАРТА: база (мясо+шпик+соль+специи) = масса калибра; добавки СВЕРХУ.
    salt_g = round(weight_g * SALT_SHARE, 1)
    spice_g = round(weight_g * cfg.spice / 100.0, 1)
    fat_g = round(weight_g * cfg.fat / 100.0, 1)
    meat_g = round(weight_g - salt_g - spice_g - fat_g, 1)
    addon_grams = [(k, round(weight_g * ADDONS[k]["share"], 1))
                   for k in ADDON_KEYS if getattr(cfg, k)]
    total_g = round(weight_g + sum(g for _, g in addon_grams), 1)
    grams = [{"label": "Мясо, основа", "g": fmt_g(meat_g)},
             {"label": "Шпик", "g": fmt_g(fat_g)},
             {"label": "Соль морская и нитритная (2.2%)", "g": fmt_g(salt_g)},
             {"label": "Перец и пряности", "g": fmt_g(spice_g)}]
    grams += [{"label": ADDONS[k]["label"], "g": fmt_g(g)} for k, g in addon_grams]
    base_days = TECHS[cfg.tech]["base_days"]
    bonus_days = CALIBERS[cfg.caliber]["days_bonus"]
    days = base_days + bonus_days
    ready_date = (date.today() + timedelta(days=days)).strftime("%d.%m.%Y")
    # СМЕТА: наценка технологии ПРОПОРЦИОНАЛЬНА массе (0.5 кг * 150 = 75 ₽).
    addon_prices = {k: addon_price(k, weight_kg) for k in ADDON_KEYS}
    line_meat = round(weight_kg * MEATS[cfg.meat]["price_per_kg"], 2)
    line_tech = round(weight_kg * TECHS[cfg.tech]["markup_per_kg"], 2)
    line_addons = round(sum(addon_prices[k] for k in ADDON_KEYS if getattr(cfg, k)), 2)
    line_pack = float(TUBE_PRICE if cfg.tube else 0)
    lines = [line_meat, line_tech, line_addons, line_pack]
    total = round(sum(lines), 2)
    return {
        "summary": f"{MEATS[cfg.meat]['label']} | {CALIBERS[cfg.caliber]['label']} | "
                   f"{TECHS[cfg.tech]['label']} | {fmt_g(float(weight_g))}",
        "weight_g": weight_g, "weight_fmt": fmt_g(float(weight_g)),
        "total_fmt": fmt_g(total_g),
        "base_days": base_days, "bonus_days": bonus_days,
        "days": days, "ready_date": ready_date,
        "grams": grams,
        "addon_prices_fmt": {k: fmt_money(v) for k, v in addon_prices.items()},
        "price": {"meat": line_meat, "meat_fmt": fmt_money(line_meat),
                  "tech": line_tech, "tech_fmt": fmt_money(line_tech),
                  "addons": line_addons, "addons_fmt": fmt_money(line_addons),
                  "pack": line_pack, "pack_fmt": fmt_money(line_pack),
                  "total": total, "total_fmt": fmt_money(total)},
        "verify": verify_price(cfg, weight_kg, lines, total),
    }


PRESETS = [
    {"key": "red_wine", "label": "К красному сухому вину",
     "patch": {"meat": "beef", "caliber": "12", "tech": "cured", "truffle": True, "cheese": True}},
    {"key": "white_wine", "label": "К сухому белому вину",
     "patch": {"meat": "turkey", "caliber": "410", "tech": "halfsmoked", "pistachio": True, "tomato": True}},
    {"key": "cognac", "label": "К выдержанному коньяку",
     "patch": {"meat": "pork", "caliber": "16", "tech": "smoked", "fat": 20, "truffle": True}},
    {"key": "pepper_vodka", "label": "К охотничьей перцовке",
     "patch": {"meat": "pork", "caliber": "20", "tech": "halfsmoked", "spice": 3.0, "cheese": True}},
]
for _p in PRESETS:
    _p["patch_json"] = json.dumps(_p["patch"], ensure_ascii=False)


# ============================================================================
# СЛОЙ 2. ПОДСТАНОВКА НЕЙРОФОТО + ЗАГЛУШКА (без оверлеев по решению СА ред. 10)
# ============================================================================

def _stub_png() -> bytes:
    """Премиальная заглушка (правка СА #4): рамка + текст вместо среза."""
    img = Image.new("RGBA", (400, 400), (11, 8, 7, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 389, 389], outline=(216, 170, 99, 255), width=2)
    d.rectangle([18, 18, 381, 381], outline=(84, 44, 27, 255), width=1)
    d.multiline_text((62, 140),
                     "По техническим причинам\nмы не можем показать вам\nвид готового продукта.\n\nРецепт уже у мастера --\nпартия будет сфотографирована\nпосле созревания.",
                     fill=(212, 217, 220, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _resolve_asset(name):
    """Поиск файла терпимо: точное совпадение (пробелы/регистр), затем по стежню --
    лечит двойные расширения, мусор в хвосте и полноширинные точки после нейросетей."""
    if not name:
        return None
    target = name.strip().lower()
    stem = re.split(r"[.．]", target)[0]
    try:
        names = os.listdir(BASE_DIR)
    except OSError:
        return None
    for n in names:
        if n.strip().lower() == target:
            return os.path.join(BASE_DIR, n)
    for n in names:
        low = n.strip().lower()
        if re.split(r"[.．]", low)[0] == stem and low != target:
            return os.path.join(BASE_DIR, n)
    return None


def _mime(path: str) -> str:
    """Mimetype по сигнатуре байтов: JFIF-в-.png уходит как image/jpeg."""
    with open(path, "rb") as f:
        head = f.read(3)
    if head == b"\x89PN":
        return "image/png"
    if head[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "application/octet-stream"


def render_slice(cfg: Config) -> bytes:
    """ДИСПЕТЧЕР (ред. 10): чистое нейрофото пары (мясо,технология) байтами.
    Добавки по решению СА кадр НЕ меняют (никаких пикселей поверх).
    Нет файла -> премиальная заглушка. ГАРАНТИРОВАННЫЙ возврат."""
    photo = PHOTOS.get((cfg.meat, cfg.tech))
    path = _resolve_asset(photo) if photo else None
    if path:
        with open(path, "rb") as f:
            return f.read()
    return _stub_png()


# ============================================================================
# СЛОЙ 3. HTTP-КОНТРОЛЛЕРЫ
# ============================================================================

_orders_lock = threading.Lock()


def _load_orders():
    if not os.path.exists(ORDERS_FILE):
        return []
    try:
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _validate_contact(payload):
    """Серверная валидация контактов: фронтенду нельзя доверять."""
    errors = {}
    name = str(payload.get("name", "")).strip()
    if len(name) < 2:
        errors["name"] = "Укажите имя (минимум 2 символа)."
    phone = str(payload.get("phone", "")).strip()
    if not PHONE_RE.match(phone) or sum(ch.isdigit() for ch in phone) < 6:
        errors["phone"] = "Укажите телефон: минимум 6 цифр."
    email = str(payload.get("email", "")).strip()
    if not EMAIL_RE.match(email):
        errors["email"] = "Укажите почту в формате name@domain.ru."
    return name, phone, email, errors


def slice_query(cfg: Config) -> str:
    parts = [f"meat={cfg.meat}", f"caliber={cfg.caliber}", f"tech={cfg.tech}",
             f"fat={cfg.fat}", f"spice={cfg.spice}"]
    parts += [f"{k}={1 if getattr(cfg, k) else 0}" for k in ADDON_KEYS + ("tube",)]
    return "&".join(parts)


@app.route("/")
def index():
    cfg = parse_config(request.args)
    q = build_quote(cfg)
    return render_template_string(
        TEMPLATE, cfg=cfg, q=q, presets=PRESETS, meats=MEATS, calibers=CALIBERS,
        techs=TECHS, addons=ADDONS, tube_price=TUBE_PRICE, banner_url=BANNER_URL,
        build=BUILD,
        state_json=json.dumps(asdict(cfg), ensure_ascii=False),
        default_json=json.dumps(asdict(DEFAULT), ensure_ascii=False),
        quote_json=json.dumps(q, ensure_ascii=False),
        slice_qs=slice_query(cfg))


@app.route("/api/slice.png")
def slice_png():
    cfg = parse_config(request.args)
    try:
        png = render_slice(cfg)
    except Exception:
        traceback.print_exc()
        png = None
    if not png:
        png = _stub_png()   # двойная страховка: кадр не может стать None
    mt = "image/png" if png[:8] == b"\x89PNG\r\n\x1a\n" else (
         "image/jpeg" if png[:2] == b"\xff\xd8" else "application/octet-stream")
    response = app.response_class(png, mimetype=mt)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/quote")
def quote():
    """Единственный источник истины по деньгам и срокам для фронтенда."""
    return jsonify(build_quote(parse_config(request.args)))


@app.route("/api/order", methods=["POST"])
def order():
    payload = request.get_json(silent=True) or {}
    name, phone, email, errors = _validate_contact(payload)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    cfg = parse_config(payload)
    q = build_quote(cfg)
    with _orders_lock:
        orders = _load_orders()
        order_no = f"#048-{len(orders) + 1:04d}"
        orders.append({"order_no": order_no,
                       "created": datetime.now().isoformat(timespec="seconds"),
                       "contact": {"name": name, "phone": phone, "email": email},
                       "config": asdict(cfg), "total": q["price"]["total"]})
        try:
            with open(ORDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(orders, f, ensure_ascii=False, indent=2)
        except OSError as e:
            return jsonify({"ok": False, "errors": {"phone": f"Журнал недоступен: {e}"}}), 500
    print(f"[ЗАЯВКА] {order_no} | {name} | {q['summary']} | итог {q['price']['total_fmt']}")
    return jsonify({"ok": True, "order_no": order_no})


@app.route("/set/<key>.png")
def set_png(key):
    path = _resolve_asset(SETS.get(key, "")) if key in SETS else None
    if not path:
        abort(404)
    return send_file(path, mimetype=_mime(path))


@app.route("/tube.png")
def tube_png():
    path = _resolve_asset("pack_tube.png")
    if not path:
        abort(404)
    return send_file(path, mimetype=_mime(path))


@app.route("/banner.jpg")
def banner_local():
    if not os.path.exists(BANNER_FILE):
        abort(404)
    return send_file(BANNER_FILE, mimetype="image/jpeg")


@app.route("/api/debug")
def debug_info():
    """Форензика: что именно исполняется. Открой в браузере и пришли мне."""
    return jsonify({"build": BUILD, "python": sys.version.split()[0],
                    "pillow": getattr(Image, "__version__", "unknown"),
                    "orders_file": ORDERS_FILE,
                    "orders_count": len(_load_orders()),
                    "photos_found": sum(1 for p in list(PHOTOS.values()) + list(SETS.values())
                                        + ["pack_tube.png", "banner.jpg"]
                                        if os.path.exists(_asset(p)))})


@app.route("/favicon.ico")
def favicon():
    return app.response_class(status=204)


# ============================================================================
# СЛОЙ 4. ПРЕДСТАВЛЕНИЕ (HTML/CSS/JS внутри монолита)
# ============================================================================

TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ВИНЧЕСТЕРЪ | Оружейная колбасная гильдия</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  :root{--walnut:#2C1712; --frame:#542C1B; --brass:#D8AA63; --parchment:#D4D9DC;
    --brass-dim:#9C7A45; --line:rgba(216,170,99,.22); --muted:rgba(212,217,220,.60);}
  html,body{background:#0B0807; color:var(--parchment);
    font-family:"Segoe UI",Arial,sans-serif; margin:0;}
  h1,h2,h3{font-family:Georgia,"Times New Roman",serif;}
  .wrap{max-width:1280px; margin:0 auto; padding:0 22px 60px;}
  .hidden{display:none!important;}
  .banner-wrapper{position:relative; background:#0B0807; display:flex;
    justify-content:center; max-width:900px; margin:0 auto; overflow:hidden;}
  .banner-img{display:block; width:100%;
    -webkit-mask-image:radial-gradient(ellipse 85% 70% at 50% 50%, black 45%, transparent 96%);
    mask-image:radial-gradient(ellipse 85% 70% at 50% 50%, black 45%, transparent 96%);}
  .banner-wrapper::after{content:''; position:absolute; inset:0; pointer-events:none;
    background:radial-gradient(ellipse 70% 60% at 50% 50%, transparent 35%, #0B0807 92%);}
  .hero{background:transparent; border:none; box-shadow:none; padding:6px 0 18px;}
  .hero-inner{max-width:1280px; margin:0 auto; padding:0 22px;
    display:flex; align-items:center; gap:26px; flex-wrap:wrap;}
  .emblem{width:100px; height:auto; flex:0 0 auto; opacity:.95;}
  .brand h1{margin:0; font-size:34px; letter-spacing:.14em;}
  .brand .sub{margin:4px 0 0; color:var(--brass); letter-spacing:.22em;
    font-size:12px; text-transform:uppercase;}
  .grid{display:grid; grid-template-columns:minmax(0,5fr) minmax(0,4fr); gap:26px;}
  @media (max-width:980px){.grid{grid-template-columns:1fr;}}
  .card{background:linear-gradient(180deg,#2C1712,#231310); border:1px solid var(--frame);
    border-radius:14px; padding:22px; margin-bottom:22px; box-shadow:0 12px 30px rgba(0,0,0,.45);}
  .card h2{margin:0 0 14px; font-size:19px; letter-spacing:.10em;}
  .step{margin:34px 0 10px; padding-bottom:8px; border-bottom:1px solid var(--frame);
    color:var(--brass); font-size:11px; letter-spacing:.18em; text-transform:uppercase;}
  .card .step:first-of-type{margin-top:6px;}
  .opt{display:flex; justify-content:space-between; gap:12px; align-items:center;
    padding:8px 10px; border:1px solid transparent; border-radius:8px; cursor:pointer;}
  .opt:hover{border-color:var(--line); background:rgba(216,170,99,.06);}
  .opt input{accent-color:var(--brass); margin-right:10px;}
  .opt .price{color:var(--muted); font-size:12px; white-space:nowrap;}
  .shell{display:inline-block; vertical-align:middle; margin-right:8px; opacity:.95;}
  #cfg input:disabled{opacity:.35; cursor:not-allowed;}
  .btn-ghost:disabled,.btn-brass:disabled{opacity:.45; cursor:not-allowed; filter:none;}
  .range-row{padding:8px 10px;}
  .range-row input[type=range]{width:100%; accent-color:var(--brass);}
  .range-head{display:flex; justify-content:space-between; font-size:13px;}
  .range-head output{color:var(--brass); font-variant-numeric:tabular-nums;}
  .presets{display:flex; gap:10px; flex-wrap:wrap; margin:0 0 22px;}
  .btn-ghost{background:transparent; border:1px solid var(--brass-dim); color:var(--brass);
    border-radius:10px; padding:9px 16px; font-size:13px; letter-spacing:.06em; cursor:pointer;}
  .btn-ghost:hover{background:rgba(216,170,99,.12);}
  .btn-brass{background:linear-gradient(180deg,#E7C078,#B08540); color:#1A120C;
    border:1px solid #F0D9A6; border-radius:10px; padding:12px 22px; font-size:14px;
    font-weight:600; letter-spacing:.08em; cursor:pointer; width:100%;}
  .btn-brass:hover{filter:brightness(1.07);}
  table{width:100%; border-collapse:collapse; font-size:13.5px;}
  td{padding:7px 8px; border-bottom:1px dashed rgba(84,44,27,.85);}
  td.num{text-align:right; font-variant-numeric:tabular-nums;}
  td.lbl{color:rgba(212,217,220,.82);}
  .slice-box{background:radial-gradient(circle at 50% 42%, #2C1712 0%, #0B0807 75%);
    border:1px solid var(--frame); border-radius:12px; padding:10px; text-align:center;}
  .slice-box img{width:100%; max-width:100%; height:auto; display:block;
    margin:0 auto; border-radius:10px;}
  .tube-thumb{width:130px; margin:12px auto 0; display:block;
    border-radius:10px; border:1px solid var(--frame);}
  .tube-stub{width:230px; margin:12px auto 0; padding:14px 12px;
    border:1px dashed var(--brass-dim); border-radius:10px; color:var(--brass);
    font-size:12px; letter-spacing:.06em; text-align:center; background:rgba(216,170,99,.06);}
  .summary{margin-top:10px; color:var(--muted); font-size:12.5px;}
  .days-big{font-size:30px; color:var(--brass);}
  .total-big{font-size:32px; font-variant-numeric:tabular-nums;}
  .verify-ok{border:1px solid var(--brass-dim); color:var(--brass); background:rgba(216,170,99,.08);
    border-radius:8px; padding:9px 12px; font-size:12.5px; letter-spacing:.06em;}
  .verify-bad{border:1px solid #7E3B32; color:#E08A7C; background:rgba(126,59,50,.12);
    border-radius:8px; padding:9px 12px; font-size:12.5px;}
  .muted{color:var(--muted); font-size:12.5px;}
  footer{border-top:1px solid var(--frame); margin-top:30px; padding-top:16px;
    color:var(--muted); font-size:12px; letter-spacing:.08em;}
  .overlay{position:fixed; inset:0; background:rgba(5,3,2,.72); display:flex;
    align-items:center; justify-content:center; z-index:50; padding:18px;}
  .overlay.hidden{display:none;}
  .modal{width:min(460px,94vw); margin:0;}
  .modal label{display:block; margin:12px 0 4px; font-size:12px; letter-spacing:.08em;
    color:var(--brass); text-transform:uppercase;}
  .modal input{width:100%; box-sizing:border-box; background:#1D0F0B; border:1px solid var(--frame);
    border-radius:8px; color:var(--parchment); padding:10px 12px; font-size:14px;}
  .modal input:focus{outline:1px solid var(--brass-dim);}
  .modal .row{display:flex; gap:10px; margin-top:16px;}
  .toast{position:fixed; left:50%; bottom:34px; transform:translateX(-50%);
    background:linear-gradient(180deg,#E7C078,#B08540); color:#1A120C;
    border:1px solid #F0D9A6; border-radius:12px; padding:14px 26px; font-size:14px;
    font-weight:600; letter-spacing:.06em; box-shadow:0 14px 40px rgba(0,0,0,.55);
    z-index:60; animation:tin .35s ease;}
  @keyframes tin{from{opacity:0; transform:translate(-50%,12px);}
    to{opacity:1; transform:translate(-50%,0);}}
</style>
</head>
<body>

<header class="hero">
  <div class="banner-wrapper">
    <img id="banner" class="banner-img" src="/banner.jpg" alt="ВИНЧЕСТЕРЪ: плашка бренда">
  </div>
  <div class="hero-inner">
    <svg class="emblem" viewBox="0 0 220 140" aria-hidden="true">
      <g stroke="#D8AA63" fill="none">
        <circle cx="110" cy="70" r="64" stroke-width="2" opacity="0.35"/>
        <circle cx="110" cy="70" r="56" stroke-width="1" opacity="0.22"/>
      </g>
      <g transform="rotate(24 110 70)">
        <rect x="18" y="62" width="120" height="15" rx="7.5" fill="#8E3942" stroke="#D8AA63" stroke-width="1.5"/>
        <circle cx="42" cy="69" r="2" fill="#E8D9C6"/><circle cx="66" cy="66" r="1.6" fill="#E8D9C6"/>
        <circle cx="90" cy="71" r="2.2" fill="#E8D9C6"/><circle cx="114" cy="67" r="1.5" fill="#E8D9C6"/>
        <path d="M138 62 L178 54 L186 70 L178 84 L138 77 Z" fill="#5A3A22" stroke="#D8AA63" stroke-width="1.5"/>
        <rect x="132" y="60" width="10" height="19" rx="3" fill="#D8AA63"/>
      </g>
      <g transform="rotate(-24 110 70)">
        <rect x="18" y="62" width="120" height="15" rx="7.5" fill="#8E3942" stroke="#D8AA63" stroke-width="1.5"/>
        <circle cx="46" cy="70" r="2" fill="#E8D9C6"/><circle cx="72" cy="67" r="1.6" fill="#E8D9C6"/>
        <circle cx="98" cy="71" r="2.2" fill="#E8D9C6"/><circle cx="120" cy="68" r="1.5" fill="#E8D9C6"/>
        <path d="M138 62 L178 54 L186 70 L178 84 L138 77 Z" fill="#5A3A22" stroke="#D8AA63" stroke-width="1.5"/>
        <rect x="132" y="60" width="10" height="19" rx="3" fill="#D8AA63"/>
      </g>
      <circle cx="110" cy="70" r="20" fill="#0B0807" stroke="#D8AA63" stroke-width="2"/>
      <text x="110" y="79" text-anchor="middle" font-family="Georgia, serif" font-size="24" font-weight="bold" fill="#D8AA63">W</text>
    </svg>
    <div class="brand">
      <h1>ВИНЧЕСТЕРЪ</h1>
      <div class="sub">Оружейная колбасная гильдия | Winchester Craft Charcuterie</div>
    </div>
  </div>
</header>

<div class="wrap">
  <div class="presets">
    {% for p in presets %}
    <button type="button" class="btn-ghost preset" data-key="{{ p.key }}" data-patch='{{ p.patch_json }}'>{{ p.label }}</button>
    {% endfor %}
  </div>

  <div class="grid">
    <div id="cfg">
      <div class="card">
        <h2>Конфигуратор партии</h2>

        <div class="step">Шаг I. Основа мяса</div>
        {% for key, m in meats.items() %}
        <label class="opt"><span><input type="radio" name="meat" value="{{ key }}" {{ 'checked' if cfg.meat == key }}>{{ m.label }}</span><span class="price">{{ m.price_per_kg }} ₽/кг</span></label>
        {% endfor %}

        <div class="step">Шаг II. Оружейный калибр (вес партии)</div>
        {% for key, c in calibers.items() %}
        <label class="opt"><span><input type="radio" name="caliber" value="{{ key }}" {{ 'checked' if cfg.caliber == key }}><svg class="shell" width="{{ c.icon_w }}" height="{{ c.icon_h }}" viewBox="0 0 44 20" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="2" width="28" height="16" rx="5" fill="#6E241E" stroke="#542C1B" stroke-width="1"/><rect x="28" y="1" width="15" height="18" rx="2" fill="#D8AA63"/><line x1="31" y1="1" x2="31" y2="19" stroke="#9C7A45" stroke-width="1.5"/></svg>{{ c.label }}</span><span class="price">{{ c.weight_g }} г</span></label>
        {% endfor %}

        <div class="step">Шаг III. Технология созревания</div>
        {% for key, t in techs.items() %}
        <label class="opt"><span><input type="radio" name="tech" value="{{ key }}" {{ 'checked' if cfg.tech == key }}>{{ t.label }}</span><span class="price">{{ t.base_days }} сут / +{{ t.markup_per_kg }} ₽/кг</span></label>
        {% endfor %}

        <div class="step">Шаг IV. Регулировка состава</div>
        <div class="range-row">
          <div class="range-head"><span>Содержание шпика</span><output id="fat_out">{{ cfg.fat }} %</output></div>
          <input type="range" id="fat" min="0" max="30" step="1" value="{{ cfg.fat }}">
        </div>
        <div class="range-row">
          <div class="range-head"><span>Интенсивность перца и пряностей</span><output id="spice_out">{{ '%.1f' % cfg.spice }} %</output></div>
          <input type="range" id="spice" min="0.5" max="3.0" step="0.1" value="{{ cfg.spice }}">
        </div>

        <div class="step">Шаг V. Крафтовые добавки</div>
        {% for key, a in addons.items() %}
        <label class="opt"><span><input type="checkbox" id="{{ key }}" {{ 'checked' if cfg[key] }}>{{ a.label }}</span><span class="price" id="price_{{ key }}">+{{ q.addon_prices_fmt[key] }}</span></label>
        {% endfor %}

        <div class="step">Шаг VI. Подарочная упаковка</div>
        <label class="opt"><span><input type="checkbox" id="tube" {{ 'checked' if cfg.tube }}>Подарочный тубус "Патрон 12 калибра" из мореного дуба с латунным донцем</span><span class="price">+{{ tube_price }} ₽</span></label>
      </div>
    </div>

    <div>
      <div class="card">
        <h2>Срез партии</h2>
        <div class="slice-box"><img id="slice" src="/api/slice.png?{{ slice_qs }}" alt="Срез батона"></div>
        <img id="tube_img" class="tube-thumb hidden" alt="Подарочный тубус">
        <div id="tube_stub" class="tube-stub hidden">Простите, но эту колбасу мы съели сразу, не успев сфотографировать.</div>
        <div class="summary" id="summary">{{ q.summary }}</div>
      </div>

      <div class="card">
        <h2>Срок созревания и выдачи</h2>
        <table>
          <tr><td class="lbl">Базовый срок технологии</td><td class="num" id="base_days">{{ q.base_days }} сут.</td></tr>
          <tr><td class="lbl">Поправка на калибр</td><td class="num" id="bonus_days">+{{ q.bonus_days }} сут.</td></tr>
        </table>
        <div style="margin-top:12px">Длительность созревания: <span class="days-big" id="days">{{ q.days }} суток</span></div>
        <div class="muted" style="margin-top:6px">Ориентировочная дата готовности партии: <span id="ready_date">{{ q.ready_date }}</span></div>
      </div>

      <div class="card">
        <h2>Технологическая карта</h2>
        <table>
          <tbody id="grams_body">
            {% for row in q.grams %}
            <tr><td class="lbl">{{ row.label }}</td><td class="num">{{ row.g }}</td></tr>
            {% endfor %}
          </tbody>
          <tr><td class="lbl"><strong>Итого масса партии (база + добавки)</strong></td><td class="num" id="weight_total"><strong>{{ q.total_fmt }}</strong></td></tr>
        </table>
      </div>

      <div class="card">
        <h2>Состав корзины</h2>
        <table>
          <tr><td class="lbl">База мяса</td><td class="num" id="p_meat">{{ q.price.meat_fmt }}</td></tr>
          <tr><td class="lbl">Наценка технологии</td><td class="num" id="p_tech">{{ q.price.tech_fmt }}</td></tr>
          <tr><td class="lbl">Добавки</td><td class="num" id="p_addons">{{ q.price.addons_fmt }}</td></tr>
          <tr><td class="lbl">Упаковка</td><td class="num" id="p_pack">{{ q.price.pack_fmt }}</td></tr>
        </table>
        <div style="margin:14px 0 10px">Итоговая стоимость: <span class="total-big" id="total">{{ q.price.total_fmt }}</span></div>
        <div style="margin-top:14px"><button type="button" class="btn-brass" id="send">Передать рецепт мастеру</button></div>
        <div id="order_status" class="muted" style="margin-top:10px"></div>
        <div id="new_batch_wrap" class="hidden" style="margin-top:12px">
          <button type="button" class="btn-ghost" id="new_batch">Новая партия (сброс конфигурации)</button>
        </div>
      </div>
    </div>
  </div>

  <footer>ВИНЧЕСТЕРЪ | Оружейная колбасная гильдия | Камера созревания #048 | {{ build }}</footer>
</div>

<div id="overlay" class="overlay hidden">
  <div class="card modal">
    <h2>Заявка мастеру</h2>
    <div class="muted" id="modal_summary"></div>
    <label for="f_name">Имя</label><input id="f_name" type="text" placeholder="Сергей">
    <label for="f_phone">Телефон</label><input id="f_phone" type="tel" placeholder="+7 900 000-00-00">
    <label for="f_email">Почта</label><input id="f_email" type="email" placeholder="name@domain.ru">
    <div id="modal_errors" class="verify-bad hidden" style="margin-top:12px"></div>
    <div class="row">
      <button type="button" class="btn-brass" id="modal_send">Оставить заявку</button>
      <button type="button" class="btn-ghost" id="modal_cancel">Отмена</button>
    </div>
  </div>
</div>

<div id="toast" class="toast hidden"></div>

<script>
var STATE = {{ state_json | safe }};
var DEFAULT_STATE = {{ default_json | safe }};
var LAST_Q = {{ quote_json | safe }};
var FLAG_KEYS = ["pistachio", "truffle", "cheese", "tomato", "tube"];
var SET_KEY = null;   // активный эталон набора; null = показываем срез
var overlay = document.getElementById("overlay");

function radioValue(name) {
  var el = document.querySelector('input[name="' + name + '"]:checked');
  return el ? el.value : null;
}
function readForm() {
  STATE.meat = radioValue("meat") || STATE.meat;
  STATE.caliber = radioValue("caliber") || STATE.caliber;
  STATE.tech = radioValue("tech") || STATE.tech;
  STATE.fat = parseInt(document.getElementById("fat").value, 10);
  STATE.spice = parseFloat(document.getElementById("spice").value);
  FLAG_KEYS.forEach(function (k) { STATE[k] = document.getElementById(k).checked; });
}
function paintSliders() {
  document.getElementById("fat_out").textContent = document.getElementById("fat").value + " %";
  document.getElementById("spice_out").textContent = Number(document.getElementById("spice").value).toFixed(1) + " %";
}
function syncForm() {
  ["meat", "caliber", "tech"].forEach(function (n) {
    var el = document.querySelector('input[name="' + n + '"][value="' + STATE[n] + '"]');
    if (el) el.checked = true;
  });
  document.getElementById("fat").value = STATE.fat;
  document.getElementById("spice").value = STATE.spice;
  FLAG_KEYS.forEach(function (k) { document.getElementById(k).checked = !!STATE[k]; });
  paintSliders();
}
function setLocked(locked) {
  document.querySelectorAll("#cfg input").forEach(function (el) { el.disabled = locked; });
  document.querySelectorAll(".preset").forEach(function (el) { el.disabled = locked; });
  document.getElementById("send").disabled = locked;
}
function buildQS() {
  var p = new URLSearchParams();
  p.set("meat", STATE.meat); p.set("caliber", STATE.caliber); p.set("tech", STATE.tech);
  p.set("fat", STATE.fat); p.set("spice", STATE.spice);
  FLAG_KEYS.forEach(function (k) { p.set(k, STATE[k] ? "1" : "0"); });
  return p.toString();
}
function applyQuote(q) {
  LAST_Q = q;
  document.getElementById("summary").textContent = q.summary;
  document.getElementById("base_days").textContent = q.base_days + " сут.";
  document.getElementById("bonus_days").textContent = "+" + q.bonus_days + " сут.";
  document.getElementById("days").textContent = q.days + " суток";
  document.getElementById("ready_date").textContent = q.ready_date;
  document.getElementById("grams_body").innerHTML = q.grams.map(function (r) {
    return "<tr><td class='lbl'>" + r.label + "</td><td class='num'>" + r.g + "</td></tr>";
  }).join("");
  document.getElementById("weight_total").innerHTML = "<strong>" + q.total_fmt + "</strong>";
  for (var k in q.addon_prices_fmt) {
    var el = document.getElementById("price_" + k);
    if (el) el.textContent = "+" + q.addon_prices_fmt[k];
  }
  document.getElementById("p_meat").textContent = q.price.meat_fmt;
  document.getElementById("p_tech").textContent = q.price.tech_fmt;
  document.getElementById("p_addons").textContent = q.price.addons_fmt;
  document.getElementById("p_pack").textContent = q.price.pack_fmt;
  document.getElementById("total").textContent = q.price.total_fmt;
}
// Гильза: фото есть -> миниатюра; файла нет -> юмористическая заглушка (СА #7).
function updateTube() {
  var img = document.getElementById("tube_img"), stub = document.getElementById("tube_stub");
  if (!STATE.tube) { img.className = "tube-thumb hidden"; stub.className = "tube-stub hidden"; return; }
  img.className = "tube-thumb"; stub.className = "tube-stub hidden";
  if (img.dataset.loaded !== "1") { img.src = "/tube.png"; img.dataset.loaded = "1"; }
}
// Эталон набора (ред. 9): единственная пара функций, без обращений к удаленным узлам.
function showSet(key, label) { SET_KEY = key; }
function hideSet() { SET_KEY = null; }
function refresh() {
  updateTube();
  var img = document.getElementById("slice");
  if (SET_KEY) {
    img.src = "/set/" + SET_KEY + ".png";
    img.alt = "Эталон гастрономического набора";
  } else {
    img.src = "/api/slice.png?" + buildQS() + "&v=" + Date.now();
    img.alt = "Срез батона";
  }
  var st = document.getElementById("order_status");
  st.textContent = ""; st.className = "muted";
  fetch("/api/quote?" + buildQS()).then(function (r) { return r.json(); }).then(applyQuote);
}
function showToast(msg) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast";
  clearTimeout(t._h);
  t._h = setTimeout(function () { t.className = "toast hidden"; }, 6000);
}
document.addEventListener("change", function (e) {
  if (e.target.matches && e.target.matches("#cfg input")) { hideSet(); readForm(); refresh(); }
});
["fat", "spice"].forEach(function (id) {
  document.getElementById(id).addEventListener("input", paintSliders);
});
document.querySelectorAll(".preset").forEach(function (btn) {
  btn.addEventListener("click", function () {
    if (btn.disabled) { return; }   // форма заблокирована после заявки -> сначала "Новая партия"
    var patch = JSON.parse(btn.getAttribute("data-patch"));
    ["pistachio", "truffle", "cheese", "tomato"].forEach(function (k) { STATE[k] = false; });
    Object.assign(STATE, patch);
    syncForm();
    showSet(btn.getAttribute("data-key"), btn.textContent);
    refresh();
  });
});
function openModal() {
  document.getElementById("modal_summary").textContent =
    LAST_Q.summary + " | Итого: " + LAST_Q.price.total_fmt;
  document.getElementById("modal_errors").className = "verify-bad hidden";
  overlay.className = "overlay";
}
function closeModal() { overlay.className = "overlay hidden"; }
document.getElementById("send").addEventListener("click", openModal);
document.getElementById("modal_cancel").addEventListener("click", closeModal);
overlay.addEventListener("click", function (e) { if (e.target === overlay) closeModal(); });
document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeModal(); });
document.getElementById("modal_send").addEventListener("click", function () {
  var body = JSON.parse(JSON.stringify(STATE));
  body.name = document.getElementById("f_name").value;
  body.phone = document.getElementById("f_phone").value;
  body.email = document.getElementById("f_email").value;
  fetch("/api/order", {method: "POST", headers: {"Content-Type": "application/json"},
                       body: JSON.stringify(body)})
    .then(function (r) { return r.json(); })
    .then(function (res) {
      if (res.ok) {
        closeModal();
        setLocked(true);
        showToast("Благодарим за заказ! В ближайшее время мастер свяжется с Вами.");
        var st = document.getElementById("order_status");
        st.textContent = "Заявка принята. Партия " + res.order_no +
          " зафиксирована. Мастер свяжется с вами.";
        st.className = "verify-ok";
        document.getElementById("new_batch_wrap").className = "";
      } else {
        var box = document.getElementById("modal_errors");
        box.className = "verify-bad";
        box.innerHTML = Object.keys(res.errors).map(function (k) {
          return "<div>" + res.errors[k] + "</div>"; }).join("");
      }
    });
});
document.getElementById("new_batch").addEventListener("click", function () {
  STATE = JSON.parse(JSON.stringify(DEFAULT_STATE));
  syncForm();
  setLocked(false);
  hideSet();
  document.getElementById("new_batch_wrap").className = "hidden";
  refresh();
});
(function () {
  var b = document.getElementById("banner");
  b.addEventListener("error", function () {
    if (!b.dataset.fallback) { b.dataset.fallback = "1"; b.src = "{{ banner_url }}"; }
    else { b.style.display = "none"; }
  });
  var img = document.getElementById("tube_img"), stub = document.getElementById("tube_stub");
  img.addEventListener("error", function () {
    img.className = "tube-thumb hidden"; stub.className = "tube-stub";
  });
})();
paintSliders();
updateTube();
</script>
</body>
</html>
"""


# ============================================================================
# СЛОЙ 5. АВТО-QA И ТОЧКА ВХОДА
# ============================================================================

def run_self_tests():
    """АВТО-QA ПРИ СТАРТЕ: эталонные кейсы по истории багов СА."""
    print("-" * 64)
    fails = []

    def check(name, cond, detail=""):
        print(f"[QA] {'OK  ' if cond else 'FAIL'} | {name}"
              + (f" | факт: {detail}" if (detail and not cond) else ""))
        if not cond:
            fails.append(name)

    q = build_quote(Config(meat="turkey", caliber="410", tech="halfsmoked"))
    check("наценка технологии пропорциональна массе (0.5 кг * 150 = 75)",
          q["price"]["tech"] == 75, q["price"]["tech"])
    check("добавки в целых рублях (250 * 0.5 / 1.2 -> 104)",
          addon_price("pistachio", 0.5) == 104, addon_price("pistachio", 0.5))
    q2 = build_quote(Config(caliber="12", tomato=True))
    base = sum(float(r["g"].replace(" г", "").replace(" ", "")) for r in q2["grams"][:4])
    check("техкарта: база компонентов = масса калибра (3000)", abs(base - 3000) <= 0.5, base)
    check("техкарта: добавки сверху (3000 + 120 = 3120)",
          q2["total_fmt"].replace(" г", "").replace(" ", "") == "3120.0", q2["total_fmt"])
    check("сроки: сыровяленая + 12 калибр = 55 суток",
          build_quote(Config(caliber="12"))["days"] == 55)
    check("сроки: дата готовности в формате ДД.ММ.ГГГГ",
          bool(re.match(r"\d{2}\.\d{2}\.\d{4}$", q2["ready_date"])), q2["ready_date"])
    check("проверка сметы: расхождение 0.00", q2["verify"]["ok"] and q2["verify"]["diff"] == 0)
    bad = parse_config({"fat": "99", "spice": "abc", "meat": "xenon"})
    check("защита от дурака: клампы и дефолты (fat=99->30, мусор->дефолт)",
          bad.fat == 30 and bad.spice == DEFAULT.spice and bad.meat == DEFAULT.meat)
    _, _, _, errs = _validate_contact({"name": "С", "phone": "12", "email": "foo"})
    check("валидация контактов: мусор отклонен по всем трем полям", len(errs) == 3, len(errs))
    try:
        png = render_slice(DEFAULT)
        ok_sig = png[:8] == b"\x89PNG\r\n\x1a\n" or png[:2] == b"\xff\xd8"
        check("срез: байты изображения (нейрофото или заглушка)", ok_sig, png[:4].hex())
        check("заглушка: PNG-сигнатура", _stub_png()[:8] == b"\x89PNG\r\n\x1a\n")
    except Exception as exc:
        check("срез: подстановка/заглушка", False, repr(exc))
    print("-" * 64)
    found = sum(1 for p in list(PHOTOS.values()) + list(SETS.values()) + ["pack_tube.png"]
                if _resolve_asset(p))
    print(f"[QA] INFO | нейрофото найдено: {found}/17")
    print(f"[QA] INFO | папка проекта: {BASE_DIR}")
    print(f"[QA] INFO | файлы картинок в папке: "
          f"{[n for n in os.listdir(BASE_DIR) if n.lower().endswith(('.png', '.jpg'))]}")
    if fails:
        print(f"[QA] ВНИМАНИЕ: провалено тестов: {len(fails)} -> {', '.join(fails)}")
    else:
        print("[QA] ВСЕ ТЕСТЫ ЗЕЛЕНЫЕ: бизнес-правила подтверждены")


def open_browser():
    webbrowser.open(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    print("=" * 64)
    print("ВИНЧЕСТЕРЪ | Оружейная колбасная гильдия")
    print(f"Сборка:  {BUILD}")
    run_self_tests()
    print(f"Сервис:  http://127.0.0.1:{PORT}")
    print(f"Журнал:  {ORDERS_FILE}")
    print("=" * 64)
    Timer(1.5, open_browser).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)