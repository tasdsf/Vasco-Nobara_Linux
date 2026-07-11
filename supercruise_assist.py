#!/usr/bin/env python3
"""
Supercruise Assist - Módulo Unificado (Mecânica Ótica + Telemetria)
Elite Dangerous Automation
"""

import os
import sys
import json
import time
import logging
import cv2
import numpy as np
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss
import time

# ==========================================
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
IMAGES_DIR = os.path.join(PROJECT_DIR, "images")
os.makedirs(LOGS_DIR, exist_ok=True)

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("supercruise")
_logger.setLevel(logging.ERROR)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(LOGS_DIR, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [SUPERCRUISE] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

def abortar_com_erro(mensagem):
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    sys.exit(1)

# Voz
engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

NOME_JANELA = "Ocular do Bot - Supercruise"
VISUAL_DEBUG = False

def inicializar_infraestrutura():
    print("[SISTEMA] A configurar foco no jogo...")
    
    focar_jogo_seguro()
    time.sleep(0.5)
            
    if VISUAL_DEBUG:
        cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
        with mss.mss() as sct:
            monitores = sct.monitors
            if len(monitores) > 2:
                ecra_secundario = monitores[2]
                cv2.moveWindow(NOME_JANELA, ecra_secundario["left"] + 50, ecra_secundario["top"] + 50)
            else:
                cv2.moveWindow(NOME_JANELA, 50, 50)
            cv2.setWindowProperty(NOME_JANELA, cv2.WND_PROP_TOPMOST, 1)

def focar_jogo_seguro():
    """ Foca o Elite Dangerous a nível de Sistema Operativo, sem enviar cliques de rato """
    print("[SISTEMA] A focar o Elite Dangerous via Windows API...")
    try:
        # Procura a janela pelo título (no Elite geralmente é "Elite - Dangerous (CLIENT)")
        janelas = gw.getWindowsWithTitle("Elite - Dangerous (CLIENT)")
        
        if janelas:
            janela_elite = janelas[0]
            # Traz a janela para a frente
            janela_elite.activate() 
            time.sleep(0.5)
            print("[OK] Jogo focado com sucesso e em segurança.")
            return True
        else:
            print("[ERRO] Janela do Elite Dangerous não encontrada!")
            return False
            
    except Exception as e:
        print(f"[AVISO] Falha ao forçar foco via OS: {e}")
        return False

# ==========================================
# 1. SETUP (TEMPLATES E TELEMETRIA)
# ==========================================
MONITOR_CENTER = {"top": 100, "left": 400, "width": 1100, "height": 800}
MONITOR_PANEL = {"top": 200, "left": 50, "width": 1000, "height": 1200}

STATUS_FLAGS = {
    "SUPERCRUISE": 0x10,
    "FSD_MASS_LOCKED": 0x10000,
    "FSD_CHARGING": 0x20000,
    "HARDPOINTS_DEPLOYED": 0x40,
    "INTERDICTION": 0x800000,
}

TEMPLATES_NOMES = {
    'nav_tab': 'NAVIGATION_SELECTED.png',
    'charging': 'CHARGING.png',
    'locked': 'LOCKED_DESTINATION.png',
    'unlocked': 'UNLOCKED_DESTINATION.png',
    'assist_active': 'SUPERCRUISE_ASSIST_ACTIVE.png',
    'align_warning': 'SUPERCRUISE_ASSIST_INACTIVE.png',
    'throttle_up': 'THROTTLE_UP.png'
}

templates = {}
try:
    from infra_bridge import ED_STATUS_FILE, ED_LOG_DIR
    STATUS_FILE = ED_STATUS_FILE
    LOG_DIR = ED_LOG_DIR
    for chave, nome_arq in TEMPLATES_NOMES.items():
        caminho = os.path.join(IMAGES_DIR, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta imagem: {caminho}")
        templates[chave] = img
    print("[SISTEMA] Módulo Unified Supercruise carregado.")
except Exception as e:
    abortar_com_erro(f"Erro ao carregar templates visuais: {e}")

# ==========================================
# 2. MOTORES CORE (VISÃO E DADOS)
# ==========================================
def procurar_template(template, nome_label, monitor, threshold=0.75, log_trace=False):
    with mss.mss() as sct:
        img_bgra = np.array(sct.grab(monitor))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        res = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        encontrou = max_val >= threshold

        if log_trace:
            print(f"[TRACE] {nome_label}: match={max_val:.3f} threshold={threshold:.2f} -> {'OK' if encontrou else '--'}")

        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            cv2.putText(img_bgr, f"{nome_label}: {max_val:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
            cv2.imshow(NOME_JANELA, img_bgr)
            cv2.waitKey(1)
            
        return encontrou

def ler_telemetria():
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("Flags", 0)
    except:
        return 0

# ==========================================
# 3. FASE 0: SALTO E TELEMETRIA
# ==========================================
def iniciar_salto_seguro():
    print("\n>>> FASE 0: Iniciar Salto (J)...")
    pydirectinput.press('j')

    # Watchdog: Confirmar CHARGING
    contagem_limpo = 0
    while contagem_limpo < 15:
        if procurar_template(templates['charging'], "CHARGING", MONITOR_CENTER, 0.85):
            contagem_limpo += 1
            print(f">>> FASE 1: Charging {contagem_limpo}s")
            time.sleep(1)
        else:
            break

    # FASE 2: acelerar a 100% para o salto. Substitui a antiga rotina de
    # impulsos repetidos na tecla '.' (com deteção do aviso THROTTLE UP,
    # cooldown de inércia, etc.) por um único press em 'right shift', que no
    # jogo salta diretamente para 100% de aceleração -- muito mais simples e
    # sem depender de deteção visual nenhuma. Confirmado com
    # debug/testar_rightshift.py.
    print(">>> FASE 2: A acelerar a 100% (right shift)...")
    pydirectinput.press('rightshift')

    # Deteção visual do THROTTLE UP a desaparecer não é fiável aqui: a
    # inércia da nave faz o símbolo sair do sítio/ampliar, o que se confunde
    # com "desapareceu". Sem telemetria de velocidade disponível no
    # Status.json, ficamos com uma espera fixa mais generosa (5s -> 8s) para
    # dar tempo à nave de realmente atingir 100% antes de cortar o throttle.
    time.sleep(8)


    print("[TELEMETRIA] A validar telemetria do FSD ...")
    flags = ler_telemetria()
    
    # 1. Verifica se está a carregar
    if bool(flags & STATUS_FLAGS["FSD_CHARGING"]):
        print("[OK] Motor FSD em carga confirmada pela telemetria.")
        # Espera o salto acontecer (ex: 8s)
        pydirectinput.press('x')
        time.sleep(4.5)
        return True
        
    pydirectinput.press('x')
    
    # 2. Se não está a carregar, usa as regras do Hermes para descobrir o porquê
    if bool(flags & STATUS_FLAGS["FSD_MASS_LOCKED"]):
        pydirectinput.press('x') 
        abortar_com_erro("Nave bloqueada por Mass Lock da estação/planeta.")
    
    if bool(flags & STATUS_FLAGS["HARDPOINTS_DEPLOYED"]):
        pydirectinput.press('x') 
        abortar_com_erro("Armas ou Trem de Aterragem abertos. Impossível saltar.")
        
    if procurar_template(templates['align_warning'], "ALIGN", MONITOR_CENTER, 0.82):
        pydirectinput.press('x') 
        abortar_com_erro("Vetor de proa totalmente desalinhado do destino.")
        
    pydirectinput.press('x')
    abortar_com_erro("Destino fisicamente obstruído por corpo celeste (Planeta/Estrela) nas costas.")

def aguardar_supercruise_confirmado(timeout=30):
    """Confirma pela telemetria (Status.json, flag SUPERCRUISE=0x10) que já
    estamos mesmo em supercruise antes de tentar ativar o assist no menu --
    sem isto, o menu pode ser aberto ainda em espaco normal/transicao."""
    print("\n>>> A confirmar entrada em Supercruise pela telemetria...")
    limite = time.time() + timeout
    while time.time() < limite:
        flags = ler_telemetria()
        if bool(flags & STATUS_FLAGS["SUPERCRUISE"]):
            print("[OK] Supercruise confirmado pela telemetria.")
            return True
        time.sleep(0.5)

    abortar_com_erro(f"Timeout ({timeout}s). Supercruise nunca foi confirmado pela telemetria.")

# ==========================================
# 4. FASE 1: NAVEGAÇÃO MECÂNICA NO MENU
# ==========================================
def engatar_assistencia_menu():
    print("\n>>> FASE 1: Navegação no Painel Esquerdo...")
    
    # Reduzir velocidade antes de mexer nos menus
    pydirectinput.press('1')
    time.sleep(1)
    pydirectinput.press('x')

    # 1. Achar a ABA NAV
    nav_found = False
    for _ in range(6):
        if procurar_template(templates['nav_tab'], "NAV TAB", MONITOR_PANEL, 0.61):
            nav_found = True
            break
        pydirectinput.press('q')
        time.sleep(0.5)
        
    if not nav_found:
        pydirectinput.press('x') 
        pydirectinput.press('1')
        abortar_com_erro("Falha crítica ao tentar focar na aba de navegação.")

    # 2. A lógica que notaste faltar: SPACE -> D -> SPACE
    print(">>> Focando no destino pré-selecionado (Space)...")
    pydirectinput.press('space')
    time.sleep(0.8)
    
    print(">>> Movendo para o botão Supercruise Assist (D)...")
    pydirectinput.press('d')
    time.sleep(0.5)
    
    print(">>> Ativando Assistência (Space)...")
    pydirectinput.press('space')
    time.sleep(1.0)
    
    pydirectinput.press('1') # Fecha painel
    print(">>> Painel fechado. Voltando ao Cockpit.")

# ==========================================
# 5. FASE 2: VIAGEM E CHEGADA
# ==========================================
def monitorar_viagem():
    print("\n>>> FASE 2: Viagem em Supercruise...")

    # Watchdog: Confirmar HUD
    contagem_limpo = 0
    timeout_assist = time.time() + 60
    while contagem_limpo < 3:
        if time.time() > timeout_assist:
            abortar_com_erro("Timeout (60s). Supercruise Assist não apareceu no HUD.")
        if not procurar_template(templates['assist_active'], "ASSIST ACTIVE", MONITOR_CENTER, 0.71):
            contagem_limpo = 0
        else:
            contagem_limpo += 1
        time.sleep(1)

    # Só agora e que o assist esta confirmado visualmente -- so agora anuncia.
    falar("Supercruise assist engaged. Monitoring flight path.")

    # Viagem Longa
    print("A aguardar que o aviso de Assist desapareça (Chegada)...")
    contagem_limpo = 0
    timeout_viagem = time.time() + 1500 # 25 mins
    
    while contagem_limpo < 3:
        flags = ler_telemetria()
        if bool(flags & STATUS_FLAGS["INTERDICTION"]):
            abortar_com_erro("ALERTA CRÍTICO: Interdição detetada durante viagem!")
            
        if time.time() > timeout_viagem:
            abortar_com_erro("Timeout (25 mins). Viagem em supercruise excedeu o limite seguro.")

        if procurar_template(templates['assist_active'], "ASSIST ACTIVE", MONITOR_CENTER, 0.71):
            contagem_limpo = 0 
        else:
            contagem_limpo += 1 
        time.sleep(1)

    # Chegada
    print("\n>>> CHEGADA CONFIRMADA! A executar travagem e boost...")
    falar("Dropping from supercruise.")
    
    time.sleep(2.0)
    pydirectinput.press('tab') 
    time.sleep(15.0)
    # pydirectinput.press('tab') 
    # time.sleep(15.0)
    pydirectinput.press('x') 
    print(">>> Manobra concluída. A aguardar aproximação para docking.")

def executar():
    inicializar_infraestrutura()

    print("Alinha o nariz da nave com o destino. Iniciando em 1s...")
    time.sleep(1)

    iniciar_salto_seguro()
    aguardar_supercruise_confirmado()
    engatar_assistencia_menu()
    monitorar_viagem()

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
