#!/usr/bin/env python3
"""
los_checker.py — Line-of-Sight Checker com calibração automática, por sistema.

As observações (visivel/oclusos) ficam na base Postgres partilhada do stack
docker do tradingBot (tabela los_observacoes, coluna 'sistema'), em vez de um
ficheiro local único — isto evita misturar observações de sistemas diferentes
(ex: Fujin e Kamitra) como aconteceu antes com o los_calibracao.json.

Ligação via variáveis de ambiente (nenhuma password fica no código):
    R2D2_DB_HOST     (default: localhost)
    R2D2_DB_PORT     (default: 5432)
    R2D2_DB_NAME     (default: ED)
    R2D2_DB_USER     (default: r2d2)
    R2D2_DB_PASSWORD (obrigatória)

Fluxo:
  1. Deteta o sistema atual pelo journal.
  2. Vai buscar o modelo orbital calibrado para esse sistema (constantes
     físicas) -- só sistemas com constantes conhecidas são suportados.
  3. Lê observacoes[] da BD, filtradas por sistema.
  4. Ajusta fase_carrier por regressão para minimizar erro.
  5. Simula a partir de agora e devolve segundos de espera.

API:
    from los_checker import calcular_espera_los
    espera = calcular_espera_los(ed_log_dir=ED_LOG_DIR)
    # 0.0 = livre (ou sem dados/sem ligação); N > 0 = aguarda N segundos

CLI:
    python los_checker.py                          -> relatório do sistema atual
    python los_checker.py registar visivel "nota"   -> regista observação
    python los_checker.py registar oclusos "nota"
"""

import glob
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

try:
    from infra_bridge import winsound
except Exception:
    winsound = None

try:
    from infra_bridge import print_ts as print
except Exception:
    pass


# ==========================================
# CONSTANTES ORBITAIS POR SISTEMA
# ==========================================
# Só sistemas aqui listados têm verificação de LOS ativa -- para qualquer
# outro sistema, calcular_espera_los() devolve 0.0 (sem bloqueio) e avisa
# que não há modelo calibrado, em vez de aplicar a física errada de outro
# sistema.
CONSTANTES_POR_SISTEMA = {
    "Fujin": {
        "raio_planeta":       5_969_000,   # metros (EDSM) -- Fujin 5
        "margem_atmosfera":   50_000,      # metros
        "semi_eixo_estacao":  25_856_000,  # metros -- Futen Spaceport
        "semi_eixo_carrier":  37_100_000,  # metros -- Zahir
        "periodo_estacao":    43_305,      # segundos (12.03h)
        "periodo_carrier":    74_431,      # segundos (20.68h)
    },
    # "Kamitra": { ... a preencher quando houver dados orbitais calibrados },
}

PASSO_SIMULACAO        = 10     # segundos
LIMITE_SIMULACAO_HORAS = 24


# ==========================================
# VETORES
# ==========================================
class Vec3:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z
    def sub(self, o):    return Vec3(self.x-o.x, self.y-o.y, self.z-o.z)
    def add(self, o):    return Vec3(self.x+o.x, self.y+o.y, self.z+o.z)
    def mult(self, s):   return Vec3(self.x*s,   self.y*s,   self.z*s)
    def dot(self, o):    return self.x*o.x + self.y*o.y + self.z*o.z
    def magnitude(self): return math.sqrt(self.x**2 + self.y**2 + self.z**2)


# ==========================================
# ORBITAL
# ==========================================
class EntidadeOrbital:
    def __init__(self, a, T, M0, epoch):
        self.a = a; self.T = T; self.M0 = M0; self.epoch = epoch

    def pos(self, t):
        dt  = (t - self.epoch).total_seconds()
        ang = (self.M0 + (2 * math.pi / self.T) * dt) % (2 * math.pi)
        return Vec3(self.a * math.cos(ang), self.a * math.sin(ang), 0.0)


# ==========================================
# OCLUSÃO (ray-sphere)
# ==========================================
def tem_los(p_est, p_car, raio_planeta, margem_atmosfera):
    d   = p_car.sub(p_est)
    o   = Vec3(-p_est.x, -p_est.y, -p_est.z)
    ddd = d.dot(d)
    if ddd == 0: return True
    t = o.dot(d) / ddd
    if t < 0 or t > 1: return True
    return p_est.add(d.mult(t)).magnitude() > (raio_planeta + margem_atmosfera)


# ==========================================
# REGRESSÃO — ajusta fase_carrier
# ==========================================
def _estado_modelo(fase_est, fase_car, epoch, t_obs, k):
    est = EntidadeOrbital(k["semi_eixo_estacao"], k["periodo_estacao"], fase_est, epoch)
    car = EntidadeOrbital(k["semi_eixo_carrier"], k["periodo_carrier"], fase_car, epoch)
    return tem_los(est.pos(t_obs), car.pos(t_obs), k["raio_planeta"], k["margem_atmosfera"])


def _score(fase_car, observacoes, epoch, k):
    acertos = 0
    for obs in observacoes:
        try:
            t = datetime.fromisoformat(obs['timestamp_utc'])
            estado = obs['estado']
            if estado not in ('visivel', 'oclusos'):
                continue
            previsto = _estado_modelo(0.0, fase_car, epoch, t, k)
            real = (estado == 'visivel')
            if previsto == real:
                acertos += 1
        except Exception:
            continue
    return acertos


def _calibrar_fase(observacoes, k):
    """Varre fase_carrier de 0 a 2π em passos de 1°, devolve a fase que
    maximiza o score. Usa a observação mais antiga como epoch."""
    if not observacoes:
        return None, 0.0, math.pi

    timestamps = []
    for obs in observacoes:
        try:
            timestamps.append(datetime.fromisoformat(obs['timestamp_utc']))
        except Exception:
            continue
    if not timestamps:
        return None, 0.0, math.pi

    epoch = min(timestamps)
    obs_validas = [o for o in observacoes if o.get('estado') in ('visivel', 'oclusos')]

    if not obs_validas:
        return epoch, 0.0, math.pi

    melhor_score  = -1
    melhor_fase   = math.pi
    passos        = 360  # resolução de 1°

    for i in range(passos):
        fase_car = (2 * math.pi * i) / passos
        s = _score(fase_car, obs_validas, epoch, k)
        if s > melhor_score:
            melhor_score = s
            melhor_fase  = fase_car

    total = len(obs_validas)
    print(f"[LOS] Regressão: {melhor_score}/{total} observações correctas "
          f"com fase_carrier={math.degrees(melhor_fase):.1f}°")

    return epoch, 0.0, melhor_fase


# ==========================================
# SISTEMA ATUAL (via journal)
# ==========================================
def obter_sistema_atual(ed_log_dir):
    if not ed_log_dir or not os.path.exists(ed_log_dir):
        return None
    lista_logs = glob.glob(os.path.join(ed_log_dir, "Journal.*.log"))
    if not lista_logs:
        return None

    ultimo_log = max(lista_logs, key=os.path.getmtime)
    sistema = None
    try:
        with open(ultimo_log, 'r', encoding='utf-8') as f:
            for linha in f:
                try:
                    d = json.loads(linha)
                    if d.get('event') in ('Location', 'FSDJump', 'CarrierJump') and 'StarSystem' in d:
                        sistema = d['StarSystem']
                except Exception:
                    continue
    except Exception:
        return None
    return sistema


# ==========================================
# BASE DE DADOS (Postgres partilhado do tradingBot)
# ==========================================
def _conectar_bd():
    host = os.environ.get("R2D2_DB_HOST", "localhost")
    port = os.environ.get("R2D2_DB_PORT", "5432")
    dbname = os.environ.get("R2D2_DB_NAME", "ED")
    user = os.environ.get("R2D2_DB_USER", "r2d2")
    password = os.environ.get("R2D2_DB_PASSWORD")
    if not password:
        raise RuntimeError("Variável de ambiente R2D2_DB_PASSWORD não definida.")
    return psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password,
        connect_timeout=5
    )


def _garantir_tabela(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS los_observacoes (
            id SERIAL PRIMARY KEY,
            sistema TEXT NOT NULL,
            timestamp_utc TIMESTAMPTZ NOT NULL,
            estado TEXT NOT NULL CHECK (estado IN ('visivel', 'oclusos')),
            nota TEXT,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_los_observacoes_sistema
        ON los_observacoes(sistema)
    """)


def _carregar_observacoes_bd(sistema):
    conn = _conectar_bd()
    try:
        with conn.cursor() as cur:
            _garantir_tabela(cur)
            conn.commit()
            cur.execute(
                "SELECT timestamp_utc, estado, nota FROM los_observacoes "
                "WHERE sistema = %s ORDER BY timestamp_utc",
                (sistema,)
            )
            linhas = cur.fetchall()
    finally:
        conn.close()

    observacoes = []
    for ts, estado, nota in linhas:
        observacoes.append({
            "timestamp_utc": ts.astimezone(timezone.utc).isoformat(),
            "estado": estado,
            "nota": nota or "",
        })
    return observacoes


def registar_observacao(sistema, estado, nota=""):
    if estado not in ("visivel", "oclusos"):
        raise ValueError("estado tem de ser 'visivel' ou 'oclusos'")
    agora = datetime.now(timezone.utc)
    conn = _conectar_bd()
    try:
        with conn.cursor() as cur:
            _garantir_tabela(cur)
            cur.execute(
                "INSERT INTO los_observacoes (sistema, timestamp_utc, estado, nota) "
                "VALUES (%s, %s, %s, %s)",
                (sistema, agora, estado, nota)
            )
            conn.commit()
    finally:
        conn.close()
    print(f"[LOS] Observação registada: sistema={sistema} estado={estado} "
          f"timestamp={agora.strftime('%Y-%m-%dT%H:%M:%S')}Z")


def _alertar_falha_bd(erro):
    print("\n" + "=" * 60)
    print(f"[LOS] ALERTA: Falha ao ligar à base de dados Postgres.")
    print(f"[LOS] Detalhe: {erro}")
    print("[LOS] Verifica se o servidor Postgres (db_r2d2) está acessível")
    print("[LOS] na rede local e se R2D2_DB_PASSWORD está definida.")
    print("=" * 60 + "\n")
    if winsound:
        try:
            winsound.Beep(400, 500)
        except Exception:
            pass


# ==========================================
# SIMULAÇÃO
# ==========================================
def _simular(estacao, carrier, agora, k):
    if tem_los(estacao.pos(agora), carrier.pos(agora), k["raio_planeta"], k["margem_atmosfera"]):
        return 0.0

    passo  = timedelta(seconds=PASSO_SIMULACAO)
    limite = agora + timedelta(hours=LIMITE_SIMULACAO_HORAS)
    t      = agora

    while t < limite:
        t += passo
        if tem_los(estacao.pos(t), carrier.pos(t), k["raio_planeta"], k["margem_atmosfera"]):
            return (t - agora).total_seconds()

    return LIMITE_SIMULACAO_HORAS * 3600.0


# ==========================================
# API PÚBLICA
# ==========================================
def calcular_espera_los(ed_log_dir=None) -> float:
    """
    Retorna segundos de espera até haver LOS livre entre a estação e o
    carrier no sistema atual. 0.0 = pode descolar imediatamente (ou não há
    dados/modelo/ligação suficiente para bloquear -- falha aberta).
    """
    sistema = obter_sistema_atual(ed_log_dir)
    if not sistema:
        print("[LOS] Não foi possível determinar o sistema atual pelo journal.")
        return 0.0

    k = CONSTANTES_POR_SISTEMA.get(sistema)
    if k is None:
        print(f"[LOS] Sem modelo orbital calibrado para o sistema '{sistema}'. "
              f"A ignorar verificação de LOS.")
        return 0.0

    try:
        observacoes = _carregar_observacoes_bd(sistema)
    except Exception as e:
        _alertar_falha_bd(e)
        return 0.0

    if not observacoes:
        print(f"[LOS] Sem observações registadas para '{sistema}' -- fase por defeito (180°).")
        epoch = datetime.now(timezone.utc)
        fase_est, fase_car = 0.0, math.pi
    else:
        epoch, fase_est, fase_car = _calibrar_fase(observacoes, k)
        if epoch is None:
            epoch = datetime.now(timezone.utc)

    agora = datetime.now(timezone.utc)
    estacao = EntidadeOrbital(k["semi_eixo_estacao"], k["periodo_estacao"], fase_est, epoch)
    carrier = EntidadeOrbital(k["semi_eixo_carrier"], k["periodo_carrier"], fase_car, epoch)

    return _simular(estacao, carrier, agora, k)


# ==========================================
# STANDALONE / CLI
# ==========================================
if __name__ == "__main__":
    from infra_bridge import ED_LOG_DIR

    if len(sys.argv) >= 2 and sys.argv[1] == "registar":
        if len(sys.argv) < 3 or sys.argv[2] not in ("visivel", "oclusos"):
            print("Uso: python los_checker.py registar <visivel|oclusos> [\"nota\"]")
            sys.exit(1)
        estado_cli = sys.argv[2]
        nota_cli = sys.argv[3] if len(sys.argv) > 3 else ""
        sistema_cli = obter_sistema_atual(ED_LOG_DIR)
        if not sistema_cli:
            print("[ERRO] Não foi possível determinar o sistema atual pelo journal.")
            sys.exit(1)
        try:
            registar_observacao(sistema_cli, estado_cli, nota_cli)
        except Exception as e:
            _alertar_falha_bd(e)
            sys.exit(1)
        sys.exit(0)

    agora_utc = datetime.now(timezone.utc)
    sistema_atual = obter_sistema_atual(ED_LOG_DIR)
    print("=" * 54)
    print("  LOS CHECKER — por sistema")
    print("=" * 54)
    print(f"  UTC actual:      {agora_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Portugal:        {(agora_utc + timedelta(hours=1)).strftime('%H:%M:%S')}")
    print(f"  Sistema atual:   {sistema_atual or 'desconhecido'}")
    print()

    espera = calcular_espera_los(ed_log_dir=ED_LOG_DIR)

    if espera == 0.0:
        print("🟢 LINHA DE VISÃO LIMPA (ou sem bloqueio calculável) — podes descolar imediatamente.")
    else:
        h, resto = divmod(int(espera), 3600)
        m, s     = divmod(resto, 60)
        partida_utc = agora_utc + timedelta(seconds=espera)
        partida_pt  = partida_utc + timedelta(hours=1)
        print(f"🔴 BLOQUEIO DETETADO — planeta no meio.")
        print(f"⏳ Espera:         {h}h {m}m {s}s")
        print(f"⏰ Partida UTC:    {partida_utc.strftime('%H:%M:%S')}")
        print(f"⏰ Partida PT:     {partida_pt.strftime('%H:%M:%S')}")
        print()
        print("Para adicionar observações:")
        print('  python los_checker.py registar visivel "nota opcional"')
        print('  python los_checker.py registar oclusos "nota opcional"')
