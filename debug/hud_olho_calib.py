import os
import json
import cv2
import mss
import numpy as np
import time
import keyboard  # Captura inputs globais do teclado
import glob      # Para varrimento de ficheiros de Journal

# ==========================================
# 1. API NATIVA DO ELITE (LEITURA DO JOURNAL)
# ==========================================
def obter_modelo_nave_atual():
    """
    Acede aos logs locais do Elite Dangerous no Windows e extrai
    o modelo exato da nave activa usando o evento Loadout (mais fiável).
    """
    try:
        caminho_logs = os.path.join(os.environ['USERPROFILE'], 'Saved Games', 'Frontier Developments', 'Elite Dangerous')
        if not os.path.exists(caminho_logs): return "Desconhecido"
        
        lista_logs = glob.glob(os.path.join(caminho_logs, "Journal.*.log"))
        if not lista_logs: return "Desconhecido"
        
        # CORREÇÃO 2: A ordenação alfabética pura garante o ficheiro mais recente do dia,
        # contornando bugs de timestamps/modificação do Windows (getmtime)
        ultimo_log = max(lista_logs) 
        
        modelo_nave = "Desconhecido"
        with open(ultimo_log, 'r', encoding='utf-8') as f:
            for linha in f:
                try:
                    log_data = json.loads(linha)
                    # CORREÇÃO 1: Adicionado "Loadout" e "ShipyardBuy" à lista de eventos
                    if log_data.get("event") in ["LoadGame", "ShipyardSwap", "Loadout", "ShipyardBuy", "Location"]:
                        if "Ship_Localised" in log_data: 
                            modelo_nave = log_data["Ship_Localised"]
                        elif "Ship" in log_data: 
                            # Traduz as strings em bruto do motor do jogo (ex: "cobramk3" -> "Cobra Mk III")
                            nome_cru = log_data["Ship"].lower()
                            if "cobramk3" in nome_cru: modelo_nave = "Cobra Mk III"
                            elif "cobramk4" in nome_cru: modelo_nave = "Cobra Mk IV"
                            else: modelo_nave = log_data["Ship"].title()
                except: continue
        return modelo_nave
    except: return "Desconhecido"
    
# ==========================================
# 2. CARREGAMENTO INICIAL DINÂMICO (PRESET)
# ==========================================
nave_identificada = obter_modelo_nave_atual()

# Valores de Fallback (Padrão) - INTEGRADO O TEU VALOR 136 COMO PADRÃO
top_dinamico = 1180
left_dinamico = 970
width_dinamico = 80
height_dinamico = 90
limiar_thresh = 136  # Base calibrada pelo Comandante

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
caminho_memoria = os.path.join(diretorio_atual, "..\memoria_bussola.json")
caminho_saida_config = os.path.join(diretorio_atual, "..\coordenadas_bussola.json")

# Nomes fixos das janelas para indexação do Windows
NOME_PAINEL = "R2D2 - Laboratorio de Calibracao da Bussola"
NOME_REBORDO = "R2D2 - Mascara do Rebordo (HUD)"

# Tenta carregar o último estado gravado para a nave actual
if os.path.exists(caminho_saida_config):
    try:
        with open(caminho_saida_config, "r") as f_in:
            cfg_persistente = json.load(f_in)
        
        if cfg_persistente.get("Nave") == nave_identificada:
            mc = cfg_persistente["MONITOR_CONFIG"]
            top_dinamico = mc["top"]
            left_dinamico = mc["left"]
            width_dinamico = mc["width"]
            height_dinamico = mc["height"]
            # Carrega o limiar persistido se existir, caso contrário assume os 136
            limiar_thresh = cfg_persistente.get("LIMIAR_THRESH", 136)
            print(f"[PERSISTÊNCIA] Dados carregados com sucesso para a nave: '{nave_identificada}'")
        else:
            print(f"[INFO] Perfil em disco pertence a outra nave ({cfg_persistente.get('Nave')}). Usando defaults para '{nave_identificada}'.")
    except Exception as e:
        print(f"[AVISO] Perfil JSON corrompido ou antigo ({e}). Iniciando com base estável.")

# Carregar assinaturas de cor HSV
try:
    with open(caminho_memoria, "r") as f:
        memoria = json.load(f)
    print(f"[OK] {len(memoria)} perfis de cor carregados.")
except Exception as e:
    print(f"[ERRO] Falha ao ler memoria_bussola.json: {e}"); exit()

# ==========================================
# 3. INICIALIZAÇÃO E ALOCAÇÃO DE ECRÃS
# ==========================================
cv2.namedWindow(NOME_PAINEL, cv2.WINDOW_AUTOSIZE)
cv2.namedWindow(NOME_REBORDO, cv2.WINDOW_AUTOSIZE)

DEAD_ZONE = 4
TOLERANCIA_CENTRO = 3.0
salvar_dados = False  # Flag de controlo de persistência

with mss.mss() as sct:
    monitors = sct.monitors
    try:
        monitor_jogo = monitors[1]
    except IndexError:
        monitor_jogo = monitors[0]
        
    print(f"\n==================================================")
    print(f">>> LAB AUTOMÁTICO ONLINE | NAVE: '{nave_identificada}'")
    print("-> SETAS DO TECLADO   : Movem a janela (Cima/Baixo/Esquerda/Direita)")
    print("-> TECLAS '+' e '-'   : Aumentam/Diminuem a área de captura")
    print("-> PAGE UP / PAGE DOWN: Ajuste fino do Limiar do Rebordo (Actual: 136)")
    print("-> TECLA 'S'          : SAI GUARDANDO as configurações em ficheiro")
    print("-> TECLA 'Q'          : ABORTA e sai de imediato SEM GUARDAR")
    print("==================================================\n")

    # Gestão de Monitores: Projeta as duas janelas no ecrã secundário lado a lado
    if len(monitors) > 2:
        cv2.moveWindow(NOME_PAINEL, monitors[2]["left"] + 50, monitors[2]["top"] + 50)
        cv2.moveWindow(NOME_REBORDO, monitors[2]["left"] + 880, monitors[2]["top"] + 50)
    else:
        cv2.moveWindow(NOME_PAINEL, 50, 50)
        cv2.moveWindow(NOME_REBORDO, 880, 50)

    # ==========================================
    # 4. LOOP DE PROCESSAMENTO ÓTICO
    # ==========================================
    while True:
        estado_acao = "SISTEMA: A aguardar interacção do Piloto..."

        # Ajuste de Posição (Setas)
        if keyboard.is_pressed('up'):
            top_dinamico -= 1
            estado_acao = "PILOTO: Mover janela para CIMA (-1px)"
            time.sleep(0.04)
        elif keyboard.is_pressed('down'):
            top_dinamico += 1
            estado_acao = "PILOTO: Mover janela para BAIXO (+1px)"
            time.sleep(0.04)
        elif keyboard.is_pressed('left'):
            left_dinamico -= 1
            estado_acao = "PILOTO: Mover janela para ESQUERDA (-1px)"
            time.sleep(0.04)
        elif keyboard.is_pressed('right'):
            left_dinamico += 1
            estado_acao = "PILOTO: Mover janela para DIREITA (+1px)"
            time.sleep(0.04)
            
        # Ajuste de Tamanho (+ / -)
        if keyboard.is_pressed('plus') or keyboard.is_pressed('shift+plus'):
            width_dinamico += 2
            height_dinamico += 2
            estado_acao = "PILOTO: Expandir janela (+2px)"
            time.sleep(0.04)
        elif keyboard.is_pressed('-'):
            if width_dinamico > 40 and height_dinamico > 40:
                width_dinamico -= 2
                height_dinamico -= 2
                estado_acao = "PILOTO: Reduzir janela (-2px)"
                time.sleep(0.04)

        # Ajuste de Sensibilidade do Threshold (PageUp / PageDown)
        if keyboard.is_pressed('page up'):
            if limiar_thresh < 254:
                limiar_thresh += 2
                estado_acao = f"PILOTO: Aumentar Filtro Rebordo ({limiar_thresh})"
                time.sleep(0.04)
        elif keyboard.is_pressed('page down'):
            if limiar_thresh > 2:
                limiar_thresh -= 2
                estado_acao = f"PILOTO: Diminuir Filtro Rebordo ({limiar_thresh})"
                time.sleep(0.04)

        # Encerramentos Condicionais Baseados no teu Pedido
        if keyboard.is_pressed('s'):
            print("\n[ENCERRAMENTO] Comando recebido: A salvar alterações...")
            salvar_dados = True
            break
            
        if keyboard.is_pressed('q'):
            print("\n[ENCERRAMENTO] Comando recebido: Abortando sem salvar...")
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
        except Exception:
            estado_acao = "ERRO: Janela fora dos limites físicos do ecrã!"
            time.sleep(0.1)
            continue

        # --- PIPELINE A: ISOLAMENTO DO REBORDO (MOLDURA DINÂMICA) ---
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
                    cv2.drawContours(img_analise, [maior_hud], -1, (255, 255, 0), 2)
                    cv2.circle(img_analise, (fx, fy), 2, (0, 165, 255), -1)
                    
                    desvio_x = abs(fx - cx_neutro)
                    desvio_y = abs(fy - cy_neutro)
                    hud_estavel = (desvio_x <= TOLERANCIA_CENTRO) and (desvio_y <= TOLERANCIA_CENTRO)

        # --- PIPELINE B: ISOLAMENTO DA BOLA DA ROTA (HSV) ---
        bola_detetada = False
        px, py = 0, 0
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
                        cv2.drawContours(img_analise, [maior_ponto], -1, (0, 255, 0), 2)
                        cv2.circle(img_analise, (px, py), 3, (0, 255, 0), -1)
                    break

        # Desenhar Mira Alvo Central (Branca)
        cv2.rectangle(img_analise, (cx_neutro-DEAD_ZONE, cy_neutro-DEAD_ZONE), 
                                   (cx_neutro+DEAD_ZONE, cy_neutro+DEAD_ZONE), (255, 255, 255), 1)

        # ==========================================
        # 5. RENDERIZAÇÃO DOS DOS DOS FEEDS GRAPHICS
        # ==========================================
        view_zoom = cv2.resize(img_analise, (400, 450), interpolation=cv2.INTER_NEAREST)
        mask_zoom = cv2.cvtColor(cv2.resize(mascara_vencedora, (400, 450), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
        rebordo_zoom = cv2.cvtColor(cv2.resize(thresh, (400, 450), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
        
        # Painel Consolidado
        divisor = np.ones((450, 10, 3), dtype=np.uint8) * 50
        top_row = np.hstack((view_zoom, divisor, mask_zoom))
        hud_texto = np.zeros((220, 810, 3), dtype=np.uint8)
        
        cv2.putText(hud_texto, f"SITUACAO ATUAL: {estado_acao}", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1)
        cv2.putText(hud_texto, f"COORDENADAS DE PROCURA: top={top_dinamico} | left={left_dinamico} | dim={width_dinamico}x{height_dinamico}px", (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cv2.putText(hud_texto, f"LIMIAR DO REBORDO PERSISTENTE: {limiar_thresh}", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        cv2.putText(hud_texto, f"BUSSOLA RECONHECIDA: {'SIM (MOLDURA ENCONTRADA)' if bussola_detetada else 'NAO (MOVE AS SETAS / SOBE O LIMIAR)'}", (20, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0) if bussola_detetada else (0, 0, 255), 1)
        cv2.putText(hud_texto, f"MECANICA DO INSTRUMENTO: {'CENTRADO E ESTAVEL' if hud_estavel else 'FORA DO EIXO / OSCILANDO'} | Centro real: ({fx},{fy})", (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0) if hud_estavel else (0, 165, 255), 1)
        
        painel_final = np.vstack((top_row, hud_texto))
        cv2.imshow(NOME_PAINEL, painel_final)
        
        # Ocular Independente da Máscara de Rebordo
        cv2.putText(rebordo_zoom, f"MASCARA REBORDO - THRESH: {limiar_thresh}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1)
        cv2.imshow(NOME_REBORDO, rebordo_zoom)
        
        cv2.waitKey(10)

cv2.destroyAllWindows()

# ==========================================
# 6. ENGENHARIA DE PERSISTÊNCIA JSON CONDICIONAL
# ==========================================
if salvar_dados:
    dados_saida = {
        "Nave": nave_identificada,
        "MONITOR_CONFIG": {
            "top": top_dinamico,
            "left": left_dinamico,
            "width": width_dinamico,
            "height": height_dinamico
        },
        "CX_NEUTRO": width_dinamico // 2,
        "CY_NEUTRO": height_dinamico // 2,
        "LIMIAR_THRESH": limiar_thresh
    }

    try:
        with open(caminho_saida_config, "w") as f_out:
            json.dump(dados_saida, f_out, indent=4)
        print(f"\n[SUCESSO] Configuração gravada e trancada para a nave: '{nave_identificada}'")
        print(f"Ficheiro guardado em: {caminho_saida_config}")
    except Exception as e:
        print(f"\n[ERRO] Falha ao persistir dados no JSON: {e}")

    print("\n==================================================")
    print("MÉTRICAS EXPORTADAS COM SUCESSO:")
    print("==================================================")
    print(f"Modelo da Nave : {nave_identificada}")
    print(f"Geometria      : top={top_dinamico}, left={left_dinamico}, dim={width_dinamico}x{height_dinamico}")
    print(f"Filtro Thresh  : {limiar_thresh}")
    print("==================================================\n")
else:
    print("\n==================================================")
    print("[AVISO] Operação abortada via tecla Q. Nada foi gravado.")
    print("==================================================\n")