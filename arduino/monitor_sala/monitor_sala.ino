/*
 * Monitor de Sala de Servidores - Arduino Uno (borda)
 *
 * Lê temperatura/umidade do DHT11, filtra os dados localmente (edge computing)
 * e envia JSON pela serial para o gateway Python, que publica no MQTT.
 * Recebe comandos pela serial (vindos do back-end via MQTT -> gateway).
 *
 * Ligações:
 *   DHT11 DATA -> pino 2   (VCC 5V, GND)
 *   LED "ventilador" -> pino 12 (LED externo + resistor de 220 ohm)
 *   Buzzer ativo -> pino 9
 *
 * Comandos aceitos (uma linha terminada em \n):
 *   VENT:1      liga o ventilador (modo manual)
 *   VENT:0      desliga o ventilador (modo manual)
 *   AUTO        volta ao modo automático (ventilador segue o alerta)
 *   LIMITE:30   define o limite de temperatura (10 a 50 °C)
 *
 * Bibliotecas: "DHT sensor library" (Adafruit) + "Adafruit Unified Sensor"
 */
#include <DHT.h>

#define PINO_DHT    2
#define TIPO_DHT    DHT11
#define PINO_VENT   12
#define PINO_BUZZER 9

const unsigned long INTERVALO_LEITURA   = 2000;   // DHT11 aceita no máximo 1 leitura/s
const unsigned long INTERVALO_MAX_ENVIO = 30000;  // heartbeat: envia mesmo sem variação
const float VARIACAO_MIN_T = 0.5;                 // °C para disparar envio
const float VARIACAO_MIN_U = 2.0;                 // % para disparar envio
const float SALTO_MAXIMO   = 5.0;                 // leitura que foge disso da média é ruído
const float HISTERESE      = 1.0;                 // evita alerta "piscando" no limite
const int   TAM_JANELA     = 5;                   // média móvel de 5 leituras (~10 s)

DHT dht(PINO_DHT, TIPO_DHT);

float janelaT[TAM_JANELA];
float janelaU[TAM_JANELA];
int idx = 0;
int preenchidas = 0;

float mediaT = 0, mediaU = 0;
float ultimaTEnviada = -100, ultimaUEnviada = -100;
unsigned long ultimaLeitura = 0, ultimoEnvio = 0;

float limite = 30.0;
bool modoAuto = true;
bool ventLigado = false;
bool alerta = false;
unsigned long descartadas = 0;

String bufferCmd;

void setup() {
  Serial.begin(9600);
  pinMode(PINO_VENT, OUTPUT);
  pinMode(PINO_BUZZER, OUTPUT);
  dht.begin();
  bufferCmd.reserve(32);
  Serial.println(F("{\"evento\":\"iniciado\"}"));
}

void loop() {
  lerComandos();

  if (millis() - ultimaLeitura >= INTERVALO_LEITURA) {
    ultimaLeitura = millis();
    processarLeitura();
  }

  // Alarme local: funciona mesmo se a nuvem cair (bipe de 200 ms a cada 2 s)
  digitalWrite(PINO_BUZZER, alerta && (millis() % 2000 < 200));
}

// ---------- Processamento na borda ----------

float media(float *v, int n) {
  float soma = 0;
  for (int i = 0; i < n; i++) soma += v[i];
  return soma / n;
}

void processarLeitura() {
  float t = dht.readTemperature();
  float u = dht.readHumidity();

  // 1) Descarta leituras inválidas ou fora da faixa do DHT11 (0-50 °C, 20-90 %)
  if (isnan(t) || isnan(u) || t < 0 || t > 50 || u < 20 || u > 90) {
    descartadas++;
    return;
  }

  // 2) Descarta picos de ruído (salto grande em relação à média atual)
  if (preenchidas == TAM_JANELA && abs(t - mediaT) > SALTO_MAXIMO) {
    descartadas++;
    return;
  }

  // 3) Média móvel
  janelaT[idx] = t;
  janelaU[idx] = u;
  idx = (idx + 1) % TAM_JANELA;
  if (preenchidas < TAM_JANELA) preenchidas++;
  if (preenchidas < TAM_JANELA) return;  // espera a janela encher

  mediaT = media(janelaT, TAM_JANELA);
  mediaU = media(janelaU, TAM_JANELA);

  // 4) Decisão local de alerta com histerese
  bool alertaAnterior = alerta;
  if (!alerta && mediaT >= limite) alerta = true;
  else if (alerta && mediaT < limite - HISTERESE) alerta = false;

  if (modoAuto) ventLigado = alerta;
  digitalWrite(PINO_VENT, ventLigado);

  // 5) Envio só quando vale a pena (economiza banda e armazenamento)
  bool variouT = abs(mediaT - ultimaTEnviada) >= VARIACAO_MIN_T;
  bool variouU = abs(mediaU - ultimaUEnviada) >= VARIACAO_MIN_U;
  bool heartbeat = millis() - ultimoEnvio >= INTERVALO_MAX_ENVIO;

  if (variouT || variouU || heartbeat || alerta != alertaAnterior) {
    enviarEstado();
  }
}

void enviarEstado() {
  if (preenchidas < TAM_JANELA) return;  // ainda sem média válida

  Serial.print(F("{\"t\":"));         Serial.print(mediaT, 1);
  Serial.print(F(",\"u\":"));         Serial.print(mediaU, 1);
  Serial.print(F(",\"vent\":"));      Serial.print(ventLigado ? 1 : 0);
  Serial.print(F(",\"auto\":"));      Serial.print(modoAuto ? 1 : 0);
  Serial.print(F(",\"alerta\":"));    Serial.print(alerta ? 1 : 0);
  Serial.print(F(",\"limite\":"));    Serial.print(limite, 1);
  Serial.print(F(",\"descartadas\":")); Serial.print(descartadas);
  Serial.println(F("}"));

  ultimaTEnviada = mediaT;
  ultimaUEnviada = mediaU;
  ultimoEnvio = millis();
}

// ---------- Comandos vindos da nuvem ----------

void lerComandos() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      executarComando(bufferCmd);
      bufferCmd = "";
    } else if (c != '\r' && bufferCmd.length() < 31) {
      bufferCmd += c;
    }
  }
}

void executarComando(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "VENT:1") {
    modoAuto = false;
    ventLigado = true;
  } else if (cmd == "VENT:0") {
    modoAuto = false;
    ventLigado = false;
  } else if (cmd == "AUTO") {
    modoAuto = true;
    ventLigado = alerta;
  } else if (cmd.startsWith("LIMITE:")) {
    float novo = cmd.substring(7).toFloat();
    if (novo < 10 || novo > 50) {
      Serial.println(F("{\"erro\":\"limite fora da faixa\"}"));
      return;
    }
    limite = novo;
  } else {
    Serial.println(F("{\"erro\":\"comando desconhecido\"}"));
    return;
  }

  digitalWrite(PINO_VENT, ventLigado);
  Serial.print(F("{\"ack\":\""));
  Serial.print(cmd);
  Serial.println(F("\"}"));
  enviarEstado();  // confirma o novo estado para o back-end
}
