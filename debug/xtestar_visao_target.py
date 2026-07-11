import os
import time
import cv2
import mss
import numpy as np

# ==========================================
# 1. SETUP DE MÚLTIPLOS ALVOS
# ==========================================
MONITOR_PANEL = {"top": 200, "left": 50, "width": 1000, "height": 1200}
THRESHOLD_ORIGINAL = 0.85

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_imagens = os.path.join(diretorio_atual, '..\\images')

# Dicionário com os alvos que queres vigiar
alvos = {
    'STATION': os.path.join(pasta_imagens, 'STATION.png'),
    'CARRIER': os.path.join(pasta_imagens, 'FLEET_CARRIER_NAME.png')
}

templates = {}
print(f"[SISTEMA] A carregar templates para auditoria visual...")

for nome, caminho in alvos.items():
    try:
        templates[nome] = cv2.imread(caminho, cv2.IMREAD_COLOR)
        print(f" -> {nome} carregado com sucesso.")
    except Exception as e:
        print(f"[ERRO] Não consegui carregar {nome}: {e}")
        exit()

# ==========================================
# 2. MOTOR DE VISÃO MULTI-ALVO
# ==========================================
with mss.mss() as sct:
    print("\n>>> OCULAR DE AUDITORIA ATIVADA (STATION + CARRIER).")
    print(">>> Pressiona 'q' para sair.")
    
    while True:
        # Captura uma única vez por ciclo para performance
        img_bgra = np.array(sct.grab(MONITOR_PANEL))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        
        # Desenha um fundo para o log de texto
        cv2.rectangle(img_bgr, (0, 0), (500, 100), (0, 0, 0), -1)

        # Processa cada alvo
        y_offset = 30
        for nome, template in templates.items():
            res = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            h, w = template.shape[:2]
            
            # Escolha de cor baseada no sucesso
            if max_val >= THRESHOLD_ORIGINAL:
                cor = (0, 255, 0) # Verde
                estado = "DETECTADO"
            else:
                cor = (0, 0, 255) # Vermelho
                estado = "AUSENTE"
            
            # Desenha retângulo e texto
            cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            cv2.putText(img_bgr, f"{nome}: {estado} ({max_val*100:.1f}%)", (10, y_offset), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, cor, 2)
            y_offset += 40

        cv2.imshow("Ocular Multi-Alvo", img_bgr)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

cv2.destroyAllWindows()