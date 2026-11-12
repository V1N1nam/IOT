"""Gera 24 h de leituras SIMULADAS antes da primeira leitura real, só para ilustrar o histórico.

Todas ficam com ilustrativo=1: aparecem tracejadas em cinza no dashboard, saem marcadas no CSV
e não entram nas métricas de SLA.

Uso:  py ferramentas/gerar_dados_demo.py            (recria os dados ilustrativos)
      py ferramentas/gerar_dados_demo.py --apagar   (remove todos os dados ilustrativos)
"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend.py fica na raiz
from backend import conectar_db, criar_tabelas

LIMITE = 30.0


def gerar(inicio, fim):
    linhas = []
    ts = inicio
    while ts < fim:
        h = time.localtime(ts).tm_hour + time.localtime(ts).tm_min / 60
        t = 25 + 2 * math.sin((h - 9) / 24 * 2 * math.pi) + random.gauss(0, 0.2)  # mais quente à tarde
        if 14.5 <= h < 15:  # um pico de calor para mostrar um alerta no histórico
            t += 5 * math.sin((h - 14.5) / 0.5 * math.pi)
        u = 55 - (t - 25) * 2 + random.gauss(0, 1)
        alerta = int(t > LIMITE)
        linhas.append((ts, round(t, 1), round(u, 1), alerta, 1, alerta, LIMITE, 0,
                       round(random.uniform(150, 400), 1), 1))
        ts += 60
    return linhas


if __name__ == "__main__":
    criar_tabelas()
    with conectar_db() as conn:
        conn.execute("DELETE FROM leituras WHERE ilustrativo = 1")
        if "--apagar" in sys.argv:
            print("Dados ilustrativos removidos.")
            sys.exit()
        fim = time.time()
        inicio = fim - 86400
        primeira_real = conn.execute("SELECT MIN(ts) FROM leituras WHERE ts >= ? AND ilustrativo = 0",
                                     (inicio,)).fetchone()[0]
        linhas = gerar(inicio, (primeira_real or fim) - 60)
        conn.executemany("""INSERT INTO leituras (ts, temperatura, umidade, ventilador, modo_auto, alerta,
                            limite, descartadas, latencia_ms, ilustrativo) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         linhas)
    print(f"{len(linhas)} leituras ILUSTRATIVAS geradas (1 por minuto).")
