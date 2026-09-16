import os
import sys
import time
import glob
import json
import logging
import cv2
import numpy as np
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss, ED_STATUS_FILE
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
# padrão do undocking.py/select_target.py/comprar.py).
log_test = os.path.join(pasta_logs, "docking_test.png")

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("docking")
_logger.setLevel(logging.ERROR)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [DOCKING] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

# Motor de Voz e Som
engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

#def tocar_alarme_erro():
#    """ Toca um beep duplo agudo para chamar a atenção do piloto """
#    winsound.Beep(1200, 300)
#    time.sleep(0.1)
#    winsound.Beep(1200, 600)

def abortar_com_erro(mensagem):
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    tocar_alarme_erro()
    falar("Critical error during docking sequence. Manual intervention required.")
    # É ESTE sys.exit(1) QUE AVISA O VASCO.PY QUE HOUVE UMA FALHA!
    sys.exit(1)

NOME_JANELA = "Ocular do Bot - Docking"
VISUAL_DEBUG = False

def inicializar_infraestrutura():
    print("[SISTEMA] A configurar foco no jogo...")

    focar_jogo_seguro()
    time.sleep(0.5)
            
    if VISUAL_DEBUG:
        print("[SISTEMA] Modo Debug Ativo: A configurar janelas...")
        cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
        with mss.mss() as sct:
            monitores = sct.monitors
            if len(monitores) > 2:
                ecra_secundario = monitores[2]
                cv2.moveWindow(NOME_JANELA, ecra_secundario["left"] + 50, ecra_secundario["top"] + 50)
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
# 1. SETUP E CAMINHOS
# ==========================================
MONITOR_PANEL = {"top": 250, "left": 200, "width": 1200, "height": 450}
from infra_bridge import ED_LOG_DIR
LOG_DIR = ED_LOG_DIR

pasta_imagens = os.path.join(diretorio_atual, 'images')
templates_nomes = {
    'contacts_tab': 'CONTACTS.png',
    # Âncora para a redundância de navegação entre abas -- ver
    # navegar_para_aba() -- já testada e fiável noutros scripts
    # (select_target.py, supercruise_assist.py).
    'nav_tab': 'NAVIGATION_SELECTED.png',
    'docking_off': 'REQUEST_DOCKING_OFF.png',
    'docking_on': 'REQUEST_DOCKING_ON.png',
    'repair': 'repair.png'
}

templates = {}
try:
    for chave, nome_arq in templates_nomes.items():
        caminho = os.path.join(pasta_imagens, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta imagem: {caminho}")
        templates[chave] = img
    print(f"[SISTEMA] Módulo Docking Automático Blindado carregado.")
except Exception as e:
    abortar_com_erro(f"Falha ao carregar imagens para a memória: {e}")

# ==========================================
# 2. MOTOR DE VISÃO
# ==========================================
def procurar_template(template, nome_label, threshold=0.80):
    with mss.mss() as sct:
        img_bgra = np.array(sct.grab(MONITOR_PANEL))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        cv2.imwrite(log_test, img_bgr)
        resultado = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
        encontrou = max_val >= threshold
        
        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            cv2.rectangle(img_bgr, (5, 5), (450, 80), (0, 0, 0), -1)
            cv2.putText(img_bgr, f"Alvo: {nome_label}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(img_bgr, f"Match: {max_val:.2f} / {threshold:.2f}", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
            cv2.imshow(NOME_JANELA, img_bgr)
            cv2.waitKey(1)
            
        return encontrou

def navegar_para_aba(template_alvo, nome_alvo, tecla_ciclo, passos_desde_nav, tentativas=6):
    """ Procura a aba 'nome_alvo' ciclando com 'tecla_ciclo'. Se falhar em
    'tentativas', usa uma âncora: procura a aba NAVIGATION (mesmo template
    já fiável em select_target.py/supercruise_assist.py) e, se a
    confirmar, navega um número FIXO de passos a partir dela -- o layout
    das abas é fixo (NAVIGATION / TRANSACTIONS / CONTACTS) -- em vez de
    continuar a confiar cegamente no template da aba alvo, que pode estar
    a falhar por outro motivo (oclusão, iluminação, etc.). Com a âncora
    confirmada, não há outra hipótese de errar que aterramos na aba certa. """
    for _ in range(tentativas):
        if procurar_template(template_alvo, nome_alvo, 0.65):
            return True
        pydirectinput.press(tecla_ciclo)
        time.sleep(0.6)

    print(f"[AVISO] Não detetei a aba {nome_alvo} diretamente -- a tentar âncora via NAVIGATION.")
    if not procurar_template(templates['nav_tab'], "NAV TAB (ANCORA)", 0.61):
        print("[AVISO] Âncora NAVIGATION também não confirmada.")
        return False

    print(f"[LOG] Âncora NAVIGATION confirmada -- a navegar {passos_desde_nav}x '{tecla_ciclo}' até {nome_alvo}.")
    for _ in range(passos_desde_nav):
        pydirectinput.press(tecla_ciclo)
        time.sleep(0.6)

    return procurar_template(template_alvo, nome_alvo, 0.65)

# ==========================================
# 3. MOTOR DE LOGS COM CURSOR DINÂMICO
# ==========================================
STATUS_FLAGS = {
    "DOCKED": 0x1,
}

def ler_telemetria_flags():
    """ Lê Flags do Status.json do jogo -- mesmo mecanismo do
    undocking.py/supercruise_assist.py. """
    try:
        with open(ED_STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("Flags", 0)
    except Exception:
        return 0

def ja_esta_atracada():
    """ Confirma pela telemetria (Status.json, flag DOCKED=0x1) se a nave já
    está pousada -- evita repetir o pedido de docking (que pode confundir o
    menu ou o próprio jogo) quando já não há nada para pedir. """
    return bool(ler_telemetria_flags() & STATUS_FLAGS["DOCKED"])

GUI_FOCUS_NENHUM = 0

def ler_gui_focus():
    """ Lê GuiFocus do Status.json -- 0 quando nenhum painel está focado.
    Mesmo mecanismo do select_target.py: distingue "o '1' não teve efeito,
    painel nunca abriu" de "o painel abriu, só não está no sítio certo".
    """
    try:
        with open(ED_STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("GuiFocus", GUI_FOCUS_NENHUM)
    except Exception:
        return GUI_FOCUS_NENHUM

def get_latest_log():
    list_of_files = glob.glob(os.path.join(LOG_DIR, 'Journal.*.log'))
    if not list_of_files: return None
    return max(list_of_files, key=os.path.getctime)

def obter_tamanho_atual_log():
    latest_log = get_latest_log()
    if not latest_log: return 0
    try:
        return os.path.getsize(latest_log)
    except:
        return 0

EVENTOS_RESOLUCAO_DOCKING = {'Docked', 'DockingCancelled', 'DockingDenied', 'Undocked'}

def pedido_docking_em_curso():
    """ Varre o journal à procura de um pedido de docking já concedido
    (DockingGranted) e ainda sem resposta (nenhum Docked/DockingCancelled/
    DockingDenied/Undocked depois dele) -- sinal de que o Docking Computer
    pode já estar a voar a nave para o pad.

    Reabrir o painel lateral e voltar a selecionar a estação nesse estado
    cancela o pedido em curso -- bug real, confirmado em produção
    (2026-09-15): uma reentrada de solicitar_docking() (depois de uma
    chamada anterior falhar por outro motivo) reabriu o painel a meio de
    uma aproximação já concedida e a voar (música "DockingComputer"
    tocando havia 23s); 52s depois, DockingCancelled -> DockingDenied
    (Offences, por já estar marcada como trespass) -> a estação abriu fogo
    e destruiu a nave. Este guard corre ANTES de qualquer input em
    solicitar_docking() -- se um pedido já estiver em curso, salta direto
    para monitorizar (aguardar_confirmacao_docking), sem tocar em mais
    nada. """
    latest_log = get_latest_log()
    if not latest_log:
        return False
    try:
        with open(latest_log, 'r', encoding='utf-8') as f:
            linhas = f.readlines()
    except Exception:
        return False

    em_curso = False
    for linha in linhas:
        try:
            evento = json.loads(linha)
        except Exception:
            continue
        nome = evento.get('event')
        if nome == 'DockingGranted':
            em_curso = True
        elif nome in EVENTOS_RESOLUCAO_DOCKING:
            em_curso = False
    return em_curso

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
            except: continue
        return eventos
    except Exception as e:
        print(f"[ERRO] Falha ao ler stream de logs: {e}")
        return []

# ==========================================
# 4. VERIFICAÇÃO EM TEMPO REAL (CLOSED-LOOP)
# ==========================================
def aguardar_confirmacao_docking(posicao_ancora, timeout=540):
    print(f"[DOCKING] A monitorizar aproximação e pouso (Timeout Máximo: {timeout}s)...")
    timeout_real = time.time() + timeout
    
    while time.time() < timeout_real:
        novos = ler_novos_eventos(posicao_ancora)
        
        for evento in novos:
            nome_evento = evento.get('event')
            if nome_evento == 'Docked':
                print(f"[OK] Pouso confirmado via Log: Concluído em {evento.get('StationName', 'Estação')}")
                return True
            elif nome_evento == 'DockingCancelled':
                print(f"[ALERTA] A permissão de atracagem foi cancelada pelo jogo.")
                return False
            elif nome_evento == 'DockingDenied':
                motivo = evento.get('Reason', '?')
                if motivo == 'Distance':
                    # Recuperável -- a nave ainda não chegou perto o
                    # suficiente quando o pedido foi enviado (docking.py não
                    # tem verificação de distância própria, confia na
                    # aproximação já feita pelo OLHO/supercruise assist).
                    # Confirmado em produção (2026-09-14): 5x negado por
                    # "Distance" no Futen Spaceport em poucos minutos, cada
                    # reentrada a pedir cedo demais outra vez. Não abortar --
                    # sinaliza ao chamador (solicitar_docking) para esperar a
                    # nave aproximar-se mais e voltar a pedir.
                    print(f"[AVISO] Pedido negado por distância -- a nave ainda não está perto o suficiente.")
                    return 'distance'
                print(f"[ALERTA] A permissão de atracagem foi negada pelo jogo (motivo: {motivo}).")
                return False

        template = templates.get('repair')
        if template is not None:
            if procurar_template(template, "Menu Repair (Fallback)", 0.85):
                print("[OK] Menu visual 'Repair' detetado! Atracagem bem-sucedida.")
                return True
                
        time.sleep(1.0)
        
    print("[ALERTA] Timeout esgotado à espera do término do docking.")
    return False

# ==========================================
# 5. ROTINA CRÍTICA DE EXECUÇÃO
# ==========================================
def solicitar_docking():
    # Guard crítico -- ver pedido_docking_em_curso(). Corre antes de
    # qualquer input: se já houver um pedido concedido sem resposta ainda,
    # não mexe em nada, só monitoriza.
    if pedido_docking_em_curso():
        print("[DOCKING] Pedido já concedido e sem resposta ainda (journal) -- "
              "a saltar o painel, só a monitorizar o pouso.")
        ancora_log = obter_tamanho_atual_log()
        resultado = aguardar_confirmacao_docking(ancora_log)
        if resultado is True:
            print("\n[SUCESSO] Operação de docking totalmente finalizada.")
            return True
        abortar_com_erro("Pedido de docking já em curso não terminou em pouso "
                          "(negado, cancelado ou timeout). Intervenção manual necessária.")

    print("\n[VÔO] Parando nave (X)...")
    pydirectinput.press('x')
    time.sleep(0.5)

    timeout_global = time.time() + 180 

    # --- NOVO SISTEMA COM 3 TENTATIVAS MÁXIMAS ---
    for tentativa in range(1, 4):
        if time.time() > timeout_global:
            abortar_com_erro("Timeout global (180s) excedido. A abortar operação.")

        print(f"\n>>> [TENTATIVA {tentativa}/3] Abrindo painel lateral (1)...")
        pydirectinput.press('1')
        time.sleep(1.2)

        # Se o GuiFocus continuar em 0, o '1' não chegou ao jogo --
        # provavelmente o foco da janela saiu para outro sítio (ex: um
        # editor de texto aberto por cima). Reforça o foco e repete o '1'
        # antes de continuar -- sem isto, o script fica preso ativamente
        # (screenshots continuam a correr, nada avança) até esgotar
        # watchdogs bem mais longos, ou nem isso. Confirmado em produção
        # (2026-09-15): mais de 7 minutos preso assim, sem nenhum
        # DockingRequested no journal.
        if ler_gui_focus() == GUI_FOCUS_NENHUM:
            print("[AVISO] GuiFocus continua em 0 (nenhum painel aberto) -- a reforçar foco e repetir o '1'.")
            focar_jogo_seguro()
            time.sleep(0.3)
            pydirectinput.press('1')
            time.sleep(1.2)

        # 2 passos de 'e' a partir de NAVIGATION chega sempre a CONTACTS
        # (layout fixo do painel: NAVIGATION / TRANSACTIONS / CONTACTS) --
        # ver navegar_para_aba().
        aba_encontrada = navegar_para_aba(templates['contacts_tab'], "CONTACTS", 'e', 2)
        if aba_encontrada:
            print("[LOG] Aba Contacts confirmada!")

        if not aba_encontrada:
            msg = "[ERRO] Não detetei a aba Contacts. Tentando reiniciar ciclo..."
            print(msg)
            logging.warning(msg)
            pydirectinput.press('1')
            time.sleep(2)
            continue 

        # Seleciona a Estação na lista
        pydirectinput.press('space')
        time.sleep(0.8)
        
        clicou = False
        ancora_log = 0
        for _ in range(5):
            if procurar_template(templates['docking_on'], "DOCKING ON", 0.65):
                ancora_log = obter_tamanho_atual_log()
                pydirectinput.press('space')
                clicou = True
                break
            if procurar_template(templates['docking_off'], "DOCKING OFF", 0.65):
                pydirectinput.press('d')
                time.sleep(0.4)
        
        # 'backspace' em vez de '1' -- '1' é o toggle que abriu o painel, só
        # fecha se o jogo estiver exatamente no estado que o toggle espera;
        # 'backspace' sai de qualquer nível de menu, mesmo padrão já usado no
        # select_target.py.
        pydirectinput.press('backspace') # Fecha o painel holográfico
        time.sleep(1.5)

        if clicou:
            # Pedido enviado: a partir daqui a nave pode entrar em manobras
            # automáticas de aproximação (acelerações/travagens assim que o
            # DockingComputer assume) que tornam qualquer template visual
            # pouco fiável -- e reabrir o painel para tentar confirmar
            # visualmente arrisca acertar sem querer no botão de pedido outra
            # vez, o que aborta o docking a meio do percurso e deixa a nave
            # parada até intervenção manual. Por isso, uma vez enviado o
            # pedido, não se manda mais nenhum input: só se monitoriza o
            # journal (Granted/Denied/Cancelled/Docked) até ao fim, em vez de
            # reiniciar a tentativa ao fim de uma janela curta de espera.
            print("[LOG] Pedido enviado. A monitorizar o journal até ao pouso (sem mais inputs)...")
            resultado = aguardar_confirmacao_docking(ancora_log)
            if resultado is True:
                print("\n[SUCESSO] Operação de docking totalmente finalizada.")
                return True
            elif resultado == 'distance':
                # Negação recuperável -- a nave só precisa de se aproximar
                # mais. O painel já está fechado (backspace acima); espera e
                # deixa o ciclo 'for tentativa' reabrir o painel e re-pedir,
                # em vez de abortar como as outras negações.
                print(f"[LOG] A aguardar a nave aproximar-se mais antes de re-pedir "
                      f"(tentativa {tentativa}/3)...")
                time.sleep(20)
                continue
            else:
                abortar_com_erro("Pedido de docking enviado mas não confirmado (negado, cancelado ou "
                                  "timeout à espera do pouso). Intervenção manual necessária.")
        else:
            msg = "[ERRO] Botão de request não foi localizado."
            print(msg)
            logging.warning(f"Tentativa {tentativa} falhou: Botão Request Docking ausente.")
            falar("Request button not found.")
            time.sleep(2)

    # --- SE O CICLO FOR TERMINAR AS 3 TENTATIVAS SEM RETORNAR SUCESSO ---
    abortar_com_erro("Esgotadas as 3 tentativas de Docking. A estação não responde ou o menu está dessincronizado.")

def executar():
    inicializar_infraestrutura()

    if ja_esta_atracada():
        print("[DOCKING] Telemetria (Status.json) reporta DOCKED -- nave já está pousada, a saltar pedido de docking.")
        return

    print("Bot pronto. Inicia a aproximação em 1 segundos...")
    time.sleep(1)
    solicitar_docking()

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
