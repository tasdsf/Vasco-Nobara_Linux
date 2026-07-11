import os
import time
import cv2
#from infra_bridge import pydirectinput, gw, winsound, mss
#import mss
import numpy as np

# ==========================================
# 0. INFRAESTRUTURA: JANELA NO 2º ECRÃ
# ==========================================
NOME_JANELA = "Ocular de Auditoria Dual"

def inicializar_infraestrutura():
    """ Cria a janela e envia para o 2º monitor se existir """
    cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
    with mss.MSS() as sct:
        monitores = sct.monitors
        # Se houver segundo monitor, move para lá
        if len(monitores) > 2:
            ecra_secundario = monitores[2]
            cv2.moveWindow(NOME_JANELA, ecra_secundario["left"] + 50, ecra_secundario["top"] + 50)
        else:
            cv2.moveWindow(NOME_JANELA, 50, 50)
        cv2.setWindowProperty(NOME_JANELA, cv2.WND_PROP_TOPMOST, 1)

# ==========================================
# 1. SETUP DE MÚLTIPLOS ALVOS
# ==========================================
MONITOR_PANEL = {"top": 200, "left": 50, "width": 1000, "height": 800}
THRESHOLD = 0.80 # Ajusta conforme necessário

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_imagens = os.path.join(diretorio_atual, '../images')

# Dicionário com os alvos a vigiar
alvos = {
    'STATION': 'STATION.png',
    'CARRIER': 'FLEET_CARRIER_NAME.png'
}

templates = {}
print(f"[SISTEMA] A carregar alvos visuais para auditoria...")
for nome, ficheiro in alvos.items():
    caminho = os.path.join(pasta_imagens, ficheiro)
    img = cv2.imread(caminho, cv2.IMREAD_COLOR)
    if img is None:
        print(f"[ERRO] Não consegui carregar {ficheiro}")
        exit()
    templates[nome] = img

# ==========================================
# 2. MOTOR DE AUDITORIA SIMULTÂNEA
# ==========================================
inicializar_infraestrutura()
print("\n>>> OCULAR DE AUDITORIA DUAL ATIVADA.")
print(">>> A detetar STATION e CARRIER em simultâneo.")

with mss.MSS() as sct:
    while True:
        img_bgra = np.array(sct.grab(MONITOR_PANEL))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

        # Injeta esta linha para gravar o frame real para o teu disco:
        cv2.imwrite("debug_visao_real.png", img_bgr)
        
        # Fundo para os textos
        cv2.rectangle(img_bgr, (0, 0), (600, 150), (0, 0, 0), -1)
        
        y_offset = 30
        for nome, template in templates.items():
            res = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            h, w = template.shape[:2]
            
            # Verde se detetado, Vermelho se ausente
            if max_val >= THRESHOLD:
                cor = (0, 255, 0)
                estado = "DETECTADO"
            else:
                cor = (0, 0, 255)
                estado = "AUSENTE"
            
            # Desenha a caixa de deteção (mesmo se falhar, desenha onde o match foi mais alto)
            cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            
            # Escreve o estado
            cv2.putText(img_bgr, f"{nome}: {estado} ({max_val*100:.1f}%)", (10, y_offset), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, cor, 2)
            y_offset += 40

        cv2.imshow(NOME_JANELA, img_bgr)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

cv2.destroyAllWindows()
