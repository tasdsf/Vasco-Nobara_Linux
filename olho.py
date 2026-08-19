import os
import json
import time
import logging
import cv2
import numpy as np
from infra_bridge import keyboard
import glob
from collections import deque
import sys
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss, SCREEN_WIDTH, SCREEN_HEIGHT, print_ts as print


if sys.platform == "win32":
    import winsound
    def tocar_alarme_erro():
        winsound.Beep(1200, 300)
else:
    import os
    def tocar_alarme_erro():
        # Usa o beep do sistema ou simplesmente print (ou ignora)
        print("\a") # Carácter ASCII de Bell (emite um som no terminal se configurado)

# ==========================================
# 0. LOGGING, SOM E INFRAESTRUTURA
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_logs = os.path.join(diretorio_atual, "logs")
pasta_imagens = os.path.join(diretorio_atual, "images")
os.makedirs(pasta_logs, exist_ok=True)

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("olho")
_logger.setLevel(logging.WARNING)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [OLHO_PILOTO] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

#def tocar_alarme_erro():
#    winsound.Beep(1200, 300)
#    time.sleep(0.1)
#    winsound.Beep(1200, 600)

def tocar_alarme_sucesso():
    winsound.Beep(600, 200)
    winsound.Beep(800, 200)
    winsound.Beep(1000, 400)

def largar_todas_as_teclas():
    for t in ["w", "s", "a", "d"]:
        pydirectinput.keyUp(t)

def abortar_com_erro(mensagem):
    largar_todas_as_teclas()
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    tocar_alarme_erro()
    falar("Navigation error. Manual control required.")
    sys.exit(1)

def abortar_por_utilizador():
    largar_todas_as_teclas()
    print("\n[AVISO] Intervenção manual solicitada.")
    _logger.warning("Operação cancelada via teclado (tecla Q).")
    winsound.Beep(800, 300)
    falar("Manual override engaged. Yielding controls.")
    sys.exit(1)

NOME_JANELA_PROD = "R2D2 Sniper v11 - Dual-Stage Core"
VISUAL_DEBUG = True

def focar_jogo_seguro():
    """ Traz a janela do jogo para a frente pelo PID/Handle sem injetar comandos de rato """
    print("[SISTEMA] A invocar processo do Elite Dangerous via API do Windows...")
    try:
        janelas = gw.getWindowsWithTitle("Elite - Dangerous (CLIENT)")
        if not janelas:
            janelas = gw.getWindowsWithTitle("Elite Dangerous")

        if janelas:
            janela_elite = janelas[0]
            janela_elite.activate()
            time.sleep(1.0) # Tempo vital para o DWM renderizar a janela à frente
            print("[OK] Acesso biométrico ao cockpit estabelecido.")
            return True
        else:
            print("[ERRO] Processo do Elite não encontrado no renderizador de janelas.")
            return False
    except Exception as e:
        print(f"[AVISO] Exceção na API do Windows (O foco terá de ser manual): {e}")
        return False

def inicializar_infraestrutura():
    print("[SISTEMA] A instanciar hooks de visão computacional...")

    focar_jogo_seguro()

    # Força a sessão PipeWire (e o eventual popup do KDE) a resolver-se AGORA,
    # antes de criar a janela de debug topmost — caso contrário essa janela
    # fica sempre-no-topo e esconde o popup por completo.
    with mss.mss() as sct:
        monitores = sct.monitors
        sct.grab({"top": 0, "left": 0, "width": 50, "height": 50})

    if VISUAL_DEBUG:
        # Permite redimensionar manualmente e previne o esmagamento de DPI
        cv2.namedWindow(NOME_JANELA_PROD, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(NOME_JANELA_PROD, 860, 350) # Força o tamanho interno
        if len(monitores) > 2:
            ecra_secundario = monitores[2]
            cv2.moveWindow(NOME_JANELA_PROD, ecra_secundario["left"] + 20, ecra_secundario["top"] + 50)
        else:
            # Monitor ultrawide 3840x1080: o jogo ocupa a metade esquerda (0-1920px),
            # por isso a janela de debug vai para a metade direita, fora do jogo.
            cv2.moveWindow(NOME_JANELA_PROD, 1950, 50)
        cv2.setWindowProperty(NOME_JANELA_PROD, cv2.WND_PROP_TOPMOST, 1)

        # Dá tempo ao KWin para mapear/posicionar a janela (senão o loop de
        # orientação começa a mandar teclas antes de tudo estar assente) e só
        # depois reforça o foco no jogo — criar a janela pode ter roubado o foco.
        for _ in range(5):
            cv2.waitKey(50)
        time.sleep(0.5)
        focar_jogo_seguro()
        time.sleep(0.5)

# ==========================================
# 1. SETUP DE CONFIGURAÇÃO (BÚSSOLA E HUD)
# ==========================================
pydirectinput.PAUSE = 0.01
BOT_ATIVO = True

DEAD_ZONE_BUSSOLA = 2
RAIO_AJUSTE_FINO = 12
IMPULSO_BUSSOLA = 0.22
TOLERANCIA_BOLA = 3.5

# Mantidos os teus valores de calibração fina atualizados:
MONITOR_HUD = {"top": 402, "left": 762, "width": 359, "height": 302}
DEAD_ZONE_HUD = 15
IMPULSO_HUD = 0.17

historico_bola_x = deque(maxlen=5)
historico_bola_y = deque(maxlen=5)

# Throttle do TRACE de ALINHADO_MACRO -- o loop corre a ~25Hz e este ramo
# dispara todos os ciclos quando já está centrado, inundando a consola.
_ultimo_trace_alinhado = 0.0
_INTERVALO_TRACE_ALINHADO = 2.0  # segundos (>10x menos que os ~0.04s do loop)

caminho_memoria = os.path.join(diretorio_atual, "memoria_bussola.json")
caminho_coordenadas = os.path.join(diretorio_atual, "coordenadas_bussola.json")

try:
    with open(caminho_memoria, "r") as f: memoria = json.load(f)
except Exception as e:
    abortar_com_erro(f"Falha ao ler memoria_bussola.json: {e}")

try:
    template_alvo_hud = cv2.imread(os.path.join(pasta_imagens, "target.png"), cv2.IMREAD_COLOR)
    if template_alvo_hud is None: raise FileNotFoundError("TARGET.png ausente")
    # Retículo do alvo é composto por dois arcos: este é o arco de baixo
    # (sempre abaixo e à direita do arco de cima, target.png) -- serve de
    # fallback quando um planeta/corpo celeste tapa só o arco de cima.
    template_alvo_hud_low = cv2.imread(os.path.join(pasta_imagens, "target_low.png"), cv2.IMREAD_COLOR)
    if template_alvo_hud_low is None: raise FileNotFoundError("target_low.png ausente")
except Exception as e:
    abortar_com_erro(f"Erro de I/O na imagem TARGET.png: {e}")

# ==========================================
# 2. TELEMETRIA
# ==========================================
def _normalizar_nome_nave(nome_cru):
    """Alguns eventos do journal só trazem o campo cru (sem 'Ship_Localised'),
    o que produzia nomes como 'Cobramkv' que não batem com a chave calibrada
    'Cobra Mk V' em coordenadas_bussola.json. Mesmo mapeamento que já existia
    no debug/zolho-teste.py."""
    n = nome_cru.lower()
    if "cobramk3" in n: return "Cobra Mk III"
    elif "cobramk4" in n: return "Cobra Mk IV"
    elif "cobramkv" in n: return "Cobra Mk V"
    return nome_cru.title()

def obter_modelo_nave_atual():
    try:
        from infra_bridge import ED_LOG_DIR
        caminho_logs = ED_LOG_DIR
        lista_logs = glob.glob(os.path.join(caminho_logs, "Journal.*.log"))
        if not lista_logs: return "Desconhecido"

        ultimo_log = max(lista_logs, key=os.path.getmtime)
        modelo_nave = "Desconhecido"
        with open(ultimo_log, 'r', encoding='utf-8') as f:
            for linha in f:
                try:
                    log_data = json.loads(linha)
                    # ShipyardSwap usa "ShipType" (não "Ship"); Loadout tem "Ship" e
                    # confirma o troço final da troca — ambos têm de ser reconhecidos,
                    # senão o modelo fica preso à nave anterior após um ShipyardSwap.
                    if log_data.get("event") in ["LoadGame", "ShipyardSwap", "Loadout", "Location", "Commander"]:
                        if "Ship_Localised" in log_data: modelo_nave = log_data["Ship_Localised"]
                        elif "Ship" in log_data: modelo_nave = _normalizar_nome_nave(log_data["Ship"])
                        elif "ShipType_Localised" in log_data: modelo_nave = log_data["ShipType_Localised"]
                        elif "ShipType" in log_data: modelo_nave = _normalizar_nome_nave(log_data["ShipType"])
                except: continue
        return modelo_nave
    except: return "Desconhecido"

def _truncar_monitor_config(cfg_pos):
    """Garante que a regiao de captura nunca ultrapassa os limites do monitor
    -- sem isto, uma calibracao (ou o fallback de emergencia) fora dos limites
    faz sct.grab() devolver uma imagem vazia e o cv2.cvtColor rebenta com
    '!_src.empty()'. Mesma logica usada no debug/zolho-teste.py."""
    width = max(10, min(cfg_pos["width"], SCREEN_WIDTH))
    height = max(10, min(cfg_pos["height"], SCREEN_HEIGHT))
    top = max(0, min(cfg_pos["top"], SCREEN_HEIGHT - height))
    left = max(0, min(cfg_pos["left"], SCREEN_WIDTH - width))
    return {"top": top, "left": left, "width": width, "height": height}

def carregar_dados_calibracao(nave_atual):
    """ ARQUITETURA MULTI-NAVE ATUALIZADA """
    if os.path.exists(caminho_coordenadas):
        try:
            with open(caminho_coordenadas, "r") as f:
                cfg = json.load(f)

            # Formato Novo: Procura a sub-chave direta da nave ativa (Evita que o Hauler seja esmagado)
            if nave_atual in cfg:
                dados_nave = cfg[nave_atual]
                cfg_pos = {k: int(v) for k, v in dados_nave["MONITOR_CONFIG"].items()}
                return _truncar_monitor_config(cfg_pos), int(dados_nave["CX_NEUTRO"]), int(dados_nave["CY_NEUTRO"])

            # Formato Antigo: Retrocompatibilidade se o ficheiro ainda for do tipo flat
            if cfg.get("Nave") == nave_atual:
                cfg_pos = {k: int(v) for k, v in cfg["MONITOR_CONFIG"].items()}
                return _truncar_monitor_config(cfg_pos), int(cfg["CX_NEUTRO"]), int(cfg["CY_NEUTRO"])

            print(f"[AVISO] Nenhuma calibração encontrada para '{nave_atual}'. A usar perfil genérico de emergência.")
        except Exception as e:
            print(f"[ERRO] Falha ao processar a matriz de calibração: {e}")

    cfg_pos = _truncar_monitor_config({"top": 1210, "left": 918, "width": 90, "height": 100})
    return cfg_pos, 47, 48

# ==========================================
# 3. PIPELINE DE VISÃO: BÚSSOLA E HUD
# ==========================================
def localizar_bola(img_bgr, cx, cy):
    img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mascara_final = np.zeros(img_hsv.shape[:2], dtype=np.uint8)
    px, py = 0, 0
    is_hollow = False

    for perfil in memoria:
        mask = cv2.inRange(img_hsv, np.array(perfil['min']), np.array(perfil['max']))
        contornos, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contornos:
            ponto_contorno = max(contornos, key=cv2.contourArea)
            if cv2.contourArea(ponto_contorno) > 2:
                M = cv2.moments(ponto_contorno)
                if M["m00"] != 0:
                    px = int(M["m10"] / M["m00"])
                    py = int(M["m01"] / M["m00"])
                    # Em vez de testar um único pixel (muito sensível a ruído/
                    # anti-aliasing, causava "oca" a piscar sem correlação real
                    # com o alvo estar mesmo atrás), olha para a proporção
                    # preenchida numa pequena vizinhança à volta do centro.
                    try:
                        r = 2
                        vizinhanca = mask[max(0, py-r):py+r+1, max(0, px-r):px+r+1]
                        proporcao_preenchida = (np.count_nonzero(vizinhanca) / vizinhanca.size) if vizinhanca.size else 1.0
                        is_hollow = proporcao_preenchida < 0.5
                    except Exception:
                        is_hollow = False
                        proporcao_preenchida = 1.0

                    historico_bola_x.append(px)
                    historico_bola_y.append(py)
                    if len(historico_bola_x) >= 3:
                        dx = px - cx
                        dy = py - cy
                        if abs(dx) <= DEAD_ZONE_BUSSOLA and abs(dy) <= DEAD_ZONE_BUSSOLA:
                            global _ultimo_trace_alinhado
                            agora_trace = time.time()
                            if agora_trace - _ultimo_trace_alinhado >= _INTERVALO_TRACE_ALINHADO:
                                print(f"[TRACE] bola=({px},{py}) centro=({cx},{cy}) dx={dx} dy={dy} -> ALINHADO_MACRO")
                                _ultimo_trace_alinhado = agora_trace
                            return "ALINHADO_MACRO", (px, py), mask, abs(dx), abs(dy)

                        # Mapeamento derivado de testes diretos e isolados: 'w'
                        # diminui y, 'a' diminui x. Logo dy>0 (abaixo) -> w,
                        # dy<0 -> s (oposto); dx>0 (direita) -> a, dx<0 -> d (oposto).
                        passos = []
                        if dy > DEAD_ZONE_BUSSOLA: passos.append("S")
                        elif dy < -DEAD_ZONE_BUSSOLA: passos.append("W")
                        if dx > DEAD_ZONE_BUSSOLA: passos.append("D")
                        elif dx < -DEAD_ZONE_BUSSOLA: passos.append("A")
                        comando = " + ".join(passos)
                        # Alvo "atrás" (bola oca): mesma direção de correção de
                        # sempre — o nariz tem de rodar na mesma para lá, oca ou
                        # não — só que o chamador aplica um impulso maior (8x).
                        prefixo = "OCA:" if is_hollow else ""
                        print(f"[TRACE] bola=({px},{py}) centro=({cx},{cy}) dx={dx} dy={dy} preenchido={proporcao_preenchida:.2f} -> {prefixo}{comando}")
                        return f"{prefixo}{comando}", (px, py), mask, abs(dx), abs(dy)
                    return "AQUECENDO", (px, py), mask, 0, 0

    historico_bola_x.clear()
    historico_bola_y.clear()
    return "NÃO_DETETADO", None, mascara_final, 0, 0

def localizar_alvo_hud(sct):
    img_bgra = np.array(sct.grab(MONITOR_HUD))
    img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

    cx_hud = MONITOR_HUD["width"] // 2
    cy_hud = MONITOR_HUD["height"] // 2

    res = cv2.matchTemplate(img_bgr, template_alvo_hud, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= 0.70:
        h, w = template_alvo_hud.shape[:2]
        tx = max_loc[0] + (w // 2)
        ty = max_loc[1] + (h // 2)
        dx = tx - cx_hud
        dy = ty - cy_hud
        return True, dx, dy, img_bgr, max_val

    # target.png (arco de cima do retículo) pode ficar tapado por um
    # planeta/corpo celeste sem tapar target_low.png (arco de baixo do
    # mesmo retículo, sempre abaixo e à direita do primeiro) -- tenta esse
    # como fallback. Só aceita se a posição bater com a geometria esperada
    # (target_low abaixo/à direita da melhor posição vista para target.png,
    # mesmo que essa não tenha passado o threshold), para não confundir
    # ruído laranja solto no HUD com o retículo real.
    res_low = cv2.matchTemplate(img_bgr, template_alvo_hud_low, cv2.TM_CCOEFF_NORMED)
    _, max_val_low, _, max_loc_low = cv2.minMaxLoc(res_low)

    if max_val_low >= 0.70 and max_loc_low[0] >= max_loc[0] and max_loc_low[1] >= max_loc[1]:
        w = template_alvo_hud_low.shape[1]
        tx = max_loc_low[0] + (w // 2)
        ty = max_loc_low[1]  # topo do arco de baixo, nao o centro -- o alvo fica ACIMA do target_low, nao em cima dele
        dx = tx - cx_hud
        dy = ty - cy_hud
        return True, dx, dy, img_bgr, max_val_low

    return False, 0, 0, img_bgr, max(max_val, max_val_low)

# ==========================================
# 4. CONTROLADORES DE VOO CX_NEUTRO
# ==========================================
# Estado de escalada: se o mesmo comando não estiver a mover a bola de facto
# (não só a repetir-se), o impulso vai aumentando progressivamente — em vez
# de insistir para sempre com o mesmo impulso fraco que não tem efeito.
_escalada_bussola = {"comando": None, "px": None, "py": None, "sem_progresso": 0}

def aplicar_manobra_bussola(comando, dist_x, dist_y, coords_bola=None):
    global _escalada_bussola

    if comando in ["NÃO_DETETADO", "ALINHADO_MACRO", "AQUECENDO"]:
        _escalada_bussola = {"comando": None, "px": None, "py": None, "sem_progresso": 0}
        largar_todas_as_teclas()
        return

    # Alvo "atrás" (bola oca) usa a MESMA direção de correção — o nariz tem de
    # rodar na mesma para lá — só que com um impulso maior (8x) e cooldown
    # proporcional, já que a distância angular a percorrer é maior.
    is_oca = comando.startswith("OCA:")
    if is_oca:
        comando = comando[len("OCA:"):]

    teclas_necessarias = []
    if "W" in comando: teclas_necessarias.append("w")
    if "S" in comando: teclas_necessarias.append("s")
    if "A" in comando: teclas_necessarias.append("a")
    if "D" in comando: teclas_necessarias.append("d")

    for t in ["w", "s", "a", "d"]:
        if t not in teclas_necessarias: pydirectinput.keyUp(t)

    dist_max = max(dist_x, dist_y)

    if is_oca:
        print(f"[INFO] 8 * IMPULSO_BUSSOLA (ALVO_ATRAS): {teclas_necessarias}")
        for t in teclas_necessarias: pydirectinput.keyDown(t)
        time.sleep(8 * IMPULSO_BUSSOLA)
        largar_todas_as_teclas()
        time.sleep(4.0)
        return

    # Deteta falta de progresso: mesmo comando E a bola não se moveu (±1px)
    # desde a última tentativa — escala o impulso em vez de repetir o mesmo
    # impulso fraco indefinidamente.
    sem_progresso_agora = (
        coords_bola is not None
        and comando == _escalada_bussola["comando"]
        and _escalada_bussola["px"] is not None
        and abs(coords_bola[0] - _escalada_bussola["px"]) <= 1
        and abs(coords_bola[1] - _escalada_bussola["py"]) <= 1
    )
    if sem_progresso_agora:
        _escalada_bussola["sem_progresso"] += 1
    else:
        _escalada_bussola["sem_progresso"] = 0
    _escalada_bussola["comando"] = comando
    if coords_bola is not None:
        _escalada_bussola["px"], _escalada_bussola["py"] = coords_bola

    multiplicador_base = 4 if dist_max > RAIO_AJUSTE_FINO else 1
    multiplicador = min(multiplicador_base + _escalada_bussola["sem_progresso"], 12)

    if multiplicador > multiplicador_base:
        print(f"[INFO] {multiplicador} * IMPULSO_BUSSOLA (escalada, sem progresso x{_escalada_bussola['sem_progresso']}): {teclas_necessarias}")
    else:
        print(f"[INFO] {multiplicador} * IMPULSO_BUSSOLA: {teclas_necessarias}")
    for t in teclas_necessarias: pydirectinput.keyDown(t)
    time.sleep(multiplicador * IMPULSO_BUSSOLA)
    for t in teclas_necessarias: pydirectinput.keyUp(t)

    largar_todas_as_teclas()
    time.sleep(2.0)

def aplicar_manobra_hud(dx, dy):
    teclas = []
    if dy < -DEAD_ZONE_HUD: teclas.append('w')
    elif dy > DEAD_ZONE_HUD: teclas.append('s')
    if dx < -DEAD_ZONE_HUD: teclas.append('a')
    elif dx > DEAD_ZONE_HUD: teclas.append('d')

    for t in ["w", "s", "a", "d"]:
        if t not in teclas: pydirectinput.keyUp(t)

    if teclas:
        print(f"[INFO] IMPULSO_HUD: {teclas}") # CORRIGIDO: Agora lista os inputs corretos
        for t in teclas: pydirectinput.keyDown(t)
        time.sleep(IMPULSO_HUD)
        for t in teclas: pydirectinput.keyUp(t)
        time.sleep(2.0) # Mantidos os 2 segundos estruturais de estabilização

ROLL_DESOCLUIR_COOLDOWN = 2.0  # segundos de pausa depois do impulso

# Telemetria de sequência: quantos roll_desocluir seguidos (mesma ocorrência
# de oclusão) até o loop principal parar de os chamar. Gap > 4s entre
# chamadas conta como ocorrência nova. Sem isto não havia como saber, sem
# contar linhas de log à mão, se 2s de cooldown chega ou se ainda encadeia
# muitos impulsos -- dados para afinar o valor depois.
_ultimo_roll_desocluir_ts = 0.0
_streak_roll_desocluir = 0

def aplicar_roll_desocluir(motivo="não especificado"):
    """ Pequeno impulso de roll -- não muda o rumo, só a orientação -- para
    tentar desocluir a vista quando algo (sol/planeta/corpo celeste) está a
    tapar a zona do HUD onde o retículo do alvo deveria aparecer.

    Duração do impulso medida e confirmada normal (~0.53s, não é tecla
    presa) -- o problema real era a ausência de cooldown: sem pausa
    nenhuma, o próximo ciclo do loop principal via logo a bússola/HUD ainda
    por confirmar e disparava outro roll na mesma direção quase sem
    intervalo (~0.64s) -- 16 seguidos numa ocorrência real (log
    2026-08-19 05:39:12-22, ~10.3s a rodar sem parar). Cooldown de 2s a
    seguir ao impulso, mais telemetria de sequência (streak) para afinar
    este valor com dados reais mais tarde. """
    global _ultimo_roll_desocluir_ts, _streak_roll_desocluir

    agora = time.time()
    if agora - _ultimo_roll_desocluir_ts > (ROLL_DESOCLUIR_COOLDOWN + 2.0):
        _streak_roll_desocluir = 0  # gap grande -- conta como ocorrência nova
    _streak_roll_desocluir += 1
    _ultimo_roll_desocluir_ts = agora

    inicio = time.time()
    pydirectinput.keyDown('q')
    time.sleep(0.5)
    pydirectinput.keyUp('q')
    duracao = time.time() - inicio
    _logger.warning(f"aplicar_roll_desocluir chamado ({motivo}) -- sequencia #{_streak_roll_desocluir} -- "
                     f"duracao real do impulso: {duracao:.2f}s (esperado ~0.50s) -- cooldown {ROLL_DESOCLUIR_COOLDOWN}s")
    time.sleep(ROLL_DESOCLUIR_COOLDOWN)

# ==========================================
# 5. EXECUÇÃO PRINCIPAL
# ==========================================
def executar():
    nave_ativa = obter_modelo_nave_atual()
    print(f"[INFO] Nave Actual: {nave_ativa}")
    MONITOR_CONFIG, CX_NEUTRO, CY_NEUTRO = carregar_dados_calibracao(nave_ativa)
    print(f"[INFO] Dados Calibracao: MONITOR_CONFIG: {MONITOR_CONFIG}\n    CX_NEUTRO: {CX_NEUTRO} CY_NEUTRO: {CY_NEUTRO}")

    tempo_inicio_centrado = None
    TEMPO_ESTABILIDADE_FINAL = 3.0
    tempo_cego = None
    LIMITE_CEGO = 30.0
    tempo_inicio_manobra = time.time()
    LIMITE_MANOBRA = 180.0

    inicializar_infraestrutura()

    print(f"\n==================================================")
    print(f"R2D2 Sniper v11 - Pipeline de Orientação")
    print(f"Módulo de Prioridade Dinâmica | Nave: {nave_ativa}")
    print("==================================================\n")
    print("Armando loop fechado em 1 segundo...")
    time.sleep(1)

    with mss.mss() as sct:
        try: monitor_jogo = sct.monitors[1]
        except: monitor_jogo = sct.monitors[0]

        area_bussola = {
            "top": monitor_jogo["top"] + MONITOR_CONFIG["top"],
            "left": monitor_jogo["left"] + MONITOR_CONFIG["left"],
            "width": MONITOR_CONFIG["width"], "height": MONITOR_CONFIG["height"]
        }

        while True:
            if keyboard.is_pressed('q'):
                abortar_por_utilizador()

            if BOT_ATIVO:
                if time.time() - tempo_inicio_manobra > LIMITE_MANOBRA:
                    abortar_com_erro(f"Bloqueio de timeout. Manobra demorou mais de {LIMITE_MANOBRA}s.")

                img_bussola = cv2.cvtColor(np.array(sct.grab(area_bussola)), cv2.COLOR_BGRA2BGR)
                cmd_bussola, coords_bola, mask_hsv, dist_x, dist_y = localizar_bola(img_bussola, CX_NEUTRO, CY_NEUTRO)

                encontrou_hud, dx_hud, dy_hud, img_hud, max_val_hud = localizar_alvo_hud(sct)

                alvo_nas_costas = cmd_bussola.startswith("OCA:")
                comando_display = ""

                if encontrou_hud and not alvo_nas_costas:
                    tempo_cego = None
                    if abs(dx_hud) <= DEAD_ZONE_HUD and abs(dy_hud) <= DEAD_ZONE_HUD:
                        comando_display = "ALVO BLOQUEADO NO HUD!"
                        largar_todas_as_teclas()

                        if tempo_inicio_centrado is None:
                            tempo_inicio_centrado = time.time()

                        if time.time() - tempo_inicio_centrado >= TEMPO_ESTABILIDADE_FINAL:
                            print("\n[SUCESSO] Vetor trancado. Coordenadas estáveis.")
                            tocar_alarme_sucesso()
                            falar("Alignment successful. Vector locked.")
                            sys.exit(0)
                    else:
                        tempo_inicio_centrado = None
                        comando_display = f"MICRO-AJUSTE HUD (DX:{dx_hud} DY:{dy_hud})"
                        aplicar_manobra_hud(dx_hud, dy_hud)

                else:
                    tempo_inicio_centrado = None

                    if not coords_bola:
                        comando_display = "MACRO: NÃO_DETETADO"
                        if tempo_cego is None: tempo_cego = time.time()
                        elif time.time() - tempo_cego > LIMITE_CEGO:
                            abortar_com_erro("Perda prolongada de telemetria visual da bússola.")
                        largar_todas_as_teclas()
                        # Muitas vezes a falha é o sol/planeta a tapar o HUD --
                        # um pequeno impulso de roll (não muda o rumo, só a
                        # orientação) pode desocluir a vista.
                        aplicar_roll_desocluir(f"bussola NAO_DETETADO ha {time.time()-tempo_cego:.1f}s -- possivel sol/planeta a tapar")
                    elif cmd_bussola == "ALINHADO_MACRO":
                        # A bússola diz que o nariz já aponta ao alvo mas o HUD
                        # não confirma nenhum dos dois arcos do retículo --
                        # normalmente um planeta/corpo celeste a tapar
                        # exatamente essa zona. Sem isto o loop ficava parado
                        # (ALINHADO_MACRO não move nada) até o timeout de
                        # LIMITE_MANOBRA. Mesmo recurso de roll do ramo
                        # NÃO_DETETADO acima.
                        tempo_cego = None
                        comando_display = "MACRO: ALINHADO_MACRO (HUD tapado -- a rodar)"
                        largar_todas_as_teclas()
                        aplicar_roll_desocluir(f"ALINHADO_MACRO mas HUD nao confirma reticulo (bola a dx={dist_x} dy={dist_y} do centro) -- possivel oclusao do reticulo")
                    else:
                        tempo_cego = None
                        comando_display = f"MACRO: {cmd_bussola}"
                        aplicar_manobra_bussola(cmd_bussola, dist_x, dist_y, coords_bola)

                if VISUAL_DEBUG:
                    img_hud_bussola = img_bussola.copy()
                    cv2.rectangle(img_hud_bussola, (CX_NEUTRO-DEAD_ZONE_BUSSOLA, CY_NEUTRO-DEAD_ZONE_BUSSOLA),
                                           (CX_NEUTRO+DEAD_ZONE_BUSSOLA, CY_NEUTRO+DEAD_ZONE_BUSSOLA), (255, 255, 255), 1)
                    cv2.circle(img_hud_bussola, (CX_NEUTRO, CY_NEUTRO), 1, (0, 165, 255), -1)
                    if coords_bola:
                        cv2.circle(img_hud_bussola, coords_bola, 3, (0, 255, 0), -1)

                    view_zoom = cv2.resize(img_hud_bussola, (300, 350), interpolation=cv2.INTER_NEAREST)
                    divisor = np.ones((350, 10, 3), dtype=np.uint8) * 80

                    cv2.putText(view_zoom, comando_display, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0) if encontrou_hud else (0, 165, 255), 1)

                    img_hud_debug = cv2.resize(img_hud, (550, 350))
                    cv2.putText(img_hud_debug, f"HUD MATCH: {max_val_hud*100:.1f}%", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0) if encontrou_hud else (0, 0, 255), 2)

                    if encontrou_hud:
                        cv2.putText(img_hud_debug, f"Desvio Real: DX:{dx_hud} DY:{dy_hud}", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                    # CORRIGIDO: Removida a barra cinzenta duplicada (limpeza visual completa)
                    painel_final = np.hstack((view_zoom, divisor, img_hud_debug))
                    cv2.imshow(NOME_JANELA_PROD, painel_final)
                    cv2.waitKey(1)

            time.sleep(0.04)

if __name__ == "__main__":
    executar()
