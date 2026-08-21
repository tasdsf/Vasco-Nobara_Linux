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

import numpy as np
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
# REGRESSÃO — auto-fit de fase_carrier E período_carrier
# ==========================================
# O período do carrier na tabela de constantes é uma medição pontual --
# ligeiramente errado, o erro de fase acumula ao longo de vários dias de
# observações e nenhuma fase única (com período fixo) consegue compensar
# isso (sintoma: ajuste só-de-fase estagna nos ~85% de acerto mesmo com
# dezenas de observações). Em vez disso, varre-se em grelha os dois
# parâmetros em simultâneo -- mesma receita já usada no lado Windows.
JANELA_PERIODO_PCT = 0.12    # +/-12% em torno do período constante
PASSO_PERIODO_PCT   = 0.002  # 0.2% da janela -> 500 candidatos de período
PASSOS_FASE          = 360   # resolução de 1°


def _calibrar_fase_periodo(observacoes, k):
    """Auto-fit de 2 parâmetros (fase_carrier E periodo_carrier) por
    varrimento em grelha contra as observações reais. Devolve
    (epoch, fase_estacao=0.0, fase_carrier, periodo_carrier_ajustado).

    Otimização: a posição da estação e o ângulo-base do carrier (sem a
    fase) só dependem do período candidato, não da fase -- pré-calculam-se
    uma vez por período, e a fase entra por adição de ângulos (cos/sin já
    tabelados), sem recalcular trigonometria por cada par (período, fase).
    """
    if not observacoes:
        return None, 0.0, math.pi, k["periodo_carrier"]

    timestamps = []
    for obs in observacoes:
        try:
            timestamps.append(datetime.fromisoformat(obs['timestamp_utc']))
        except Exception:
            continue
    if not timestamps:
        return None, 0.0, math.pi, k["periodo_carrier"]

    epoch = min(timestamps)
    # Só observações verificadas por um humano ('linux'/'win', via
    # los_calibrar.py ou CLI) entram no ajuste -- as automáticas
    # ('auto-linux'/'auto-win', registadas só porque um salto de
    # supercruise correu bem, ver registar_los_visivel_auto() em
    # supercruise_assist.py) são um sinal fraco por definição ("pelo sim
    # pelo nao: pode ter tido ajuda do utilizador") e em produção chegaram
    # a ser 88% do total -- afogavam as poucas observações manuais e o
    # ajuste passava a prever "visivel" mesmo com observações manuais
    # recentes a dizer "oclusos" (confirmado 2026-08-20 ~15:36: 3
    # observações manuais seguidas de "oclusos" todas previstas como
    # visivel; ao filtrar para só as 62 manuais, a previsão para "agora"
    # já batia certo -- 57/62 corretas, período quase idêntico ao do
    # conjunto completo, confirmando que as automáticas não acrescentavam
    # informação, só ruído).
    obs_validas = [
        o for o in observacoes
        if o.get('estado') in ('visivel', 'oclusos') and o.get('origem', '') in ('linux', 'win')
    ]
    if not obs_validas:
        return epoch, 0.0, math.pi, k["periodo_carrier"]

    # Pré-calcula, por observação: dt desde o epoch, posição da estação
    # (fase fixa 0.0, período constante -- não faz parte do ajuste) e o
    # estado real observado.
    dts, est_x, est_y, reais = [], [], [], []
    for obs in obs_validas:
        t = datetime.fromisoformat(obs['timestamp_utc'])
        dt = (t - epoch).total_seconds()
        ang_est = (2 * math.pi / k["periodo_estacao"]) * dt
        dts.append(dt)
        est_x.append(k["semi_eixo_estacao"] * math.cos(ang_est))
        est_y.append(k["semi_eixo_estacao"] * math.sin(ang_est))
        reais.append(obs['estado'] == 'visivel')

    dts   = np.array(dts)
    est_x = np.array(est_x)
    est_y = np.array(est_y)
    reais = np.array(reais, dtype=bool)

    raio_bloqueio = k["raio_planeta"] + k["margem_atmosfera"]
    a_car = k["semi_eixo_carrier"]
    periodo_base = k["periodo_carrier"]

    n_passos_periodo = int(round(1.0 / PASSO_PERIODO_PCT))
    periodos = np.linspace(periodo_base * (1 - JANELA_PERIODO_PCT),
                            periodo_base * (1 + JANELA_PERIODO_PCT),
                            n_passos_periodo)

    fases    = np.linspace(0.0, 2 * math.pi, PASSOS_FASE, endpoint=False)
    cos_fase = np.cos(fases)
    sin_fase = np.sin(fases)

    def _acertos_por_fase(periodo):
        """ Score (N observações corretas) para cada uma das PASSOS_FASE
        fases candidatas, com este período fixo -- vetorizado. """
        ang_base = (2 * math.pi / periodo) * dts        # (N,)
        cos_base = np.cos(ang_base)
        sin_base = np.sin(ang_base)

        # Adição de ângulos: roda o ângulo-base pela fase candidata sem
        # recalcular cos/sin do ângulo somado a cada combinação.
        cos_car = cos_base[:, None] * cos_fase[None, :] - sin_base[:, None] * sin_fase[None, :]  # (N,F)
        sin_car = sin_base[:, None] * cos_fase[None, :] + cos_base[:, None] * sin_fase[None, :]  # (N,F)

        car_x = a_car * cos_car
        car_y = a_car * sin_car

        dx  = car_x - est_x[:, None]
        dy  = car_y - est_y[:, None]
        ddd = dx * dx + dy * dy
        o_dot_d = (-est_x[:, None]) * dx + (-est_y[:, None]) * dy
        with np.errstate(divide='ignore', invalid='ignore'):
            t_param = np.where(ddd > 0, o_dot_d / ddd, 0.0)

        px = est_x[:, None] + dx * t_param
        py = est_y[:, None] + dy * t_param
        mag = np.sqrt(px * px + py * py)

        visivel_previsto = (ddd == 0) | (t_param < 0) | (t_param > 1) | (mag > raio_bloqueio)
        return (visivel_previsto == reais[:, None]).sum(axis=0)   # (F,)

    melhor_score_global = -1
    periodos_no_topo = []

    for periodo in periodos:
        acertos = _acertos_por_fase(periodo)
        score = int(acertos.max())
        if score > melhor_score_global:
            melhor_score_global = score
            periodos_no_topo = [float(periodo)]
        elif score == melhor_score_global:
            periodos_no_topo.append(float(periodo))

    # Escolhe o período CENTRAL (mediana) de entre os empatados no melhor
    # score -- o argmax salta pelas bordas do planalto e é instável de
    # corrida para corrida; a mediana do planalto é estável.
    periodos_no_topo.sort()
    melhor_periodo = periodos_no_topo[len(periodos_no_topo) // 2]

    # Recalcula a fase ótima especificamente para o período mediano
    # escolhido (o planalto pode não ser perfeitamente plano em fase).
    acertos_final = _acertos_por_fase(melhor_periodo)
    idx_melhor = int(np.argmax(acertos_final))
    melhor_fase  = float(fases[idx_melhor])
    melhor_score = int(acertos_final[idx_melhor])

    total = len(obs_validas)
    desvio_pct = (melhor_periodo - periodo_base) / periodo_base * 100.0
    largura_planalto = len(periodos_no_topo)

    print(f"[LOS] Auto-fit (fase+período): {melhor_score}/{total} observações correctas")
    print(f"[LOS]   período_carrier ajustado = {melhor_periodo:.0f}s "
          f"({melhor_periodo/3600:.3f}h) [{desvio_pct:+.3f}% vs constante {periodo_base}s]")
    print(f"[LOS]   fase_carrier ajustada    = {math.degrees(melhor_fase):.1f}°")
    print(f"[LOS]   planalto no topo         = {largura_planalto}/{n_passos_periodo} "
          f"períodos empatados (incerteza real do período)")

    return epoch, 0.0, melhor_fase, melhor_periodo


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
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            origem TEXT NOT NULL DEFAULT 'linux'
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_los_observacoes_sistema
        ON los_observacoes(sistema)
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_los_observacoes_sistema_ts_estado
        ON los_observacoes (sistema, timestamp_utc, estado)
    """)


def _carregar_observacoes_bd(sistema):
    conn = _conectar_bd()
    try:
        with conn.cursor() as cur:
            _garantir_tabela(cur)
            conn.commit()
            cur.execute(
                "SELECT timestamp_utc, estado, nota, origem FROM los_observacoes "
                "WHERE sistema = %s ORDER BY timestamp_utc",
                (sistema,)
            )
            linhas = cur.fetchall()
    finally:
        conn.close()

    observacoes = []
    for ts, estado, nota, origem in linhas:
        observacoes.append({
            "timestamp_utc": ts.astimezone(timezone.utc).isoformat(),
            "estado": estado,
            "nota": nota or "",
            "origem": origem or "",
        })
    return observacoes


def registar_observacao(sistema, estado, nota="", origem="auto-linux"):
    """ origem distingue quem gravou a observação: 'linux' é o humano a
    calibrar via los_calibrar.py / CLI interativa; 'auto-linux' (default)
    fica reservado para quando o próprio vasco.py vier a registar
    observações de forma automática, sem input humano. """
    if estado not in ("visivel", "oclusos"):
        raise ValueError("estado tem de ser 'visivel' ou 'oclusos'")
    agora = datetime.now(timezone.utc)
    conn = _conectar_bd()
    try:
        with conn.cursor() as cur:
            _garantir_tabela(cur)
            cur.execute(
                "INSERT INTO los_observacoes (sistema, timestamp_utc, estado, nota, origem) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sistema, agora, estado, nota, origem)
            )
            conn.commit()
    finally:
        conn.close()
    print(f"[LOS] Observação registada: sistema={sistema} estado={estado} "
          f"timestamp={agora.strftime('%Y-%m-%dT%H:%M:%S')}Z origem={origem}")


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
        periodo_car = k["periodo_carrier"]
    else:
        epoch, fase_est, fase_car, periodo_car = _calibrar_fase_periodo(observacoes, k)
        if epoch is None:
            epoch = datetime.now(timezone.utc)

    agora = datetime.now(timezone.utc)
    estacao = EntidadeOrbital(k["semi_eixo_estacao"], k["periodo_estacao"], fase_est, epoch)
    carrier = EntidadeOrbital(k["semi_eixo_carrier"], periodo_car, fase_car, epoch)

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
            registar_observacao(sistema_cli, estado_cli, nota_cli, origem="linux")
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
