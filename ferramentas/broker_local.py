"""Broker MQTT local em Python (amqtt) - alternativa ao Mosquitto que não exige admin.

Só aceita conexões desta máquina e não tem senha: use para testar offline.
Para a apresentação, prefira o EMQX Cloud (TLS + usuário/senha).

Uso:  py -m pip install --user amqtt   e depois   py ferramentas/broker_local.py
"""
import os
import sys

from amqtt.scripts.broker_script import app

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker_local.yaml")

if __name__ == "__main__":
    sys.argv = ["amqtt", "-c", CONFIG]
    app()
