"""Back-end: assina a telemetria, grava no SQLite, envia alertas ao Telegram,
serve o dashboard e publica comandos para o Arduino.

Uso:  py backend.py   ->   abra http://localhost:5000
"""
import io
import json
import os
import sqlite3
import threading
import time
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from openpyxl import Workbook
from openpyxl.styles import Font

import config

ARQUIVO_DB = os.path.join(config.PASTA, "dados", "telemetria.db")
os.makedirs(os.path.dirname(ARQUIVO_DB), exist_ok=True)

estado = {
    "ultima": None,           # última leitura recebida
    "ultima_ts": 0.0,         # quando ela chegou
    "gateway": "desconhecido",
    "sem_dados": False,
    "alerta": False,
}
lock = threading.Lock()


# ---------- Banco de dados ----------

def conectar_db():
    conn = sqlite3.connect(ARQUIVO_DB)
    conn.row_factory = sqlite3.Row
    return conn


def criar_tabelas():
    with conectar_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS leituras (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,   -- epoch em segundos
                temperatura  REAL    NOT NULL,
                umidade      REAL    NOT NULL,
                ventilador   INTEGER NOT NULL,
                modo_auto    INTEGER NOT NULL,
                alerta       INTEGER NOT NULL,
                limite       REAL    NOT NULL,
                descartadas  INTEGER NOT NULL,   -- leituras filtradas na borda (acumulado)
                latencia_ms  REAL,               -- gateway -> back-end
                ilustrativo  INTEGER NOT NULL DEFAULT 0  -- 1 = dado simulado (ferramentas/gerar_dados_demo.py)
            );
            CREATE INDEX IF NOT EXISTS idx_leituras_ts ON leituras(ts);

            CREATE TABLE IF NOT EXISTS eventos (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        REAL NOT NULL,
                tipo      TEXT NOT NULL,         -- alerta, normalizado, comando, offline...
                mensagem  TEXT NOT NULL
            );
        """)
        # bancos criados antes da coluna ilustrativo
        if "ilustrativo" not in [c[1] for c in conn.execute("PRAGMA table_info(leituras)")]:
            conn.execute("ALTER TABLE leituras ADD COLUMN ilustrativo INTEGER NOT NULL DEFAULT 0")


def registrar_evento(tipo, mensagem, notificar=False):
    with conectar_db() as conn:
        conn.execute("INSERT INTO eventos (ts, tipo, mensagem) VALUES (?, ?, ?)",
                     (time.time(), tipo, mensagem))
    print(f"[evento] {tipo}: {mensagem}")
    if notificar:
        threading.Thread(target=enviar_telegram, args=(mensagem,), daemon=True).start()


# ---------- Telegram ----------

def enviar_telegram(texto):
    if not (config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto}, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[telegram] falha ao enviar: {e}")


AJUDA_TELEGRAM = ("Comandos:\n/vent_1 - liga o ventilador\n/vent_0 - desliga o ventilador\n"
                  "/auto - volta ao modo automático\n/limite 28.5 - define o limite em °C\n/status - leitura atual")


def tratar_mensagem_telegram(texto):
    partes = texto.strip().split()
    if not partes:
        return
    cmd = partes[0].split("@")[0].lower()
    arg = partes[1].replace(",", ".") if len(partes) > 1 else ""
    if cmd == "/status":
        with lock:
            u, gateway = estado["ultima"], estado["gateway"]
        if not u:
            enviar_telegram(f"Sem leituras ainda (gateway {gateway}).")
            return
        enviar_telegram(f"🌡️ {u['t']:.1f} °C · 💧 {u['u']:.0f} %\nVentilador {'LIGADO' if u['vent'] else 'desligado'} "
                        f"({'automático' if u['auto'] else 'manual'}) · limite {u['limite']:.1f} °C\n"
                        f"Gateway {gateway}{' · ALERTA' if u['alerta'] else ''}")
        return
    comando = {"/vent": f"VENT:{arg}", "/vent_1": "VENT:1", "/vent_0": "VENT:0", "/auto": "AUTO", "/limite": f"LIMITE:{arg}"}.get(cmd)
    if not comando:
        enviar_telegram(AJUDA_TELEGRAM)
        return
    erro = enviar_comando(comando, "Telegram")
    enviar_telegram(f"✗ {erro}" if erro else f"✓ {comando} enviado. O dashboard atualiza na próxima leitura.")


def ouvir_telegram():
    """Long polling do bot: recebe comandos só do chat configurado."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
    offset = None
    try:
        requests.post(f"{url}/setMyCommands", timeout=10, json={"commands": [
            {"command": "vent_1", "description": "liga o ventilador"},
            {"command": "vent_0", "description": "desliga o ventilador"},
            {"command": "auto", "description": "modo automático"},
            {"command": "limite", "description": "define o limite em °C (ex: /limite 28.5)"},
            {"command": "status", "description": "leitura atual"}]})
        # ignora mensagens antigas acumuladas enquanto o back-end estava desligado
        pendentes = requests.get(f"{url}/getUpdates", params={"offset": -1}, timeout=10).json()["result"]
        offset = pendentes[-1]["update_id"] + 1 if pendentes else None
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[telegram] falha ao iniciar comandos: {e}")
    while True:
        try:
            r = requests.get(f"{url}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40)
            r.raise_for_status()
            for upd in r.json()["result"]:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id")) == str(config.TELEGRAM_CHAT_ID):
                    tratar_mensagem_telegram(msg.get("text", ""))
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"[telegram] falha ao receber: {e}")
            time.sleep(5)


# ---------- MQTT ----------

def ao_conectar(cli, userdata, flags, reason_code, properties):
    print(f"[mqtt] conectado ao broker ({reason_code})")
    cli.subscribe([(config.TOPICO_TELEMETRIA, 1), (config.TOPICO_STATUS, 1)])


def ao_receber(cli, userdata, msg):
    if msg.topic == config.TOPICO_STATUS:
        tratar_status(msg.payload.decode(errors="ignore"))
    elif msg.topic == config.TOPICO_TELEMETRIA:
        try:
            tratar_telemetria(json.loads(msg.payload))
        except (ValueError, KeyError) as e:
            print(f"[mqtt] telemetria inválida: {e}")


def tratar_status(status):
    with lock:
        anterior = estado["gateway"]
        estado["gateway"] = status
    if status == "offline" and anterior == "online":
        registrar_evento("offline", "⚠️ Gateway da sala ficou OFFLINE", notificar=True)
    elif status == "online" and anterior == "offline":
        registrar_evento("online", "✅ Gateway da sala voltou a ficar online", notificar=True)


def tratar_telemetria(d):
    agora = time.time()
    latencia = (agora - d["ts"]) * 1000 if "ts" in d else None
    with conectar_db() as conn:
        conn.execute("""INSERT INTO leituras (ts, temperatura, umidade, ventilador, modo_auto,
                        alerta, limite, descartadas, latencia_ms) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (agora, d["t"], d["u"], d["vent"], d["auto"], d["alerta"],
                      d["limite"], d.get("descartadas", 0), latencia))

    with lock:
        estado["ultima"] = d
        estado["ultima_ts"] = agora
        voltou = estado["sem_dados"]
        estado["sem_dados"] = False
        alerta_anterior = estado["alerta"]
        estado["alerta"] = bool(d["alerta"])

    if voltou:
        registrar_evento("dados", "✅ Telemetria da sala restabelecida", notificar=True)

    # A decisão de alerta é tomada na borda (Arduino); aqui só notificamos a transição
    if d["alerta"] and not alerta_anterior:
        registrar_evento("alerta", f"🔥 ALERTA: sala a {d['t']:.1f} °C (limite {d['limite']:.1f} °C). "
                                   f"Ventilador {'LIGADO' if d['vent'] else 'DESLIGADO'}.", notificar=True)
    elif not d["alerta"] and alerta_anterior:
        registrar_evento("normalizado", f"✅ Temperatura normalizada: {d['t']:.1f} °C", notificar=True)


def vigiar_dados():
    """Avisa se a telemetria parar de chegar (Arduino desligado, cabo solto, gateway travado)
    e insiste no Telegram enquanto a sala estiver acima do limite com o ventilador desligado."""
    ultimo_lembrete = 0.0
    while True:
        time.sleep(1)
        with lock:
            atrasado = (estado["ultima_ts"] and not estado["sem_dados"]
                        and time.time() - estado["ultima_ts"] > config.TIMEOUT_SEM_DADOS)
            if atrasado:
                estado["sem_dados"] = True
            u = estado["ultima"]
            perigo = u and not estado["sem_dados"] and u["t"] > u["limite"] and not u["vent"]
        if atrasado:
            registrar_evento("sem_dados", f"⚠️ Sem telemetria da sala há mais de "
                                          f"{config.TIMEOUT_SEM_DADOS} s", notificar=True)
        # só Telegram, sem gravar evento: senão a lista de eventos enche a cada 10 s
        if perigo and time.time() - ultimo_lembrete >= config.LEMBRETE_VENT_SEG:
            ultimo_lembrete = time.time()
            threading.Thread(target=enviar_telegram, daemon=True, args=(
                f"🚨 Sala a {u['t']:.1f} °C, acima do limite de {u['limite']:.1f} °C, e o ventilador está "
                f"DESLIGADO!\nUse /vent_1 para ligar ou /auto para o modo automático.",)).start()


cliente = config.criar_cliente_mqtt("backend-sala")
cliente.on_connect = ao_conectar
cliente.on_message = ao_receber


# ---------- API + Dashboard ----------

app = Flask(__name__)


@app.get("/")
def dashboard():
    return send_from_directory(os.path.join(config.PASTA, "dashboard"), "index.html")


@app.get("/api/estado")
def api_estado():
    desde_24h = time.time() - 86400
    with conectar_db() as conn:
        stats = conn.execute("""SELECT MIN(temperatura) tmin, MAX(temperatura) tmax,
                                       AVG(temperatura) tmed, AVG(latencia_ms) lat_media,
                                       MAX(latencia_ms) lat_max, COUNT(*) leituras
                                FROM leituras WHERE ts >= ? AND ilustrativo = 0""", (desde_24h,)).fetchone()
        alertas = conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo='alerta' AND ts >= ?",
                               (desde_24h,)).fetchone()[0]
        # soma o intervalo até a leitura seguinte enquanto o ventilador estava ligado;
        # buracos maiores que o timeout (sem dados) não contam
        vent_seg = conn.execute("""SELECT COALESCE(SUM(prox - ts), 0) FROM (
                                     SELECT ts, ventilador, LEAD(ts) OVER (ORDER BY ts) prox
                                     FROM leituras WHERE ts >= ? AND ilustrativo = 0)
                                   WHERE ventilador = 1 AND prox - ts <= ?""",
                                (desde_24h, config.TIMEOUT_SEM_DADOS)).fetchone()[0]
    with lock:
        return jsonify({
            "ultima": estado["ultima"],
            "ultima_ts": estado["ultima_ts"],
            "gateway": estado["gateway"],
            "sem_dados": estado["sem_dados"],
            "broker_conectado": cliente.is_connected(),
            "stats_24h": {**dict(stats), "alertas": alertas, "vent_seg": vent_seg},
        })


@app.get("/api/leituras")
def api_leituras():
    minutos = min(int(request.args.get("minutos", 30)), 1440)
    with conectar_db() as conn:
        linhas = conn.execute("""SELECT ts, temperatura, umidade, ventilador, limite, ilustrativo FROM leituras
                                 WHERE ts >= ? ORDER BY ts""",
                              (time.time() - minutos * 60,)).fetchall()
    return jsonify([dict(l) for l in linhas])


@app.get("/api/exportar")
def api_exportar():
    minutos = min(int(request.args.get("minutos", 30)), 1440)
    with conectar_db() as conn:
        linhas = conn.execute("""SELECT ts, temperatura, umidade, ventilador, modo_auto, alerta, limite,
                                        latencia_ms, ilustrativo FROM leituras WHERE ts >= ? ORDER BY ts""",
                              (time.time() - minutos * 60,)).fetchall()
    # .xlsx em vez de CSV: o CSV depende do separador regional de cada Excel (vírgula x ponto e vírgula)
    wb = Workbook()
    ws = wb.active
    ws.title = "Leituras"
    ws.append(["Data/hora", "Temperatura (°C)", "Umidade (%)", "Ventilador", "Modo", "Alerta",
               "Limite (°C)", "Latência (ms)", "Origem"])
    for l in linhas:
        ws.append([datetime.fromtimestamp(l["ts"]), l["temperatura"], l["umidade"],
                   "ligado" if l["ventilador"] else "desligado", "automático" if l["modo_auto"] else "manual",
                   "SIM" if l["alerta"] else "não", l["limite"],
                   None if l["latencia_ms"] is None else round(l["latencia_ms"]),
                   "ILUSTRATIVO (simulado)" if l["ilustrativo"] else "real"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, largura in zip("ABCDEFGHI", (20, 17, 13, 12, 12, 9, 12, 14, 24)):
        ws.column_dimensions[col].width = largura
    for linha in ws.iter_rows(min_row=2):
        linha[0].number_format = "dd/mm/yyyy hh:mm:ss"
        linha[1].number_format = linha[2].number_format = linha[6].number_format = "0.0"
        if linha[8].value != "real":
            for c in linha:
                c.font = Font(italic=True, color="808080")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    saida = io.BytesIO()
    wb.save(saida)
    return Response(saida.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition":
                             f"attachment; filename={time.strftime('leituras_sala_%Y%m%d_%H%M.xlsx')}"})


@app.get("/api/eventos")
def api_eventos():
    with conectar_db() as conn:
        linhas = conn.execute("SELECT ts, tipo, mensagem FROM eventos ORDER BY id DESC LIMIT 15").fetchall()
    return jsonify([dict(l) for l in linhas])


def enviar_comando(cmd, origem):
    """Publica o comando para o Arduino. Retorna a mensagem de erro, ou None se deu certo."""
    cmd = cmd.strip().upper()
    if not config.COMANDO_VALIDO.fullmatch(cmd):
        return "comando inválido"
    if not cliente.is_connected():
        return "back-end sem conexão com o broker"
    cliente.publish(config.TOPICO_COMANDO, cmd, qos=1)
    registrar_evento("comando", f"Comando enviado pelo {origem}: {cmd}")
    return None


@app.post("/api/comando")
def api_comando():
    erro = enviar_comando(str((request.get_json(silent=True) or {}).get("comando", "")), "dashboard")
    if erro:
        return jsonify({"ok": False, "erro": erro}), 400 if erro == "comando inválido" else 503
    return jsonify({"ok": True})


if __name__ == "__main__":
    criar_tabelas()
    cliente.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
    cliente.loop_start()
    threading.Thread(target=vigiar_dados, daemon=True).start()
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        threading.Thread(target=ouvir_telegram, daemon=True).start()
    print("Dashboard em http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)
