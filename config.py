"""Configuração compartilhada entre gateway e back-end (lida do arquivo .env)."""
import os
import re
import ssl

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

PASTA = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PASTA, ".env"))

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")
MQTT_TLS = os.getenv("MQTT_TLS", "0") == "1"
MQTT_WEBSOCKET = os.getenv("MQTT_WEBSOCKET", "0") == "1"  # para redes que bloqueiam a porta 8883

SERIAL_PORT = os.getenv("SERIAL_PORT", "COM3")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TIMEOUT_SEM_DADOS = int(os.getenv("TIMEOUT_SEM_DADOS", "60"))

TOPICO_TELEMETRIA = "sala/telemetria"   # Edge -> Cloud
TOPICO_COMANDO = "sala/comando"         # Cloud -> Edge
TOPICO_STATUS = "sala/status"           # online/offline do gateway (retido + LWT)

# Comandos que o Arduino entende; qualquer outra coisa é bloqueada antes de chegar nele
COMANDO_VALIDO = re.compile(r"VENT:[01]|AUTO|LIMITE:\d{1,2}(\.\d)?")


def criar_cliente_mqtt(client_id: str) -> mqtt.Client:
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                          transport="websockets" if MQTT_WEBSOCKET else "tcp")
    if MQTT_WEBSOCKET:
        cliente.ws_set_options(path="/mqtt")
    if MQTT_USER:
        cliente.username_pw_set(MQTT_USER, MQTT_PASS)
    if MQTT_TLS:
        cliente.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)
    return cliente
