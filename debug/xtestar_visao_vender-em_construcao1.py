import os
import cv2
import mss
import numpy as np

# ==========================================
# 1. SETUP DE CONFIGURAÇÃO E GEOMETRIA
# ==========================================
GEO_AREAS = {
    # Câmara 1: Área Principal do Mercado (Carrier)
    "mercado": {"top": 200, "left": 0, "width": 1600, "height": 1400},
    
    # Câmara 2: Área de Menus / Navegação
    "menu": {"top": 1100, "left": 1000, "width": 600, "height": 400}
}

ALVOS = {
    # ---------------- ALVOS DO MENU ----------------
    'SERVICOS': {
        'ficheiro': 'CARRIER_SERVICES.png', 
        'threshold': 0.75, 
        'cor': (0, 255, 0),      # Verde
        'area_id': 'menu'
    },
    'NO_SELECTION': {
        'ficheiro': 'NO_SELECTION.png', 
        'threshold': 0.75, 
        'cor': (0, 165, 255),    # Laranja
        'area_id': 'menu'
    },
    
    # ---------------- ALVOS DO MERCADO ----------------
    'MARKET_OFF': {
        'ficheiro': 'COMMODITIES_MARKET_OFF.png', 
        'threshold': 0.75, 
        'cor': (0, 0, 255),      # Vermelho
        'area_id': 'mercado'
    },
    'MARKET_ON': {
        'ficheiro': 'COMMODITIES_MARKET_ON2.png', 
        'threshold': 0.75, 
        'cor': (255, 255, 0),    # Ciano
        'area_id': 'mercado'
    },
    'RARE_NOT_ON': {
        'ficheiro': 'RARE_NOT_SELECTED.png', 
        'threshold': 0.75, 
        'cor': (255, 0, 255),    # Rosa/Magenta
        'area_id': 'mercado'
    },
    'EXIT_ON': {
        'ficheiro': 'EXIT_SELECTED.png', 
        'threshold': 0.75, 
        'cor': (0, 255, 255),    # Amarelo
        'area_id': 'mercado'
    }
}

# ==========================================
# 2. CARREGAMENTO DOS TEMPLATES
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
# Caminho ajustado para '../images' conforme solicitado
pasta_imagens = os.path.join(diretorio_atual, '../images')

templates_carregados = {}
print("\n[SISTEMA] A instanciar matrizes óticas para validação de Venda...")
for chave, dados in ALVOS.items():
    caminho = os.path.join(pasta_imagens, dados['ficheiro'])
    img = cv2.imread(caminho, cv2.IMREAD_COLOR)
    if img is not None:
        templates_carregados[chave] = img
        print(f"[OK] Template validado em memória: {dados['ficheiro']}")
    else:
        print(f"[ERRO] I/O Exception. Ficheiro ausente em: {caminho}")

# ==========================================
# 3. ALOCAÇÃO DE ECRÃS (Modo Headless/Debug)
# ==========================================
NOME_PAINEL = "R2D2 - Ocular de Venda (Market Lab)"
cv2.namedWindow(NOME_PAINEL, cv2.WINDOW_NORMAL)
cv2.resizeWindow(NOME_PAINEL, 1500, 800) # Janela larga para acomodar ambas as visualizações

with mss.mss() as sct:
    monitores = sct.monitors
    
    if len(monitores) > 2:
        ecra_secundario = monitores[2]
        cv2.moveWindow(NOME_PAINEL, ecra_secundario["left"] + 50, ecra_secundario["top"] + 50)
    else:
        cv2.moveWindow(NOME_PAINEL, 50, 50)

    print("\n==================================================")
    print(">>> MARKET VISION LAB EM MODO DE VARRIMENTO")
    print("-> Pressione 'Q' na janela das câmaras para Sair")
    print("==================================================\n")

    # ==========================================
    # 4. LOOP DE INSPEÇÃO
    # ==========================================
    while True:
        if cv2.waitKey(40) & 0xFF == ord('q'):
            print("\n[ENCERRAMENTO] Conexão ótica terminada.")
            break

        frames = {}
        
        # 1. Extração Absoluta de Coordenadas (Sem offsets flutuantes)
        for nome_area, coords in GEO_AREAS.items():
            try:
                area_absoluta = {
                    "top": int(coords["top"]),
                    "left": int(coords["left"]),
                    "width": int(coords["width"]),
                    "height": int(coords["height"])
                }
                img_bgra = np.array(sct.grab(area_absoluta))
                img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
                frames[nome_area] = img_bgr.copy()
            except Exception as e:
                print(f"[ERRO] Violação de limites no ecrã para a área '{nome_area}': {e}")
                frames[nome_area] = np.zeros((coords["height"], coords["width"], 3), dtype=np.uint8)

        y_textos = {chave: 30 for chave in GEO_AREAS.keys()}

        # 2. Avaliação de Métrica Algorítmica (Template Matching)
        for nome_alvo, dados in ALVOS.items():
            if nome_alvo not in templates_carregados: continue
                
            area_id = dados['area_id']
            frame_trabalho = frames[area_id]
            template = templates_carregados[nome_alvo]
            th, tw = template.shape[:2]

            res = cv2.matchTemplate(frame_trabalho, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            percentagem = max_val * 100
            
            if max_val >= dados['threshold']:
                cor_render = dados['cor']
                texto = f"[{nome_alvo}]: OK ({percentagem:.1f}%)"
                espessura = 2
                cv2.rectangle(frame_trabalho, max_loc, (max_loc[0] + tw, max_loc[1] + th), cor_render, espessura)
            else:
                cor_render = (50, 50, 130) # Red Shift para indicar falha
                texto = f"[{nome_alvo}]: Falha ({percentagem:.1f}% / Exige: {dados['threshold']*100:.0f}%)"
                espessura = 1
            
            cv2.putText(frames[area_id], texto, (10, y_textos[area_id]), cv2.FONT_HERSHEY_SIMPLEX, 0.55, cor_render, 1)
            y_textos[area_id] += 25

        # 3. Pipeline de Renderização (Adaptação para dimensões grandes)
        # Redimensionamos a área do mercado (1600x1400) para metade para caber na janela
        view_mercado = cv2.resize(frames["mercado"], (800, 700), interpolation=cv2.INTER_NEAREST)
        view_menu = cv2.resize(frames["menu"], (600, 400), interpolation=cv2.INTER_NEAREST)
        
        canvas_final = np.zeros((800, 1450, 3), dtype=np.uint8)
        
        # Colagem da Câmara do Mercado (Esquerda)
        canvas_final[50:750, 20:820] = view_mercado
        cv2.putText(canvas_final, "CÂMARA 1: MERCADO / CARRIER", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        # Colagem da Câmara de Menus (Direita)
        canvas_final[50:450, 840:1440] = view_menu
        cv2.putText(canvas_final, "CÂMARA 2: SELEÇÃO DE MENUS", (840, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        cv2.imshow(NOME_PAINEL, canvas_final)

cv2.destroyAllWindows()