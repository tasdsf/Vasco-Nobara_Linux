import os
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from infra_bridge import mss, keyboard

# ==========================================
# 1. SETUP DE CONFIGURAÇÃO — mesmas áreas do undocking.py atual
# ==========================================
GEO_AREAS = {
    "menu": {"top": 750, "left": 800, "width": 300, "height": 300},
    "corner": {"top": 50, "left": 1400, "width": 500, "height": 300},
}

ALVOS = {
    'AUTO_LAUNCH': {
        'ficheiro': 'AUTO_LAUNCH.png',
        'threshold': 0.85,
        'cor': (255, 0, 0),
        'area_id': 'menu'
    },
    'NO_SELECTION': {
        'ficheiro': 'NO_SELECTION.png',
        'threshold': 0.85,
        'cor': (0, 255, 255),
        'area_id': 'menu'
    },
    'REPAIR': {
        'ficheiro': 'repair.png',
        'threshold': 0.85,
        'cor': (0, 255, 0),
        'area_id': 'menu'
    },
    'COMPLETE': {
        'ficheiro': 'AUTO_LAUNCH_COMPLETE.png',
        'threshold': 0.70,
        'cor': (0, 0, 255),
        'area_id': 'corner'
    }
}

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_imagens = os.path.join(diretorio_atual, '../images')
log_test = os.path.join(diretorio_atual, '../logs/testar_visao_undocking.png')

print("[SISTEMA] A carregar templates...")
for nome_alvo, dados in ALVOS.items():
    caminho = os.path.join(pasta_imagens, dados['ficheiro'])
    img = cv2.imread(caminho, cv2.IMREAD_COLOR)
    if img is not None:
        dados['template'] = img
        dados['h'], dados['w'] = img.shape[:2]
    else:
        print(f" -> [ERRO] Não encontrei o ficheiro: {dados['ficheiro']}")
        sys.exit(1)

# CRÍTICO: a sessão PipeWire (e o eventual popup do KDE) tem de arrancar ANTES
# de qualquer janela cv2 (namedWindow/imshow) — senão o Qt do cv2 colide com o
# main loop do GLib do dbus-python e o CreateSession nunca responde.
print("[SISTEMA] A iniciar sessão de captura...")
with mss.mss() as sct:
    sct.grab({"top": 0, "left": 0, "width": 50, "height": 50})

NOME_MENU = "R2D2 - Visao do Menu"
NOME_CORNER = "R2D2 - Visao do Canto Superior"
cv2.namedWindow(NOME_MENU, cv2.WINDOW_NORMAL)
cv2.namedWindow(NOME_CORNER, cv2.WINDOW_NORMAL)
# Monitor ultrawide 3840x1080: o jogo ocupa a metade esquerda (0-1920px),
# por isso as janelas de debug vão para a metade direita, fora do jogo.
cv2.moveWindow(NOME_MENU, 1950, 50)
cv2.moveWindow(NOME_CORNER, 1950, 400)

print("[SISTEMA] Pronto. 'q' fecha.")

# ==========================================
# 2. LOOP DE EXECUÇÃO
# ==========================================
with mss.mss() as sct:
    while True:
        if keyboard.is_pressed('q'):
            print("[SISTEMA] A encerrar...")
            break

        img_menu_raw = np.array(sct.grab(GEO_AREAS["menu"]))
        img_menu_bgr = cv2.cvtColor(img_menu_raw, cv2.COLOR_BGRA2BGR)
        cv2.imwrite(log_test, img_menu_bgr)

        img_corner_raw = np.array(sct.grab(GEO_AREAS["corner"]))

        frames = {
            "menu": img_menu_bgr.copy(),
            "corner": cv2.cvtColor(img_corner_raw, cv2.COLOR_BGRA2BGR)
        }

        cv2.rectangle(frames["menu"], (5, 5), (450, 85), (0, 0, 0), -1)
        cv2.rectangle(frames["corner"], (5, 5), (450, 55), (0, 0, 0), -1)

        y_texto_menu = 30
        y_texto_center = 30

        for nome_alvo, dados in ALVOS.items():
            area_alvo = dados['area_id']
            frame_trabalho = frames[area_alvo]

            res = cv2.matchTemplate(frame_trabalho, dados['template'], cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            percentagem = max_val * 100
            print(f"[TRACE] {nome_alvo}: match={max_val:.3f} threshold={dados['threshold']:.2f} loc={max_loc}")

            if max_val >= dados['threshold']:
                cor_render = dados['cor']
                texto = f"[{nome_alvo}]: OK ({percentagem:.1f}%)"
                espessura = 2
                cv2.rectangle(frame_trabalho, max_loc, (max_loc[0] + dados['w'], max_loc[1] + dados['h']), cor_render, espessura)
            else:
                cor_render = (50, 50, 130)
                texto = f"[{nome_alvo}]: Falha ({percentagem:.1f}% / Min: {dados['threshold']*100:.0f}%)"
                espessura = 1

            if area_alvo == "menu":
                cv2.putText(frames["menu"], texto, (10, y_texto_menu), cv2.FONT_HERSHEY_SIMPLEX, 0.55, cor_render, 1)
                y_texto_menu += 25
            else:
                cv2.putText(frames["corner"], texto, (10, y_texto_center), cv2.FONT_HERSHEY_SIMPLEX, 0.55, cor_render, 1)
                y_texto_center += 25

        menu_show = cv2.resize(frames["menu"], (450, 300))
        center_show = cv2.resize(frames["corner"], (550, 300))

        cv2.imshow(NOME_MENU, menu_show)
        cv2.imshow(NOME_CORNER, center_show)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

cv2.destroyAllWindows()
