# Monitor de Sala de Servidores — IoT com Arduino Uno

Sensor DHT11 no Arduino Uno mede temperatura e umidade, filtra os dados na borda e decide o alerta.
Um gateway Python (o Uno não tem Wi-Fi) leva os dados via MQTT para o back-end, que grava no SQLite,
mostra num dashboard web e avisa no Telegram. Do dashboard dá para ligar/desligar o "ventilador" (LED).

```
[DHT11] → [Arduino Uno] ⇄ USB/serial ⇄ [gateway.py] ⇄ MQTT/TLS ⇄ [EMQX Cloud] ⇄ [backend.py] → SQLite
   LED/buzzer ←─ comandos ───────────────────────────────────────────────────────────┤        → Dashboard (Flask)
                                                                                     └──────→ Telegram
```

**Nada aqui precisa de permissão de administrador.**

## 1. Hardware (~R$ 15–25)

| Peça | Ligação no Uno |
|---|---|
| DHT11 (módulo de 3 pinos) + jumpers macho-fêmea | VCC (+) → 5V, GND (−) → GND, DATA (S) → pino 2 |
| "Ventilador" | LED da própria placa (pino 13) — nada a comprar |
| LED externo + resistor 220 Ω (opcional, mais visível na demo) | pino 13 → resistor → LED (perna longa) → GND |

> O LED que vem no módulo do DHT11 só indica que ele está ligado; não dá para controlar.
| Buzzer **ativo** (opcional) | + → pino 9, − → GND |

> Usou o DHT11 "puro" (4 pinos, sem placa)? Coloque um resistor de 10 kΩ entre DATA e 5V.

## 2. Arduino

1. Na Arduino IDE: *Ferramentas → Gerenciar Bibliotecas*, instale **DHT sensor library** (Adafruit) — aceite instalar também a **Adafruit Unified Sensor**.
2. Abra `arduino/monitor_sala/monitor_sala.ino` e envie para a placa.
3. Teste no Monitor Serial (9600 baud): em ~10 s aparece `{"t":25.3,"u":60.0,...}`. Digite `VENT:1` e o LED acende.
4. **Feche o Monitor Serial** antes de rodar o gateway (a porta COM só aceita um programa por vez).

> Sem admin no PC? A Arduino IDE tem versão *ZIP* (portátil) no site oficial, que roda sem instalar.
> Placas clones com chip CH340 podem precisar de driver — nesse caso grave o Arduino em casa.

## 3. Python

```bash
py -m pip install --user -r requirements.txt
```

## 4. Broker MQTT — EMQX Cloud Serverless (gratuito, sem instalar nada)

> O plano gratuito do HiveMQ Cloud foi descontinuado em 30/09/2026, por isso usamos o EMQX.

1. Crie conta em <https://www.emqx.com/en/cloud> → *New Deployment* → plano **Serverless**.
2. Defina o **Spend Limit = 0** (fica só na cota gratuita, nunca cobra).
3. Na deployment, em *Access Control → Authentication*, crie um usuário e senha.
4. Em *Overview*, copie o endereço (algo como `xxxx.ala.us-east-1.emqxsl.com`).
5. Copie `.env.example` para `.env` e preencha `MQTT_HOST`, `MQTT_USER`, `MQTT_PASS` (porta 8883, TLS=1).

Se a rede (ex.: da empresa/faculdade) bloquear a porta 8883, use `MQTT_PORT=8084` e `MQTT_WEBSOCKET=1`.

**Alternativa offline:** `py -m pip install --user amqtt` e `py broker_local.py`; no `.env` use
`MQTT_HOST=localhost`, `MQTT_PORT=1883`, `MQTT_TLS=0` e usuário/senha vazios (sem segurança — só para testes).

## 5. Telegram

1. No Telegram (pode ser o web.telegram.org), fale com **@BotFather** → `/newbot` → copie o token.
2. Mande qualquer mensagem para o seu bot.
3. Abra `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o número em `"chat":{"id": ...}`.
4. Preencha `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`.

## 6. Rodar

Em dois terminais, dentro desta pasta:

```bash
py backend.py               # abre o dashboard em http://localhost:5000
py gateway.py               # com o Arduino na porta SERIAL_PORT do .env
py gateway.py --simular     # sem Arduino (testes ou plano B na apresentação)
```

## Tópicos MQTT

| Tópico | Sentido | Conteúdo |
|---|---|---|
| `sala/telemetria` | Edge → Cloud | `{"t":25.3,"u":60,"vent":0,"auto":1,"alerta":0,"limite":30,"descartadas":2,"ts":...}` |
| `sala/comando` | Cloud → Edge | `VENT:1`, `VENT:0`, `AUTO`, `LIMITE:28` |
| `sala/status` | Edge → Cloud | `online` / `offline` (mensagem retida + Last Will) |

## O que roda na borda (Arduino)

- Descarta leituras inválidas (NaN, fora de 0–50 °C / 20–90 %) e picos de ruído (> 5 °C da média).
- Média móvel de 5 leituras.
- Só envia quando a temperatura varia ≥ 0,5 °C ou a umidade ≥ 2 %, ou a cada 30 s (heartbeat).
- Decide o alerta localmente, com histerese de 1 °C, e liga ventilador + buzzer **mesmo sem internet**.

## Alertas no Telegram

- Temperatura passou do limite / voltou ao normal.
- Gateway ficou offline (Last Will do MQTT) / voltou.
- Nenhuma telemetria há mais de `TIMEOUT_SEM_DADOS` segundos.

## Roteiro da demo ao vivo

1. Dashboard aberto, temperatura ambiente estável.
2. Segure o sensor na mão (ou use um secador de longe) → a temperatura sobe no gráfico.
3. Passou do limite: LED e buzzer ligam sozinhos (borda) e chega mensagem no Telegram (nuvem).
4. Clique em "Desligar ventilador" no dashboard → o LED apaga (comando nuvem → Arduino).
5. Puxe o cabo USB → em até 60 s chega o alerta "sem telemetria".
6. Dica: abaixe o limite para ~2 °C acima da temperatura da sala antes da demo, para o alerta vir rápido.

## Modelo de dados (SQLite — `telemetria.db`)

- `leituras`: ts, temperatura, umidade, ventilador, modo_auto, alerta, limite, descartadas, latencia_ms
- `eventos`: ts, tipo (alerta, normalizado, comando, offline, online, sem_dados, dados), mensagem
