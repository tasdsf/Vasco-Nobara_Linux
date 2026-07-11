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
cv2.resizeWindow(NOME_PAINEL, 966, 534)

with mss.mss() as sct:
    monitores = sct.monitors
    
    if len(monitores) > 2:
        ecra_secundario = monitores[2]
        cv2.moveWindow(NOME_PAINEL, ecra_secundario["left"] + 50, ecra_secundario["top"] + 50)
    else:
        cv2.moveWindow(NOME_PAINEL, 50, 50)

    print("\n==================================================")
    print(">>> MARKET VISION LAB ONLINE | LETRAS AMPLIADAS")
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
        
        # 1. Extração Absoluta de Coordenadas
        for nome_area, coords in GEO_AREAS.items():
            try:
                area_absoluta = {
                    "top": int(coords["top"]), "left": int(coords["left"]),
                    "width": int(coords["width"]), "height": int(coords["height"])
                }
                img_bgra = np.array(sct.grab(area_absoluta))
                img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
                frames[nome_area] = img_bgr.copy()
            except Exception as e:
                frames[nome_area] = np.zeros((coords["height"], coords["width"], 3), dtype=np.uint8)

        # Filas de telemetria isoladas para desenho pós-resize
        telemetria_mercado = []
        telemetria_menu = []

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
                texto = f"{nome_alvo}: OK ({percentagem:.1f}%)"
                # Desenha o retângulo na resolução nativa (vai encolher proporcionalmente)
                cv2.rectangle(frame_trabalho, max_loc, (max_loc[0] + tw, max_loc[1] + th), cor_render, 3)
            else:
                cor_render = (50, 50, 130) # Tons escuros para falhas
                texto = f"{nome_alvo}: Falha ({percentagem:.1f}%)"
            
            # Guarda o texto e a cor correspondente para rendering tardio
            if area_id == 'mercado':
                telemetria_mercado.append((texto, cor_render))
            else:
                telemetria_menu.append((texto, cor_render))

        # 3. Pipeline de Redimensionamento
        view_mercado = cv2.resize(frames["mercado"], (534, 466), interpolation=cv2.INTER_NEAREST)
        view_menu = cv2.resize(frames["menu"], (400, 266), interpolation=cv2.INTER_NEAREST)
        
        # 4. OVERLAY DAS LETRAS AMPLIADAS (Pós-Resize e Sem Fundo)
        # Canal do Mercado: Tamanho Grande (3x a 4x maior que o original)
        y_offset_mercado = 40
        for texto, cor in telemetria_mercado:
            cv2.putText(view_mercado, texto, (15, y_offset_mercado), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, cor, 2, cv2.LINE_AA)
            y_offset_mercado += 38

        # Canal do Menu: Tamanho Proporcional e Altamente Legível
        y_offset_menu = 45
        for texto, cor in telemetria_menu:
            cv2.putText(view_menu, texto, (15, y_offset_menu), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, cor, 2, cv2.LINE_AA)
            y_offset_menu += 32

        # 5. Composição Final do Painel de Debug
        canvas_final = np.zeros((534, 966, 3), dtype=np.uint8)
        
        # Colagem das duas janelas processadas
        canvas_final[40:506, 15:549] = view_mercado
        cv2.putText(canvas_final, "CAMARA 1: MERCADO / CARRIER (ZOOM 50%)", (15, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        canvas_final[40:306, 550:950] = view_menu
        cv2.putText(canvas_final, "CAMARA 2: SELECAO DE MENUS (NATIVO)", (550, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        cv2.imshow(NOME_PAINEL, canvas_final)

cv2.destroyAllWindows()