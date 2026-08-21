import os
import sys
import time
import glob
import json
import logging
import cv2
import numpy as np
from infra_bridge import pydirectinput, gw, winsound, mss, ED_LOG_DIR
import time

if sys.platform == "win32":
    import winsound
    def tocar_alarme_erro():
        winsound.Beep(1200, 300)
else:
    import os
    def tocar_alarme_erro():
        # Usa o beep do sistema ou simplesmente print (ou ignora)
        print("\a") # Carácter ASCII de Bell (emite um som no terminal se configurado)

# ==========================================
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_logs = os.path.join(diretorio_atual, "logs")
os.makedirs(pasta_logs, exist_ok=True)
# Última captura de procurar_template(), sobrescrita a cada chamada -- dá
# evidência forense de qualquer falha sem depender de VISUAL_DEBUG (mesmo
# padrão do undocking.py/select_target.py).
log_test = os.path.join(pasta_logs, "comprar_test.png")

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("comprar")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [COMPRAR] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

def abortar_com_erro(mensagem):
    """ Regista o erro no log e dispara exit code 1 para o Orquestrador intercetar """
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    for _ in range(3):
        pydirectinput.press('backspace')
        time.sleep(0.8)
    sys.exit(1)

NOME_JANELA = "Ocular do Bot - Diagnostico"
VISUAL_DEBUG = False # Muda para False para esconder as janelas

def inicializar_infraestrutura():
    """ Foca no jogo (Ecrã 1) e envia o painel visual para o Ecrã 2 """
    print("[SISTEMA] A focar no jogo...")
    
    focar_jogo_seguro()
    time.sleep(0.5)
            
    if VISUAL_DEBUG:
        print("[SISTEMA] A configurar janelas de diagnóstico...")
        cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
        with mss.mss() as sct:
            monitores = sct.monitors
            if len(monitores) > 2:
                ecra_secundario = monitores[2]
                pos_x = ecra_secundario["left"] + 50
                pos_y = ecra_secundario["top"] + 50
                cv2.moveWindow(NOME_JANELA, pos_x, pos_y)
            else:
                cv2.moveWindow(NOME_JANELA, 50, 50)
            cv2.setWindowProperty(NOME_JANELA, cv2.WND_PROP_TOPMOST, 1)

def focar_jogo_seguro():
    """ Foca o Elite Dangerous a nível de Sistema Operativo, sem enviar cliques de rato """
    print("[SISTEMA] A focar o Elite Dangerous via Windows API...")
    try:
        # Procura a janela pelo título (no Elite geralmente é "Elite - Dangerous (CLIENT)")
        janelas = gw.getWindowsWithTitle("Elite - Dangerous (CLIENT)")
        
        if janelas:
            janela_elite = janelas[0]
            # Traz a janela para a frente
            janela_elite.activate() 
            time.sleep(0.5)
            print("[OK] Jogo focado com sucesso e em segurança.")
            return True
        else:
            print("[ERRO] Janela do Elite Dangerous não encontrada!")
            return False
            
    except Exception as e:
        print(f"[AVISO] Falha ao forçar foco via OS: {e}")
        return False

# ==========================================
# 1. SETUP E CALIBRAÇÃO DE ÁREAS
# ==========================================
MONITOR_MENU = {"top": 770, "left": 800, "width": 330, "height": 300}
MONITOR_MARKET = {"top": 190, "left": 70, "width": 530, "height": 860}

pasta_imagens = os.path.join(diretorio_atual, 'images')

templates_nomes = {
    'servicos': 'STARPORT_SERVICES.png',
    # Pelo sim pelo não -- caso este script alguma vez corra com a nave
    # atracada no Fleet Carrier (menu diz "CARRIER SERVICES" em vez de
    # "STARPORT SERVICES", mesmo layout). Mesmo mismatch já visto e
    # corrigido no undocking.py.
    'servicos_carrier': 'CARRIER_SERVICES.png',
    # "Starport Services" vem pré-selecionado a amarelo brilhante assim que
    # o painel abre (é o primeiro item da lista) -- 'servicos' foi calibrado
    # no estado apagado/sem foco e fica sempre a ~0.69 (abaixo do threshold
    # 0.70) contra este estado. Recortado diretamente de logs/comprar_test.png.
    'servicos_selecionado': 'STARPORT_SERVICES_SELECTED.png',
    'noselection': 'NO_SELECTION.png',
    'market_off': 'COMMODITIES_MARKET_OFF.png',
    'market_on': 'COMMODITIES_MARKET_ON.png',
    'buy_on': 'BUY_SELECTED.png',
    'buy_button_on': 'BUY_BUTTON.png',
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
    abortar_com_erro(f"Falha ao carregar imagens para a memória: {e}")

# ==========================================
# 1b. CONFIRMAÇÃO DA COMPRA VIA JOURNAL DO JOGO
# ==========================================
# Mesmos tipos internos aceites pelo vender.py -- só um dos dois está
# disponível para compra de cada vez.
TIPOS_RARE_ACEITES = {"fujintea", "kamitracigars"}

def get_latest_log():
    list_of_files = glob.glob(os.path.join(ED_LOG_DIR, 'Journal.*.log'))
    if not list_of_files: return None
    return max(list_of_files, key=os.path.getmtime)

def obter_tamanho_atual_log():
    latest_log = get_latest_log()
    if not latest_log: return 0
    try:
        return os.path.getsize(latest_log)
    except Exception:
        return 0

def ler_novos_eventos(posicao_ancora):
    latest_log = get_latest_log()
    if not latest_log: return []
    try:
        tamanho_atual = os.path.getsize(latest_log)
        if tamanho_atual <= posicao_ancora:
            return []
        with open(latest_log, 'r', encoding='utf-8') as f:
            f.seek(posicao_ancora)
            linhas_novas = f.readlines()
        eventos = []
        for linha in linhas_novas:
            try:
                data = json.loads(linha)
                if 'event' in data:
                    eventos.append(data)
            except Exception:
                continue
        return eventos
    except Exception:
        return []

def aguardar_confirmacao_compra(posicao_ancora, timeout=30):
    """ Confirma a compra pelo evento MarketBuy real do journal, em vez de
    confiar só na sequência visual de teclas/templates -- sem isto, um menu
    dessincronizado ou compra recusada fica por detetar e a nave segue com
    o porão vazio. """
    print("[LOG] A confirmar a compra pelo journal do jogo...")
    limite = time.time() + timeout
    while time.time() < limite:
        for evento in ler_novos_eventos(posicao_ancora):
            if evento.get('event') == 'MarketBuy' and evento.get('Type', '').lower() in TIPOS_RARE_ACEITES:
                print(f"[OK] Compra confirmada pelo journal: {evento.get('Type_Localised')} "
                      f"x{evento.get('Count')} por {evento.get('TotalCost')} CR")
                return True
        time.sleep(0.5)
    print("[AVISO] Compra não confirmada pelo journal dentro do tempo limite.")
    return False

# ==========================================
# 2. MOTOR DE VISÃO
# ==========================================
def procurar_template(template, nome_label, monitor, threshold=0.80):
    with mss.mss() as sct:
        img_bgra = np.array(sct.grab(monitor))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        cv2.imwrite(log_test, img_bgr)
        resultado = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
        encontrou = max_val >= threshold

        print(f"[TRACE] {nome_label}: match={max_val:.3f} threshold={threshold:.2f} loc={max_loc} -> {'OK' if encontrou else '--'}")

        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            
            cv2.rectangle(img_bgr, (5, 5), (450, 80), (0, 0, 0), -1)
            cv2.putText(img_bgr, f"Alvo: {nome_label}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(img_bgr, f"Match: {max_val:.2f} / {threshold}", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
            
            cv2.imshow(NOME_JANELA, img_bgr)
            cv2.waitKey(1)
        
        return encontrou

# ==========================================
# 3. FASES DE NAVEGAÇÃO COM WATCHDOGS
# ==========================================
def fase_1_entrar_servicos():
    print("\n>>> FASE 1: Menu Holográfico...")
    timeout = time.time() + 15
    while not (procurar_template(templates['noselection'], "IDLE", MONITOR_MENU, 0.72) or
               procurar_template(templates['servicos'], "SERVICES", MONITOR_MENU, 0.70) or
               procurar_template(templates['servicos_carrier'], "SERVICES (CARRIER)", MONITOR_MENU, 0.70) or
               procurar_template(templates['servicos_selecionado'], "SERVICES (SELECIONADO)", MONITOR_MENU, 0.80)):

        if time.time() > timeout:
            abortar_com_erro("Timeout (15s) à espera que o painel principal da estação estabilize.")
        time.sleep(0.5)

    time.sleep(1.2)

    # Varrimento mecânico do botão Services -- por tempo (não por um número
    # fixo de teclas), mesma regra do NAVIGATION em
    # engatar_assistencia_menu() (supercruise_assist.py): 10s na primeira
    # tentativa; se não bater, fecha (2x backspace) e tenta outra vez com
    # mais 5s; se mesmo assim não bater, algo está muito errado -- aborta
    # em vez de continuar às cegas. Intervalo entre teclas subido de 0.6s
    # para 0.9s -- teclas andavam a falhar na ida ao mercado, possivelmente
    # por serem enviadas depressa demais para o jogo registar.
    def _tentar_focar_servicos(timeout):
        teclas_ciclo = ['s', 's', 'w', 'w']
        tempo_fim = time.time() + timeout
        i = 0
        while time.time() < tempo_fim:
            if (procurar_template(templates['servicos'], "SERVICES", MONITOR_MENU, 0.70) or
                    procurar_template(templates['servicos_carrier'], "SERVICES (CARRIER)", MONITOR_MENU, 0.70) or
                    procurar_template(templates['servicos_selecionado'], "SERVICES (SELECIONADO)", MONITOR_MENU, 0.80)):
                pydirectinput.press('space')
                return True
            pydirectinput.press(teclas_ciclo[i % len(teclas_ciclo)])
            i += 1
            time.sleep(0.9)
        return False

    if _tentar_focar_servicos(10.0):
        return True

    print("[AVISO] Botão Services não detetado em 10s -- a fechar e a tentar outra vez...")
    for _ in range(4):
        pydirectinput.press('backspace')
        time.sleep(0.3)
    time.sleep(0.5)

    if _tentar_focar_servicos(5.0):
        return True

    abortar_com_erro("Botão 'Starport/Carrier Services' não detetado mesmo depois de reabrir -- algo está muito errado.")

def fase_2_abrir_mercado():
    print("\n>>> FASE 2: Abrindo Mercado...")
    timeout = time.time() + 15
    while not (procurar_template(templates['market_off'], "MARKET OFF", MONITOR_MARKET) or 
               procurar_template(templates['market_on'], "MARKET ON", MONITOR_MARKET)):
        
        if time.time() > timeout:
            abortar_com_erro("Timeout (15s) à espera que a interface interna dos Serviços carregue.")
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
        
    abortar_com_erro("Botão 'Commodities Market' não detetado na lista após varrimento mecânico.")
    pydirectinput.press('backspace')

def fase_3_comprar_item():
    print("\n>>> FASE 3: Acedendo ao Mercado...")
    timeout = time.time() + 15
    while not procurar_template(templates['buy_on'], "BUY ON", MONITOR_MARKET):
        if time.time() > timeout:
            abortar_com_erro("Timeout (15s) à espera que o menu do Mercado estabeleça o botão BUY.")
        time.sleep(0.5)
        
    print("\n>>> FASE 3: Procurando rare goods...")
    _logger.info("Selecionou a lista de rare goods (tecla 'd').")
    time.sleep(2.0)
    pydirectinput.press('d')
    time.sleep(0.5)

    for i in range(40):
        if procurar_template(templates['rare_on'], "RARE FOUND", MONITOR_MARKET, 0.80):
            print(">>> ITEM DETETADO! Comprando...")
            _logger.info("Detetou o item (Fujin Tea / Kamitra Cigars) no mercado.")
            pydirectinput.press('space')
            time.sleep(1.0)
            pydirectinput.keyDown('d')
            time.sleep(2.5)
            pydirectinput.keyUp('d')
            # 1.5s (subido de 1.0s) -- dar tempo ao jogo de assentar depois
            # do hold longo de 'd' (2.5s), antes de mandar mais teclas.
            time.sleep(1.5)

            # Confirma que o foco chegou mesmo ao botão BUY antes de
            # confirmar a compra -- sem isto, um 's' que não registasse
            # (enviado cedo demais a seguir ao hold longo de 'd') levava a
            # confirmar a compra sem o foco lá ter chegado. Confirmado em
            # produção (2026-08-21): duas tentativas seguidas, uma "desceu
            # mas não comprou", outra "nem desceu" -- em ambas o código
            # continuou às cegas como se tivesse resultado.
            buy_focado = False
            for tentativa_s in range(3):
                pydirectinput.press('s')
                print(f"\n[DEBUG] desceu <s> (tentativa {tentativa_s+1}/3)")
                time.sleep(1.0)
                if procurar_template(templates['buy_button_on'], "BUY BUTTON ON (focado)", MONITOR_MARKET, 0.80):
                    buy_focado = True
                    break

            if not buy_focado:
                abortar_com_erro("Botão BUY não ficou focado após 3 tentativas de 's' -- algo está muito errado.")

            ancora_journal = obter_tamanho_atual_log()
            pydirectinput.press('space')
            print("\n[DEBUG] comprou <space>")
            _logger.info("Comprou -- carregou no SPACE para confirmar a compra.")
            time.sleep(2.0)

            compra_confirmada = aguardar_confirmacao_compra(ancora_journal)
            if compra_confirmada:
                _logger.info("Compra confirmada pelo journal (MarketBuy detetado).")
            else:
                _logger.warning("Compra NÃO confirmada pelo journal dentro do tempo limite.")

            for _ in range(3):
                pydirectinput.press('backspace')
                time.sleep(0.8)

            if not compra_confirmada:
                return "FALHA_CONFIRMACAO"
            return "COMPRADO"
        
        # Este template é válido de falhar se o item estiver esgotado (lógica de negócio normal)
        if procurar_template(templates['exit_on'], "EXIT BUTTON", MONITOR_MARKET, 0.75):
            print(">>> Fim da lista. Item não disponível no momento.")
            return "NAO_ENCONTRADO"
            
        pydirectinput.press('s')
        time.sleep(0.5)
        
    abortar_com_erro("Passou 40 iterações na lista sem encontrar o RARE nem o botão de EXIT. Bot perdeu-se.")

# ==========================================
# 4. LOOP DE PERSISTÊNCIA (20 MINUTOS MAX)
# ==========================================
def executar_ciclo_completo():
    tempo_limite = 20 * 60  
    inicio_contagem = time.time()
    
    while True:
        tempo_decorrido = time.time() - inicio_contagem
        if tempo_decorrido > tempo_limite:
            abortar_com_erro("Tempo limite operacional da rotina de compra (20 min) excedido.")

        print(f"\n--- INICIANDO TENTATIVA (Tempo decorrido: {int(tempo_decorrido/60)} min) ---")
        
        # A lógica passa automaticamente para a frente ou morre de imediato por erro nas fases
        if fase_1_entrar_servicos():
            if fase_2_abrir_mercado():
                resultado = fase_3_comprar_item()
                
                if resultado == "COMPRADO":
                    print("\n>>> OPERAÇÃO CONCLUÍDA COM SUCESSO! <<<")
                    return True

                elif resultado == "FALHA_CONFIRMACAO":
                    print("\n[REPETIR] Compra não confirmada pelo journal (menu dessincronizado?). "
                          "A tentar de novo...")
                    time.sleep(5.0)
                    continue

                elif resultado == "NAO_ENCONTRADO":
                    print("\n[REPETIR] Item esgotado. Saindo e aguardando 1 minuto...")
                    # Clica no Exit que já está selecionado
                    pydirectinput.press('space')
                    time.sleep(1.5)
                    # Garante que volta ao Cockpit para resetar menus -- mesmos
                    # 3x backspace do caminho "COMPRADO" acima; 1x não chegava
                    # para desfazer Commodities Market -> BUY tab -> lista.
                    for _ in range(3):
                        pydirectinput.press('backspace')
                        time.sleep(0.8)

                    time.sleep(60) # 1 Minuto
                    continue

def executar():
    inicializar_infraestrutura()

    print("O R2D2 assume os comandos em 1 segundos...")
    time.sleep(1)
    executar_ciclo_completo()

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
