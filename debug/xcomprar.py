import os
import time
import pydirectinput
import cv2
import mss
import numpy as np

# ==========================================
# 0. INFRAESTRUTURA: FOCO E MULTI-MONITOR
# ==========================================
NOME_JANELA = "Ocular do Bot - Diagnostico"

def inicializar_infraestrutura():
    """ Foca no jogo (Ecrã 1) e envia o painel visual para o Ecrã 2 """
    print("[SISTEMA] A analisar hardware e mapear monitores...")
    cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
    
    with mss.mss() as sct:
        monitores = sct.monitors
        
        # 1. Focar o jogo no monitor principal
        if len(monitores) > 1:
            monitor_principal = monitores[1]
            centro_x = monitor_principal["left"] + (monitor_principal["width"] // 2)
            centro_y = monitor_principal["top"] + (monitor_principal["height"] // 2)
            print(f"[SISTEMA] A focar no jogo (Ecrã Principal -> Clique em X:{centro_x}, Y:{centro_y})...")
            pydirectinput.click(int(centro_x), int(centro_y))
            time.sleep(0.5) # Tempo para a janela do Windows vir para a frente
            
        # 2. Enviar a janela do OpenCV para o segundo monitor
        if len(monitores) > 2:
            ecra_secundario = monitores[2]
            pos_x = ecra_secundario["left"] + 50
            pos_y = ecra_secundario["top"] + 50
            cv2.moveWindow(NOME_JANELA, pos_x, pos_y)
            print(f"[SISTEMA] Ecrã secundário detetado! Painel enviado para X: {pos_x}")
        else:
            print("[AVISO] Segundo monitor não detetado. O painel ficará no ecrã principal.")
            
        cv2.setWindowProperty(NOME_JANELA, cv2.WND_PROP_TOPMOST, 1)

# ==========================================
# 1. SETUP E CALIBRAÇÃO DE ÁREAS
# ==========================================
MONITOR_MENU = {"top": 1100, "left": 1000, "width": 600, "height": 400}
MONITOR_MARKET = {"top": 200, "left": 0, "width": 800, "height": 1400} 

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_imagens = os.path.join(diretorio_atual, 'images')

templates_nomes = {
    'servicos': 'STARPORT_SERVICES.png',
    'noselection': 'NO_SELECTION.png',
    'disembark': 'DISEMBARK.png',
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
    print(f"ERRO: {e}"); exit()

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
        
        # Debug Visual
        cor = (0, 255, 0) if encontrou else (0, 0, 255)
        if encontrou:
            h, w = template.shape[:2]
            cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
        
        cv2.putText(img_bgr, f"Alvo: {nome_label}", (10, 30), 1, 1.2, (255, 255, 255), 2)
        cv2.putText(img_bgr, f"Match: {max_val:.2f} / {threshold}", (10, 60), 1, 1.2, cor, 2)
        
        # A janela já está criada e presa no ecrã secundário pela função inicializar_infraestrutura()
        cv2.imshow(NOME_JANELA, img_bgr)
        cv2.waitKey(1)
        return encontrou

# ==========================================
# 3. FASES DE NAVEGAÇÃO
# ==========================================
def fase_1_entrar_servicos():
    print("\n>>> FASE 1: Menu Holográfico...")
    while not (procurar_template(templates['noselection'], "IDLE", MONITOR_MENU, 0.75) or 
               procurar_template(templates['disembark'], "DISEMBARK", MONITOR_MENU, 0.75) or
               procurar_template(templates['servicos'], "SERVICES", MONITOR_MENU)):
        time.sleep(0.5)
    
    time.sleep(1.2)
    for tecla in ['s', 's', 'w', 'w']:
        if procurar_template(templates['servicos'], "SERVICES", MONITOR_MENU):
            pydirectinput.press('space')
            return True
        pydirectinput.press(tecla)
        time.sleep(0.6)
    return False

def fase_2_abrir_mercado():
    print("\n>>> FASE 2: Abrindo Mercado...")
    while not (procurar_template(templates['market_off'], "MARKET OFF", MONITOR_MARKET) or 
               procurar_template(templates['market_on'], "MARKET ON", MONITOR_MARKET)):
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
    return False

def fase_3_comprar_item():
    print("\n>>> FASE 3: Abrindo Mercado...")
    while not procurar_template(templates['buy_on'], "BUY ON", MONITOR_MARKET):
        time.sleep(0.5)
        
    print("\n>>> FASE 3: Procurando Fujin Tea...")
    time.sleep(2.0)
    pydirectinput.press('d')
    time.sleep(0.5)
    
    for i in range(40): 
        if procurar_template(templates['rare_on'], "RARE FOUND", MONITOR_MARKET, 0.92):
            print(">>> ITEM DETETADO! Comprando...")
            pydirectinput.press('space')
            time.sleep(1.0)
            pydirectinput.keyDown('d')
            time.sleep(2.5)
            pydirectinput.keyUp('d')
            pydirectinput.press('s')
            time.sleep(0.5)
            pydirectinput.press('space')
            time.sleep(1.5)
            
            for _ in range(3):
                pydirectinput.press('backspace')
                time.sleep(0.8)
            return "COMPRADO"
        
        if procurar_template(templates['exit_on'], "EXIT BUTTON", MONITOR_MARKET, 0.75):
            print(">>> Fim da lista. Item não disponível.")
            return "NAO_ENCONTRADO"
            
        pydirectinput.press('s')
        time.sleep(0.4)
    return "TIMEOUT"

# ==========================================
# 4. LOOP DE PERSISTÊNCIA (20 MINUTOS MAX)
# ==========================================
def executar_ciclo_completo():
    tempo_limite = 20 * 60  
    inicio_contagem = time.time()
    
    while True:
        tempo_decorrido = time.time() - inicio_contagem
        if tempo_decorrido > tempo_limite:
            print("\n[!] TEMPO LIMITE ATINGIDO (20 min). Desistindo da compra.")
            return False

        print(f"\n--- INICIANDO TENTATIVA (Tempo decorrido: {int(tempo_decorrido/60)} min) ---")
        
        if fase_1_entrar_servicos():
            if fase_2_abrir_mercado():
                resultado = fase_3_comprar_item()
                
                if resultado == "COMPRADO":
                    print("\n>>> OPERAÇÃO CONCLUÍDA COM SUCESSO! <<<")
                    return True
                
                elif resultado == "NAO_ENCONTRADO":
                    print("\n[REPETIR] Item esgotado. Saindo e aguardando 2 minutos...")
                    pydirectinput.press('space')
                    time.sleep(1.5)
                    pydirectinput.press('backspace')
                    time.sleep(120) 
                    continue

        print("\n[ERRO] Falha na navegação. Resetando HUD em 10s...")
        pydirectinput.press('backspace')
        time.sleep(10)

if __name__ == "__main__":
    # Arranca a rotina visual e posicionamento
    inicializar_infraestrutura()
    
    print("O R2D2 assume os comandos em 2 segundos...")
    time.sleep(2)
    executar_ciclo_completo()
    cv2.destroyAllWindows()