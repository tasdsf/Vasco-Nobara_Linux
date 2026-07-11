import os
import time
import cv2
import mss
import numpy as np

# ==========================================
# 1. SETUP EXATO DO SCRIPT ORIGINAL
# ==========================================
# Esta é a área exata onde o select_target_station.py procura
MONITOR_PANEL = {"top": 200, "left": 50, "width": 1000, "height": 1200}
THRESHOLD_ORIGINAL = 0.85 # O valor que o teu script exige para aceitar o clique

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_imagens = os.path.join(diretorio_atual, '..\images')
caminho_template = os.path.join(pasta_imagens, 'STATION.png')

print(f"[SISTEMA] A iniciar Ocular de Diagnóstico...")

try:
    template = cv2.imread(caminho_template, cv2.IMREAD_COLOR)
    h_temp, w_temp = template.shape[:2]
except Exception as e:
    print(f"[ERRO] Não consegui carregar a imagem STATION.png: {e}")
    exit()

# ==========================================
# 2. MOTOR DE VISÃO (TEMPO REAL)
# ==========================================
with mss.mss() as sct:
    print("\n>>> OCULAR ATIVADA. Vai para o Elite Dangerous e abre o painel esquerdo (tecla 1).")
    print(">>> Pressiona 'q' na janela da Ocular ou CTRL+C na consola para sair.\n")
    
    while True:
        # 1. Captura APENAS a área do MONITOR_PANEL
        img_bgra = np.array(sct.grab(MONITOR_PANEL))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        
        # 2. Faz a correspondência
        resultado = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
        
        # 3. Lógica de Feedback Visual
        if max_val >= THRESHOLD_ORIGINAL:
            # SUCESSO: O script original encontraria o alvo
            cor = (0, 255, 0) # Verde
            texto_estado = f"SUCESSO! Match: {max_val*100:.1f}% (Min: 85%)"
            cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w_temp, max_loc[1] + h_temp), cor, 2)
        else:
            # FALHA: O script original ignoraria
            cor = (0, 0, 255) # Vermelho
            texto_estado = f"FALHA. Match: {max_val*100:.1f}% (Exige: 85%)"
            # Desenha a caixa onde ele acha que se parece mais, mesmo falhando
            cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w_temp, max_loc[1] + h_temp), cor, 1)

        # 4. Escreve a percentagem diretamente na imagem da Ocular
        cv2.putText(img_bgr, texto_estado, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
        cv2.putText(img_bgr, "Area: MONITOR_PANEL (1000x1200)", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        # 5. Mostra a janela (redimensionada para caber bem no teu ecrã)
        img_redimensionada = cv2.resize(img_bgr, (500, 600)) # Reduz para metade do tamanho visualmente, mas o cálculo é no tamanho original
        cv2.imshow("Ocular de Diagnostico", img_redimensionada)
        
        # Mostra também o template original isolado para poderes comparar a olho nu
        cv2.imshow("A tua STATION.png original", template)
        
        if cv2.waitKey(100) & 0xFF == ord('q'):
            break

cv2.destroyAllWindows()