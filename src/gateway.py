"""Gateway Serial <-> MQTT.

O Arduino Uno não tem Wi-Fi, então este script roda no PC ligado a ele por USB:
  - lê o JSON que o Arduino imprime na serial e publica em sala/telemetria;
  - assina sala/comando e repassa os comandos válidos para a serial.

Uso:
  py gateway.py              # com o Arduino na porta definida em SERIAL_PORT
  py gateway.py --simular    # sem hardware (testes / plano B na apresentação)
"""
import argparse
import json
import random
import threading
import time

import serial

import config


class Arduino:
    """Conexão serial com reconexão automática (cabo solto, porta ocupada etc.)."""

    def __init__(self, porta, baud):
        self.porta, self.baud = porta, baud
        self.ser = None
        self.lock = threading.Lock()

    def _abrir(self):
        while self.ser is None:
            try:
                self.ser = serial.Serial(self.porta, self.baud, timeout=1)
                time.sleep(2)  # abrir a porta reinicia o Uno
                print(f"[serial] conectado em {self.porta}")
            except serial.SerialException as e:
                print(f"[serial] não consegui abrir {self.porta} ({e}); tentando em 3 s")
                time.sleep(3)

    def ler_linha(self):
        self._abrir()
        try:
            return self.ser.readline().decode("utf-8", errors="ignore").strip()
        except serial.SerialException:
            print("[serial] conexão perdida")
            self.ser = None
            return ""

    def escrever(self, cmd):
        with self.lock:
            if self.ser is None:
                print("[serial] comando descartado: Arduino desconectado")
                return
            self.ser.write((cmd + "\n").encode())


class Simulador:
    """Imita o firmware: temperatura sobe com o ventilador desligado e cai com ele ligado."""

    def __init__(self):
        self.t, self.u = 27.0, 55.0
        self.limite, self.auto, self.vent, self.alerta = 30.0, True, False, False
        self.pendentes = []

    def ler_linha(self):
        if self.pendentes:
            return self.pendentes.pop(0) or self._estado()  # None = estado atual após comando
        time.sleep(2)
        self.t += (-0.4 if self.vent else 0.3) + random.uniform(-0.1, 0.1)
        self.t = max(22.0, min(35.0, self.t))
        self.u = max(30.0, min(80.0, self.u + random.uniform(-1, 1)))
        if not self.alerta and self.t >= self.limite:
            self.alerta = True
        elif self.alerta and self.t < self.limite - 1:
            self.alerta = False
        if self.auto:
            self.vent = self.alerta
        return self._estado()

    def _estado(self):
        return json.dumps({"t": round(self.t, 1), "u": round(self.u, 1), "vent": int(self.vent),
                           "auto": int(self.auto), "alerta": int(self.alerta),
                           "limite": self.limite, "descartadas": 0})

    def escrever(self, cmd):
        if cmd.startswith("VENT:"):
            self.auto, self.vent = False, cmd.endswith("1")
        elif cmd == "AUTO":
            self.auto, self.vent = True, self.alerta
        elif cmd.startswith("LIMITE:"):
            self.limite = float(cmd[7:])
        self.pendentes += [json.dumps({"ack": cmd}), None]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simular", action="store_true", help="usa dados simulados em vez do Arduino")
    args = parser.parse_args()

    dispositivo = Simulador() if args.simular else Arduino(config.SERIAL_PORT, config.SERIAL_BAUD)

    cliente = config.criar_cliente_mqtt("gateway-sala")
    # Last Will: se o gateway cair sem avisar, o broker publica "offline" sozinho
    cliente.will_set(config.TOPICO_STATUS, "offline", qos=1, retain=True)

    def ao_conectar(cli, userdata, flags, reason_code, properties):
        print(f"[mqtt] conectado ao broker ({reason_code})")
        cli.subscribe(config.TOPICO_COMANDO, qos=1)
        cli.publish(config.TOPICO_STATUS, "online", qos=1, retain=True)

    def ao_receber(cli, userdata, msg):
        cmd = msg.payload.decode(errors="ignore").strip().upper()
        if config.COMANDO_VALIDO.fullmatch(cmd):
            print(f"[cmd] nuvem -> arduino: {cmd}")
            dispositivo.escrever(cmd)
        else:
            print(f"[cmd] rejeitado: {cmd!r}")

    cliente.on_connect = ao_conectar
    cliente.on_message = ao_receber
    cliente.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
    cliente.loop_start()

    try:
        while True:
            linha = dispositivo.ler_linha()
            if not linha:
                continue
            try:
                dados = json.loads(linha)
            except json.JSONDecodeError:
                print(f"[serial] ignorado: {linha}")
                continue
            if "t" not in dados:
                print(f"[arduino] {dados}")  # ack, erro, evento de inicialização
                continue
            dados["ts"] = time.time()  # usado pelo back-end para medir latência
            cliente.publish(config.TOPICO_TELEMETRIA, json.dumps(dados), qos=1)
            print(f"[telemetria] {dados}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            cliente.publish(config.TOPICO_STATUS, "offline", qos=1, retain=True).wait_for_publish(2)
        except (RuntimeError, ValueError):
            pass  # broker fora do ar: o Last Will avisa por nós
        cliente.loop_stop()
        cliente.disconnect()


if __name__ == "__main__":
    main()
