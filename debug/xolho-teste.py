import os
import json
import cv2
import mss
import numpy as np
import time

# ==========================================
# 1. SETUP DE ÁREAS
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
caminho_memoria = os.path.join(diretorio_atual, "../memoria_bussola.json")
caminho_coordenadas = os.path.join(diretorio_atual, "../coordenadas_bussola.json")

# Calibrações baseadas no novo olho.py
MONITOR_HUD = {"top": 500, "left":950, "width": 700, "height": 700}
DEAD_ZONE_HUD = 25

try:
    with open(caminho_memoria, "r") as f: memoria = json.load(f)
    with open(caminho_coordenadas, "r") as f: cfg = json.load(f)
    MONITOR_BUSSOLA = cfg["MONITOR_CONFIG"]
    CX_NEUTRO = cfg["CX_NEUTRO"]
    CY_NEUTRO = cfg["CY_NEUTRO"]
except Exception as e:
    print(f"[ERRO] Falha ao carregar JSONs da bússola: {e}")
    exit()

try:
    template_hud = cv2.imread(os.path.join(diretorio_atual, '../images', 'TARGET.png'), cv2.IMREAD_COLOR)
    if template_hud is None: raise Exception()
except:
    print("[ERRO] Falha ao carregar TARGET.png na pasta images.")
    exit()

# ==========================================
# 2. RENDERIZAÇÃO DUAL-VIEW
# ==========================================
cv2.namedWindow("Auditoria de Pontaria (HUD + Bussola)", cv2.WINDOW_NORMAL)
print("\n>>> MODO DE CALIBRAÇÃO ATIVADO <<<")
print("Vê na janela visual se a zona morta (caixa verde no HUD) é suficiente.")
print("Pressiona 'q' para sair.\n")

with mss.mss() as sct:
    try: monitor_jogo = sct.monitors[1]
    except: monitor_jogo = sct.monitors[0]
        
    area_bussola_real = {
        "top": monitor_jogo["top"] + MONITOR_BUSSOLA["top"],
        "left": monitor_jogo["left"] + MONITOR_BUSSOLA["left"],
        "width": MONITOR_BUSSOLA["width"], "height": MONITOR_BUSSOLA["height"]
    }
    
    while True:
        # 1. Captura as duas áreas
        img_bussola = cv2.cvtColor(np.array(sct.grab(area_bussola_real)), cv2.COLOR_BGRA2BGR)
        img_hud = cv2.cvtColor(np.array(sct.grab(MONITOR_HUD)), cv2.COLOR_BGRA2BGR)
        
        # 2. Processa HUD
        res_hud = cv2.matchTemplate(img_hud, template_hud, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res_hud)
        
        cx_hud = MONITOR_HUD["width"] // 2
        cy_hud = MONITOR_HUD["height"] // 2
        
        # Desenha a zona morta exigida (Centro exato)
        cv2.rectangle(img_hud, (cx_hud - DEAD_ZONE_HUD, cy_hud - DEAD_ZONE_HUD), 
                               (cx_hud + DEAD_ZONE_HUD, cy_hud + DEAD_ZONE_HUD), (255, 0, 0), 2)
        cv2.circle(img_hud, (cx_hud, cy_hud), 2, (0, 0, 255), -1)
        
        texto_estado_hud = "HUD: PERDIDO"
        cor_hud = (0, 0, 255)
        
        if max_val >= 0.70:
            h, w = template_hud.shape[:2]
            tx = max_loc[0] + (w // 2)
            ty = max_loc[1] + (h // 2)
            
            dx = tx - cx_hud
            dy = ty - cy_hud
            
            cor_hud = (0, 255, 0) if (abs(dx) <= DEAD_ZONE_HUD and abs(dy) <= DEAD_ZONE_HUD) else (0, 165, 255)
            texto_estado_hud = f"HUD: dx={dx} dy={dy} (Match: {max_val:.2f})"
            
            cv2.rectangle(img_hud, max_loc, (max_loc[0] + w, max_loc[1] + h), cor_hud, 2)
            cv2.line(img_hud, (cx_hud, cy_hud), (tx, ty), (255, 255, 255), 1)

        cv2.putText(img_hud, texto_estado_hud, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, cor_hud, 2)

        # 3. Montar a vista Dual (Aumentar a bussola para ficar legível)
        img_bussola_zoom = cv2.resize(img_bussola, (300, 300), interpolation=cv2.INTER_NEAREST)
        img_hud_resized = cv2.resize(img_hud, (600, 300)) # Igualar alturas para colar
        
        vista_final = np.hstack((img_bussola_zoom, img_hud_resized))
        
        cv2.imshow("Auditoria de Pontaria (HUD + Bussola)", vista_final)
        if cv2.waitKey(1) & 0xFF == ord('q'): break

cv2.destroyAllWindows()