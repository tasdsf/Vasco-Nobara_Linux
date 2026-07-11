import os
import sys
import time
import logging
import cv2
import numpy as np
from infra_bridge import pydirectinput, gw, winsound, mss
import time

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
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_logs = os.path.join(diretorio_atual, "logs")
os.makedirs(pasta_logs, exist_ok=True)

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("comprar")
_logger.setLevel(logging.ERROR)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [COMPRAR] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

def abortar_com_erro(mensagem):
    """ Regista o erro no log e dispara exit code 1 para o Orquestrador intercetar """
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    for _ in range(3):
        pydirectinput.press('backspace')
        time.sleep(0.8)
    sys.exit(1)

NOME_JANELA = "Ocular do Bot - Diagnostico"
VISUAL_DEBUG = False # Muda para False para esconder as janelas

def inicializar_infraestrutura():
    """ Foca no jogo (Ecrã 1) e envia o painel visual para o Ecrã 2 """
    print("[SISTEMA] A focar no jogo...")
    
    focar_jogo_seguro()
    time.sleep(0.5)
            
    if VISUAL_DEBUG:
        print("[SISTEMA] A configurar janelas de diagnóstico...")
        cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
        with mss.mss() as sct:
            monitores = sct.monitors
            if len(monitores) > 2:
                ecra_secundario = monitores[2]
                pos_x = ecra_secundario["left"] + 50
                pos_y = ecra_secundario["top"] + 50
                cv2.moveWindow(NOME_JANELA, pos_x, pos_y)
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
# 1. SETUP E CALIBRAÇÃO DE ÁREAS
# ==========================================
MONITOR_MENU = {"top": 770, "left": 800, "width": 330, "height": 300}
MONITOR_MARKET = {"top": 190, "left": 70, "width": 530, "height": 860}

pasta_imagens = os.path.join(diretorio_atual, 'images')

templates_nomes = {
    'servicos': 'STARPORT_SERVICES.png',
    'noselection': 'NO_SELECTION.png',
    'market_off': 'COMMODITIES_MARKET_OFF.png',
    'market_on': 'COMMODITIES_MARKET_ON.png',
    'buy_on': 'BUY_SELECTED.png',
    'rare_on': 'RARE_SELECTED.png',
    'exit_on': 'EXIT_SELECTED.png'
}

templates = {}
try:
    for chave, nome_arq in templates_nomes.items():
        caminho = os.path.join(pasta_imagens, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta imagem: {caminho}")
        templates[chave] = img
    print(f"[SISTEMA] {len(templates)} templates carregados!")
except Exception as e:
    abortar_com_erro(f"Falha ao carregar imagens para a memória: {e}")

# ==========================================
# 2. MOTOR DE VISÃO
# ==========================================
def procurar_template(template, nome_label, monitor, threshold=0.80):
    with mss.mss() as sct:
        img_bgra = np.array(sct.grab(monitor))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        resultado = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
        encontrou = max_val >= threshold

        print(f"[TRACE] {nome_label}: match={max_val:.3f} threshold={threshold:.2f} loc={max_loc} -> {'OK' if encontrou else '--'}")

        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            
            cv2.rectangle(img_bgr, (5, 5), (450, 80), (0, 0, 0), -1)
            cv2.putText(img_bgr, f"Alvo: {nome_label}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(img_bgr, f"Match: {max_val:.2f} / {threshold}", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
            
            cv2.imshow(NOME_JANELA, img_bgr)
            cv2.waitKey(1)
        
        return encontrou

# ==========================================
# 3. FASES DE NAVEGAÇÃO COM WATCHDOGS
# ==========================================
def fase_1_entrar_servicos():
    print("\n>>> FASE 1: Menu Holográfico...")
    timeout = time.time() + 15
    while not (procurar_template(templates['noselection'], "IDLE", MONITOR_MENU, 0.72) or
               procurar_template(templates['servicos'], "SERVICES", MONITOR_MENU, 0.70)):
        
        if time.time() > timeout:
            abortar_com_erro("Timeout (15s) à espera que o painel principal da estação estabilize.")
        time.sleep(0.5)
    
    time.sleep(1.2)
    for tecla in ['s', 's', 'w', 'w']:
        if procurar_template(templates['servicos'], "SERVICES", MONITOR_MENU, 0.70):
            pydirectinput.press('space')
            return True
        pydirectinput.press(tecla)
        time.sleep(0.6)
        
    abortar_com_erro("Botão 'Starport Services' não detetado após a sequência mecânica de focagem.")

def fase_2_abrir_mercado():
    print("\n>>> FASE 2: Abrindo Mercado...")
    timeout = time.time() + 15
    while not (procurar_template(templates['market_off'], "MARKET OFF", MONITOR_MARKET) or 
               procurar_template(templates['market_on'], "MARKET ON", MONITOR_MARKET)):
        
        if time.time() > timeout:
            abortar_com_erro("Timeout (15s) à espera que a interface interna dos Serviços carregue.")
        time.sleep(0.5)

    for tecla in ['s', 's', 'w', 'w']:
        if procurar_template(templates['market_on'], "MARKET ON", MONITOR_MARKET):
            print("\n>>> FASE 2 : Market on, will be selected")
            time.sleep(1)
            pydirectinput.press('space')
            print("\n>>> FASE 2 : Market selected")
            return True
        pydirectinput.press(tecla)
        time.sleep(2.6)
        
    abortar_com_erro("Botão 'Commodities Market' não detetado na lista após varrimento mecânico.")
    pydirectinput.press('backspace')

def fase_3_comprar_item():
    print("\n>>> FASE 3: Acedendo ao Mercado...")
    timeout = time.time() + 15
    while not procurar_template(templates['buy_on'], "BUY ON", MONITOR_MARKET):
        if time.time() > timeout:
            abortar_com_erro("Timeout (15s) à espera que o menu do Mercado estabeleça o botão BUY.")
        time.sleep(0.5)
        
    print("\n>>> FASE 3: Procurando rare goods...")
    time.sleep(2.0)
    pydirectinput.press('d')
    time.sleep(0.5)
    
    for i in range(40): 
        if procurar_template(templates['rare_on'], "RARE FOUND", MONITOR_MARKET, 0.80):
            print(">>> ITEM DETETADO! Comprando...")
            pydirectinput.press('space')
            time.sleep(1.0)
            pydirectinput.keyDown('d')
            time.sleep(2.5)
            pydirectinput.keyUp('d')
            time.sleep(1.0)
            pydirectinput.press('s')
            print("\n[DEBUG] desceu <s>")
            time.sleep(1.0)
            pydirectinput.press('space')
            print("\n[DEBUG] comprou <space>")
            time.sleep(2.0)
            
            for _ in range(3):
                pydirectinput.press('backspace')
                time.sleep(0.8)
            return "COMPRADO"
        
        # Este template é válido de falhar se o item estiver esgotado (lógica de negócio normal)
        if procurar_template(templates['exit_on'], "EXIT BUTTON", MONITOR_MARKET, 0.75):
            print(">>> Fim da lista. Item não disponível no momento.")
            return "NAO_ENCONTRADO"
            
        pydirectinput.press('s')
        time.sleep(0.5)
        
    abortar_com_erro("Passou 40 iterações na lista sem encontrar o RARE nem o botão de EXIT. Bot perdeu-se.")

# ==========================================
# 4. LOOP DE PERSISTÊNCIA (20 MINUTOS MAX)
# ==========================================
def executar_ciclo_completo():
    tempo_limite = 20 * 60  
    inicio_contagem = time.time()
    
    while True:
        tempo_decorrido = time.time() - inicio_contagem
        if tempo_decorrido > tempo_limite:
            abortar_com_erro("Tempo limite operacional da rotina de compra (20 min) excedido.")

        print(f"\n--- INICIANDO TENTATIVA (Tempo decorrido: {int(tempo_decorrido/60)} min) ---")
        
        # A lógica passa automaticamente para a frente ou morre de imediato por erro nas fases
        if fase_1_entrar_servicos():
            if fase_2_abrir_mercado():
                resultado = fase_3_comprar_item()
                
                if resultado == "COMPRADO":
                    print("\n>>> OPERAÇÃO CONCLUÍDA COM SUCESSO! <<<")
                    return True
                
                elif resultado == "NAO_ENCONTRADO":
                    print("\n[REPETIR] Item esgotado. Saindo e aguardando 2 minutos...")
                    # Clica no Exit que já está selecionado
                    pydirectinput.press('space')
                    time.sleep(1.5)
                    # Garante que volta ao Cockpit para resetar menus -- mesmos
                    # 3x backspace do caminho "COMPRADO" acima; 1x não chegava
                    # para desfazer Commodities Market -> BUY tab -> lista.
                    for _ in range(3):
                        pydirectinput.press('backspace')
                        time.sleep(0.8)

                    time.sleep(120) # 2 Minutos
                    continue

def executar():
    inicializar_infraestrutura()

    print("O R2D2 assume os comandos em 1 segundos...")
    time.sleep(1)
    executar_ciclo_completo()

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
