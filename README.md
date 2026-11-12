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

## Estrutura

```text
IOT/
├─ backend.py            back-end: MQTT → SQLite, API, dashboard, Telegram
├─ gateway.py            ponte Arduino (USB/serial) ⇄ MQTT
├─ config.py             lê o .env e cria o cliente MQTT (usado pelos dois acima)
├─ rodar_teste.bat       sobe backend + gateway e abre o dashboard (Windows)
├─ arduino/monitor_sala/ código do Arduino (.ino)
├─ dashboard/            página web (index.html)
├─ ferramentas/          broker MQTT local (testes offline) e gerador de dados ilustrativos
├─ docs/                 relatório técnico e slides da banca
├─ .env.example          modelo de configuração → copie para .env (este não vai pro git)
└─ requirements.txt
```

## Rodar em outra máquina — checklist rápido

O Arduino já gravado leva o programa junto; só a parte do PC precisa ser preparada.

1. **Python 3.10+** (testado no 3.14) com o launcher `py` no Windows. Confira: `py --version`.
2. **Copie a pasta do projeto** (ou `git clone`).
3. **Copie o `.env` à mão** (pendrive, mensagem para você mesmo...). Ele **não vai pelo git** porque tem as senhas.
   Sem ele, copie `.env.example` para `.env` e preencha MQTT e Telegram (seções 4 e 5).
4. **Instale as dependências:** `py -m pip install --user -r requirements.txt`
5. **Ligue o Arduino na USB** e descubra a porta: `py -m serial.tools.list_ports -v`.
   Se não for `COM3`, ajuste `SERIAL_PORT` no `.env`. Feche o Monitor Serial da Arduino IDE.
6. **Confira o broker:** no console do EMQX (<https://cloud-intl.emqx.com/console/>) o deployment tem que estar
   **Running**. Se estiver *Stopped*, clique nele → **Start** (no assistente que aparecer, pode clicar em *Skip*).
7. **Rode** em dois terminais, nesta ordem:
   ```bash
   py backend.py     # espere aparecer "[mqtt] conectado ao broker"
   py gateway.py     # espere "[serial] conectado em COMx" e "[telemetria] {...}"
   ```
8. **Abra** <http://localhost:5000>. Broker e gateway devem aparecer em verde.
9. *(Opcional)* Histórico de exemplo para a apresentação: `py ferramentas/gerar_dados_demo.py`
   (some com `py ferramentas/gerar_dados_demo.py --apagar`). O banco `telemetria.db` é criado sozinho e não vai pelo git.

**Se der errado:**

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Broker "desconectado", gateway sem mensagem `[mqtt] conectado` | Deployment do EMQX parado ou rede bloqueando a 8883 | Passo 6; se continuar, `MQTT_PORT=8084` e `MQTT_WEBSOCKET=1` no `.env`; em último caso o broker local (seção 4) |
| `could not open port 'COM3'` | Porta errada ou Monitor Serial aberto | Passo 5 |
| Bot do Telegram não responde ou dá `409 Conflict` | Tem **outro** `backend.py` rodando com o mesmo token (outro terminal ou outro PC) | Deixe só um backend aberto |
| Telegram demora ~20 s | IPv6 sem rota na rede | Já tratado em `config.py` (força IPv4); só reaparece se essa linha for removida |
| `ModuleNotFoundError` | Dependências não instaladas | Passo 4 |
| Temperatura salta / "leituras filtradas" sobe sem parar | Mau contato nos fios do DHT11 | Revise as ligações da seção 1 |

## 1. Hardware (~R$ 15–25)

| Peça | Ligação no Uno |
|---|---|
| DHT11 (módulo de 3 pinos) + jumpers macho-fêmea | VCC (+) → 5V, GND (−) → GND, DATA (S) → pino 2 |
| "Ventilador" | LED externo + resistor 220 Ω: pino 12 → resistor → LED (perna longa) → GND |
| Buzzer **ativo** (opcional) | + → pino 9, − → GND |

> O LED que vem no módulo do DHT11 só indica que ele está ligado; não dá para controlar.

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

**Alternativa offline:** `py -m pip install --user amqtt` e `py ferramentas/broker_local.py`; no `.env` use
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

O dashboard atualiza sozinho a cada 10 s, tem tema claro/escuro (🌙), mostra quanto tempo o ventilador
ficou ligado nas últimas 24 h e exporta o período selecionado em **Excel (.xlsx)**.

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
- Acima do limite **com o ventilador desligado** (modo manual): lembrete a cada `LEMBRETE_VENT_SEG` s (padrão 10).

## Comandos pelo Telegram

Só o chat de `TELEGRAM_CHAT_ID` é atendido. Digite `/` no bot para ver o menu.

| Comando | Efeito |
|---|---|
| `/vent_1` | liga o ventilador (modo manual) |
| `/vent_0` | desliga o ventilador (modo manual) |
| `/auto` | volta ao modo automático |
| `/limite 28.5` | define o limite de alerta em °C |
| `/status` | temperatura, umidade, ventilador e gateway agora |

## Roteiro da demo ao vivo

1. Dashboard aberto, temperatura ambiente estável.
2. Segure o sensor na mão (ou use um secador de longe) → a temperatura sobe no gráfico.
3. Passou do limite: LED e buzzer ligam sozinhos (borda) e chega mensagem no Telegram (nuvem).
4. Clique em "Desligar ventilador" no dashboard (ou mande `/vent_0` no Telegram) → o LED apaga (comando nuvem → Arduino)
   e o bot passa a insistir a cada 10 s enquanto a sala estiver acima do limite.
5. Puxe o cabo USB → em até 60 s chega o alerta "sem telemetria".
6. Dica: abaixe o limite para ~2 °C acima da temperatura da sala antes da demo, para o alerta vir rápido.

## Modelo de dados (SQLite — `telemetria.db`)

- `leituras`: ts, temperatura, umidade, ventilador, modo_auto, alerta, limite, descartadas, latencia_ms,
  ilustrativo (1 = simulado por `ferramentas/gerar_dados_demo.py`: cinza no gráfico, marcado no Excel, fora das métricas de SLA)
- `eventos`: ts, tipo (alerta, normalizado, comando, offline, online, sem_dados, dados), mensagem

## Modelos de nuvem (IaaS / PaaS / SaaS)

| Componente | Hoje | Em produção | Plano gratuito / limites |
|---|---|---|---|
| Broker MQTT (EMQX Cloud Serverless) | PaaS | PaaS | 1 mi minutos de sessão, 1 GB de tráfego e 1 mi ações de regra por mês; até 1.000 conexões. Com *spend limit* 0 nunca cobra |
| Notificações (Telegram Bot API) | SaaS | SaaS | Grátis; ~1 msg/s por conversa e ~30 msg/s no total |
| Back-end Flask + dashboard | Local | PaaS (Render, Railway, App Service) ou IaaS (VM) | Open source |
| Banco (SQLite) | Local | Banco gerenciado (PaaS) com backup | Open source, sem replicação |
| Gateway Python | Local (borda) | Substituído por ESP32 com Wi-Fi | Open source |

Gateway + back-end = 2 clientes ligados o mês todo ≈ 86.400 minutos de sessão (8,6 % da cota gratuita).
Fontes: [EMQX pricing](https://docs.emqx.com/en/cloud/latest/price/pricing.html), [Telegram Bots FAQ](https://core.telegram.org/bots/faq).

## SLA proposto (cenário hipotético)

| Métrica | Meta | Como medir | Medido no protótipo (12/10/2026) |
|---|---|---|---|
| Disponibilidade (telemetria chegando, sem buraco > 60 s) | ≥ 99,5 %/mês (≈ 3 h 39 min fora) | `leituras` + eventos `sem_dados` | sem quedas no teste |
| Latência gateway → back-end | p95 ≤ 1 s | coluna `latencia_ms` | mediana 153 ms, p95 223 ms |
| Temperatura passou do limite → mensagem no Telegram | ≤ 60 s em 99 % | evento `alerta` + hora no Telegram | 1–2 s após baixar o limite |
| Queda do gateway/sensor → aviso | ≤ 90 s | eventos `offline` / `sem_dados` | não medido (Last Will ≈ 45 s, sem dados 60 s) |
| Comando do dashboard → `ack` | ≤ 5 s em 99 % | evento `comando` + `ack` | < 2 s, sem medição formal |
| Recuperação (RTO) | automática ≤ 1 min; manual ≤ 4 h | `offline` → `online` | MQTT reconecta em 1–30 s, serial a cada 3 s |
| Perda de dados (RPO) | ≤ 24 h (backup diário) | data do último backup | sem backup hoje |

Disponibilidade composta = produto das partes: EMQX publica 99,9 %; com back-end em PaaS a 99,9 %, sobram ≈ 0,3 p.p. para a borda — o gateway no PC é o elo fraco.
Relatório técnico completo (contextualização, arquitetura, nuvem, SLA, considerações finais) é entregue à parte.
