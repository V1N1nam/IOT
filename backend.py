"""Back-end: assina a telemetria, grava no SQLite, envia alertas ao Telegram,
serve o dashboard e publica comandos para o Arduino.

Uso:  py backend.py   ->   abra http://localhost:5000
"""
import json
import os
import sqlite3
import threading
import time

import requests
from flask import Flask, jsonify, request, send_from_directory

import config

ARQUIVO_DB = os.path.join(config.PASTA, "telemetria.db")

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
                latencia_ms  REAL                -- gateway -> back-end
            );
            CREATE INDEX IF NOT EXISTS idx_leituras_ts ON leituras(ts);

            CREATE TABLE IF NOT EXISTS eventos (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        REAL NOT NULL,
                tipo      TEXT NOT NULL,         -- alerta, normalizado, comando, offline...
                mensagem  TEXT NOT NULL
            );
        """)


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
    """Avisa se a telemetria parar de chegar (Arduino desligado, cabo solto, gateway travado)."""
    while True:
        time.sleep(5)
        with lock:
            atrasado = (estado["ultima_ts"] and not estado["sem_dados"]
                        and time.time() - estado["ultima_ts"] > config.TIMEOUT_SEM_DADOS)
            if atrasado:
                estado["sem_dados"] = True
        if atrasado:
            registrar_evento("sem_dados", f"⚠️ Sem telemetria da sala há mais de "
                                          f"{config.TIMEOUT_SEM_DADOS} s", notificar=True)


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
                                FROM leituras WHERE ts >= ?""", (desde_24h,)).fetchone()
        alertas = conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo='alerta' AND ts >= ?",
                               (desde_24h,)).fetchone()[0]
    with lock:
        return jsonify({
            "ultima": estado["ultima"],
            "ultima_ts": estado["ultima_ts"],
            "gateway": estado["gateway"],
            "sem_dados": estado["sem_dados"],
            "broker_conectado": cliente.is_connected(),
            "stats_24h": {**dict(stats), "alertas": alertas},
        })


@app.get("/api/leituras")
def api_leituras():
    minutos = min(int(request.args.get("minutos", 30)), 1440)
    with conectar_db() as conn:
        linhas = conn.execute("""SELECT ts, temperatura, umidade, ventilador, limite FROM leituras
                                 WHERE ts >= ? ORDER BY ts""",
                              (time.time() - minutos * 60,)).fetchall()
    return jsonify([dict(l) for l in linhas])


@app.get("/api/eventos")
def api_eventos():
    with conectar_db() as conn:
        linhas = conn.execute("SELECT ts, tipo, mensagem FROM eventos ORDER BY id DESC LIMIT 15").fetchall()
    return jsonify([dict(l) for l in linhas])


@app.post("/api/comando")
def api_comando():
    cmd = str((request.get_json(silent=True) or {}).get("comando", "")).strip().upper()
    if not config.COMANDO_VALIDO.fullmatch(cmd):
        return jsonify({"ok": False, "erro": "comando inválido"}), 400
    if not cliente.is_connected():
        return jsonify({"ok": False, "erro": "back-end sem conexão com o broker"}), 503
    cliente.publish(config.TOPICO_COMANDO, cmd, qos=1)
    registrar_evento("comando", f"Comando enviado pelo dashboard: {cmd}")
    return jsonify({"ok": True})


if __name__ == "__main__":
    criar_tabelas()
    cliente.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
    cliente.loop_start()
    threading.Thread(target=vigiar_dados, daemon=True).start()
    print("Dashboard em http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)
