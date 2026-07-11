import os
# Força o Qt embutido no cv2 a usar XWayland (xcb) em vez de tentar Wayland
# nativo — evita a colisão com o main loop do GLib do dbus-python (timers
# QBasicTimer/QObject "cannot be used/started from another thread").
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import sys
import json
import cv2
import numpy as np
import time
import glob

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from infra_bridge import mss, keyboard, ED_LOG_DIR

# ==========================================
# 1. API NATIVA DO ELITE (LEITURA DO JOURNAL)
# ==========================================
def _normalizar_nome_nave(nome_cru):
    """Mesma normalizacao usada no olho.py -- alguns eventos (Loadout,
    ShipyardSwap) so trazem o campo cru, sem versao localizada, o que produzia
    nomes como 'Cobramkv' que nao batem com a chave calibrada 'Cobra Mk V'."""
    n = nome_cru.lower()
    if "cobramk3" in n: return "Cobra Mk III"
    elif "cobramk4" in n: return "Cobra Mk IV"
    elif "cobramkv" in n: return "Cobra Mk V"
    return nome_cru.title()

def obter_modelo_nave_atual():
    try:
        caminho_logs = ED_LOG_DIR
        if not os.path.exists(caminho_logs): return "Desconhecido"
        lista_logs = glob.glob(os.path.join(caminho_logs, "Journal.*.log"))
        if not lista_logs: return "Desconhecido"

        ultimo_log = max(lista_logs, key=os.path.getmtime)
        modelo_nave = "Desconhecido"
        with open(ultimo_log, 'r', encoding='utf-8') as f:
            for linha in f:
                try:
                    log_data = json.loads(linha)
                    # ShipyardSwap usa "ShipType" (nao "Ship"); sem isto o nome
                    # fica preso a nave anterior no intervalo entre o swap e o
                    # Loadout seguinte -- mesma correcao ja aplicada ao olho.py.
                    if log_data.get("event") in ["LoadGame", "ShipyardSwap", "Loadout", "ShipyardBuy", "Location", "Commander"]:
                        if "Ship_Localised" in log_data:
                            modelo_nave = log_data["Ship_Localised"]
                        elif "Ship" in log_data:
                            modelo_nave = _normalizar_nome_nave(log_data["Ship"])
                        elif "ShipType_Localised" in log_data:
                            modelo_nave = log_data["ShipType_Localised"]
                        elif "ShipType" in log_data:
                            modelo_nave = _normalizar_nome_nave(log_data["ShipType"])
                except: continue
        return modelo_nave
    except: return "Desconhecido"
    
# ==========================================
# 2. CARREGAMENTO INICIAL DINÂMICO
# ==========================================
nave_identificada = obter_modelo_nave_atual()

top_dinamico = 1180
left_dinamico = 970
width_dinamico = 80
height_dinamico = 90
limiar_thresh = 136  

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
# CORRIGIDO: Uso de "../" para evitar erros de Escape Sequence no Windows
caminho_memoria = os.path.join(diretorio_atual, "../memoria_bussola.json")
caminho_saida_config = os.path.join(diretorio_atual, "../coordenadas_bussola.json")

NOME_PAINEL = "R2D2 - Laboratorio de Calibracao da Bussola"
NOME_REBORDO = "R2D2 - Mascara do Rebordo (HUD)"

if os.path.exists(caminho_saida_config):
    try:
        with open(caminho_saida_config, "r") as f_in:
            cfg_persistente = json.load(f_in)
        
        if nave_identificada in cfg_persistente:
            dados_nave = cfg_persistente[nave_identificada]
            mc = dados_nave["MONITOR_CONFIG"]
            top_dinamico = mc["top"]
            left_dinamico = mc["left"]
            width_dinamico = mc["width"]
            height_dinamico = mc["height"]
            limiar_thresh = dados_nave.get("LIMIAR_THRESH", 136)
            
        elif cfg_persistente.get("Nave") == nave_identificada:
            mc = cfg_persistente["MONITOR_CONFIG"]
            top_dinamico = mc["top"]
            left_dinamico = mc["left"]
            width_dinamico = mc["width"]
            height_dinamico = mc["height"]
            limiar_thresh = cfg_persistente.get("LIMIAR_THRESH", 136)
    except Exception: pass

try:
    with open(caminho_memoria, "r") as f:
        memoria = json.load(f)
except Exception: exit()

# ==========================================
# 3. INICIALIZAÇÃO E ALOCAÇÃO DE ECRÃS
# ==========================================
# CRÍTICO: cv2.namedWindow() inicializa o Qt embutido no cv2, que interfere
# com o main loop do GLib usado pelo dbus-python (assertion 'acquired_context'
# failed) e faz o CreateSession do portal nunca responder. Por isso a sessão
# PipeWire (via um grab de aquecimento) tem de arrancar ANTES de criar
# qualquer janela cv2 — mesma correção já aplicada ao olho.py.
DEAD_ZONE = 4
TOLERANCIA_CENTRO = 3.0
salvar_dados = False

def _truncar_regiao(top, left, width, height, monitor):
    """Garante que a regiao de captura nunca ultrapassa os limites do monitor
    -- calibracoes antigas guardadas (ex: top=1228 num monitor de 1080px)
    ficavam completamente fora do ecra sem isto."""
    max_w = monitor["width"]
    max_h = monitor["height"]
    width = max(10, min(width, max_w))
    height = max(10, min(height, max_h))
    top = max(0, min(top, max_h - height))
    left = max(0, min(left, max_w - width))
    return top, left, width, height

with mss.mss() as sct:
    monitors = sct.monitors
    try: monitor_jogo = monitors[1]
    except IndexError: monitor_jogo = monitors[0]

    top_dinamico, left_dinamico, width_dinamico, height_dinamico = _truncar_regiao(
        top_dinamico, left_dinamico, width_dinamico, height_dinamico, monitor_jogo
    )

    sct.grab({"top": 0, "left": 0, "width": 50, "height": 50})

    cv2.namedWindow(NOME_PAINEL, cv2.WINDOW_AUTOSIZE)
    cv2.namedWindow(NOME_REBORDO, cv2.WINDOW_AUTOSIZE)

    print(f"\n==================================================")
    print(f">>> LAB AUTOMÁTICO ONLINE | NAVE: '{nave_identificada}'")
    print("==================================================\n")

    if len(monitors) > 2:
        cv2.moveWindow(NOME_PAINEL, monitors[2]["left"] + 50, monitors[2]["top"] + 50)
        cv2.moveWindow(NOME_REBORDO, monitors[2]["left"] + 880, monitors[2]["top"] + 50)
    else:
        # Monitor ultrawide 3840x1080: o jogo ocupa a metade esquerda (0-1920px),
        # por isso as janelas de debug vão para a metade direita, fora do jogo.
        cv2.moveWindow(NOME_PAINEL, 1950, 50)
        cv2.moveWindow(NOME_REBORDO, 2830, 50)

    centro_real_x = width_dinamico // 2
    centro_real_y = height_dinamico // 2

    # ==========================================
    # 4. LOOP DE PROCESSAMENTO ÓTICO
    # ==========================================
    while True:
        estado_acao = "SISTEMA: A aguardar interacção do Piloto..."

        if keyboard.is_pressed('up'): top_dinamico -= 1; time.sleep(0.04)
        elif keyboard.is_pressed('down'): top_dinamico += 1; time.sleep(0.04)
        elif keyboard.is_pressed('left'): left_dinamico -= 1; time.sleep(0.04)
        elif keyboard.is_pressed('right'): left_dinamico += 1; time.sleep(0.04)
            
        # Nomes de tecla reais do evdev: tecla '+' física = KEY_EQUAL (sem shift),
        # '-' física = KEY_MINUS; "page up/down" não têm espaço (KEY_PAGEUP/KEY_PAGEDOWN)
        if keyboard.is_pressed('equal') or keyboard.is_pressed('kpplus'):
            width_dinamico += 2; height_dinamico += 2; time.sleep(0.04)
        elif keyboard.is_pressed('minus') or keyboard.is_pressed('kpminus'):
            if width_dinamico > 40 and height_dinamico > 40:
                width_dinamico -= 2; height_dinamico -= 2; time.sleep(0.04)

        if keyboard.is_pressed('pageup'):
            if limiar_thresh < 254: limiar_thresh += 2; time.sleep(0.04)
        elif keyboard.is_pressed('pagedown'):
            if limiar_thresh > 2: limiar_thresh -= 2; time.sleep(0.04)

        top_dinamico, left_dinamico, width_dinamico, height_dinamico = _truncar_regiao(
            top_dinamico, left_dinamico, width_dinamico, height_dinamico, monitor_jogo
        )

        if keyboard.is_pressed('s'):
            print("\n[ENCERRAMENTO] A salvar centro ótico real da bússola...")
            salvar_dados = True
            break
            
        if keyboard.is_pressed('q'):
            salvar_dados = False
            break

        cx_neutro = width_dinamico // 2
        cy_neutro = height_dinamico // 2

        area_real = {
            "top": monitor_jogo["top"] + top_dinamico,
            "left": monitor_jogo["left"] + left_dinamico,
            "width": width_dinamico,
            "height": height_dinamico
        }

        try:
            img_bgra = np.array(sct.grab(area_real))
            img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
            if img_bgr.size == 0: raise ValueError("área de captura fora do ecrã")
        except Exception:
            # Área ainda fora do ecrã (normal ao ajustar uma nave nova) — mostra
            # um placeholder preto do tamanho certo para o painel de coordenadas
            # continuar visível enquanto navegas de volta para a área visível.
            estado_acao = "AVISO: área de captura fora do ecrã (ajusta com as setas)"
            img_bgr = np.zeros((max(height_dinamico, 1), max(width_dinamico, 1), 3), dtype=np.uint8)

        cinzento = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(cinzento, limiar_thresh, 255, cv2.THRESH_BINARY)
        contornos_hud, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        bussola_detetada = False
        hud_estavel = False
        fx, fy = cx_neutro, cy_neutro
        img_analise = img_bgr.copy()
        
        if contornos_hud:
            maior_hud = max(contornos_hud, key=cv2.contourArea)
            if cv2.contourArea(maior_hud) > 50:
                bussola_detetada = True
                M = cv2.moments(maior_hud)
                if M["m00"] != 0:
                    fx = int(M["m10"] / M["m00"])
                    fy = int(M["m01"] / M["m00"])
                    
                    centro_real_x = fx
                    centro_real_y = fy
                    
                    cv2.drawContours(img_analise, [maior_hud], -1, (255, 255, 0), 2)
                    cv2.circle(img_analise, (fx, fy), 2, (0, 165, 255), -1)
                    
                    desvio_x = abs(fx - cx_neutro)
                    desvio_y = abs(fy - cy_neutro)
                    hud_estavel = (desvio_x <= TOLERANCIA_CENTRO) and (desvio_y <= TOLERANCIA_CENTRO)

        bola_detetada = False
        mascara_vencedora = np.zeros_like(thresh)
        
        for perfil in memoria:
            img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(img_hsv, np.array(perfil['min']), np.array(perfil['max']))
            contornos_ponto, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            if contornos_ponto:
                maior_ponto = max(contornos_ponto, key=cv2.contourArea)
                if cv2.contourArea(maior_ponto) > 1:
                    bola_detetada = True
                    mascara_vencedora = mask
                    M_p = cv2.moments(maior_ponto)
                    if M_p["m00"] != 0:
                        px = int(M_p["m10"] / M_p["m00"])
                        py = int(M_p["m01"] / M_p["m00"])
                        cv2.circle(img_analise, (px, py), 3, (0, 255, 0), -1)
                    break

        cv2.rectangle(img_analise, (cx_neutro-DEAD_ZONE, cy_neutro-DEAD_ZONE), 
                                   (cx_neutro+DEAD_ZONE, cy_neutro+DEAD_ZONE), (255, 255, 255), 1)

        view_zoom = cv2.resize(img_analise, (400, 450), interpolation=cv2.INTER_NEAREST)
        mask_zoom = cv2.cvtColor(cv2.resize(mascara_vencedora, (400, 450), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
        rebordo_zoom = cv2.cvtColor(cv2.resize(thresh, (400, 450), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
        
        divisor = np.ones((450, 10, 3), dtype=np.uint8) * 50
        top_row = np.hstack((view_zoom, divisor, mask_zoom))
        
        # CORRIGIDO: Painel de texto restaurado na íntegra
        hud_texto = np.zeros((220, 810, 3), dtype=np.uint8)
        cv2.putText(hud_texto, f"SITUACAO ATUAL: {estado_acao}", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1)
        cv2.putText(hud_texto, f"COORDENADAS: top={top_dinamico} | left={left_dinamico} | dim={width_dinamico}x{height_dinamico}px", (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cv2.putText(hud_texto, f"LIMIAR DO REBORDO: {limiar_thresh}", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        cv2.putText(hud_texto, f"BUSSOLA RECONHECIDA: {'SIM' if bussola_detetada else 'NAO'}", (20, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0) if bussola_detetada else (0, 0, 255), 1)
        cv2.putText(hud_texto, f"CENTRO OTICO DA NAVE: X={centro_real_x} Y={centro_real_y}", (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        
        painel_final = np.vstack((top_row, hud_texto))
        cv2.imshow(NOME_PAINEL, painel_final)
        cv2.imshow(NOME_REBORDO, rebordo_zoom)
        cv2.waitKey(10)

cv2.destroyAllWindows()

# ==========================================
# EXPORTAÇÃO DOS DADOS (CENTRO DE MASSA REAL)
# ==========================================
if salvar_dados:
    banco_coordenadas = {}
    if os.path.exists(caminho_saida_config):
        try:
            with open(caminho_saida_config, "r") as f_in:
                conteudo = json.load(f_in)
                if "Nave" in conteudo:
                    nave_antiga = conteudo["Nave"]
                    banco_coordenadas[nave_antiga] = {
                        "MONITOR_CONFIG": conteudo["MONITOR_CONFIG"],
                        "CX_NEUTRO": conteudo["CX_NEUTRO"],
                        "CY_NEUTRO": conteudo["CY_NEUTRO"],
                        "LIMIAR_THRESH": conteudo.get("LIMIAR_THRESH", 127)
                    }
                else: banco_coordenadas = conteudo
        except Exception: pass

    banco_coordenadas[nave_identificada] = {
        "MONITOR_CONFIG": {
            "top": top_dinamico,
            "left": left_dinamico,
            "width": width_dinamico,
            "height": height_dinamico
        },
        "CX_NEUTRO": int(cx_neutro),
        "CY_NEUTRO": int(cy_neutro),
        "LIMIAR_THRESH": limiar_thresh
    }

    try:
        with open(caminho_saida_config, "w") as f_out:
            json.dump(banco_coordenadas, f_out, indent=4)
        print(f"\n[SUCESSO] Calibração trancada. Centro (quadrado branco) registado em X:{int(cx_neutro)} Y:{int(cy_neutro)}")
    except Exception as e: print(f"\n[ERRO] {e}")