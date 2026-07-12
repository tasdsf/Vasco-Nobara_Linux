import os
import sys
import time
import glob
import json
import logging
import cv2
import numpy as np
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss, print_ts as print, ED_LOG_DIR
import time

# ==========================================
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_logs = os.path.join(diretorio_atual, "logs")
log_test = os.path.join(pasta_logs, "undocking_test.png")
os.makedirs(pasta_logs, exist_ok=True)

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("undocking")
# INFO por causa do registo do score do AUTO_COMPLETE (unico caso de uso
# nao-ERROR neste logger) -- ver aguardar_saida_estacao().
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [UNDOCKING] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

# Logger a parte, so para os scores do AUTO_COMPLETE aceites -- enquanto o
# valor do template estiver "em estudo" (ver historico do threshold: 0.70 ->
# 0.73 -> 0.89), queremos uma serie limpa de dados para decidir ate onde da
# para descer, sem ter de garimpar isto no meio do r2d2_combined.log. So
# regista a deteccao final que passou o threshold, nao cada poll falhado.
_logger_calibracao = logging.getLogger("undocking.auto_complete_calibracao")
_logger_calibracao.setLevel(logging.INFO)
if not _logger_calibracao.handlers:
    _fh_cal = logging.FileHandler(os.path.join(pasta_logs, "auto_complete_scores.log"), encoding='utf-8')
    _fh_cal.setFormatter(logging.Formatter('%(asctime)s,%(message)s'))
    _logger_calibracao.addHandler(_fh_cal)
    _logger_calibracao.propagate = False

def abortar_com_erro(mensagem):
    """ Regista o erro no log e dispara exit code 1 para o Orquestrador intercetar """
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    sys.exit(1)

NOME_JANELA = "R2D2 - Ocular de Auditoria"
VISUAL_DEBUG = False # Muda para False para esconder a janela

def inicializar_infraestrutura():
    """ Foca no jogo (Ecrã 1) e envia o painel visual para o Ecrã 2 APENAS SE VISUAL_DEBUG FOR TRUE """
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
# 1. SETUP DE GEOMETRIA E TEMPLATES
# ==========================================
MONITOR_MENU = {"top": 770, "left": 800, "width": 330, "height": 300}
MONITOR_CORNER = {"top": 50, "left": 1400, "width": 500, "height": 300}

LOG_DIR = ED_LOG_DIR

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

pasta_imagens = os.path.join(diretorio_atual, 'images')

templates_nomes = {
    'repair': 'repair.png',
    'autolaunch': 'AUTO_LAUNCH.png',
    'noselection': 'NO_SELECTION.png',
    'auto_complete': 'AUTO_LAUNCH_COMPLETE.png'
}

templates = {}
try:
    for chave, nome_arq in templates_nomes.items():
        caminho = os.path.join(pasta_imagens, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta a imagem: {caminho}")
        templates[chave] = img
    print(f"[SISTEMA] Módulo: Undocking Ótico Closed-Loop Carregado.")
except Exception as e:
    abortar_com_erro(f"Falha de Assinatura Visual ao arrancar: {e}")

# --- Motor de Voz ---
engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

# ==========================================
# 2. MOTOR DE VISÃO COMPUTACIONAL
# ==========================================
def procurar_template(template, nome_label, monitor, threshold=0.85):
    if template is None: return False, 0.0
    
    with mss.mss() as sct:
        monitors = sct.monitors
        try:
            monitor_jogo = monitors[1]
        except IndexError:
            monitor_jogo = monitors[0]
            
        area_real = {
            "top": monitor_jogo["top"] + monitor["top"],
            "left": monitor_jogo["left"] + monitor["left"],
            "width": monitor["width"],
            "height": monitor["height"]
        }
        
        img_bgra = np.array(sct.grab(area_real))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        # teste begin
        cv2.imwrite(log_test, img_bgr) 
        # teste end
        resultado = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
        
        encontrou = max_val >= threshold
        
        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            
            cv2.rectangle(img_bgr, (0, 0), (350, 45), (0, 0, 0), -1)
            cv2.putText(img_bgr, f"{nome_label}: {max_val*100:.1f}% (Min: {threshold*100:.0f}%)", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            img_show = cv2.resize(img_bgr, (700, 600))
            cv2.imshow(NOME_JANELA, img_show)
            cv2.waitKey(1)
        
        return encontrou, max_val

# ==========================================
# 3. MÁQUINA DE ESTADOS DETERMINÍSTICA
# ==========================================
def executar_auto_launch():
    print("\n==================================================")
    print(">>> CODE EXECUTION: MANOBRA DE UNDOCKING SEQUENCIAL")
    print("==================================================")
    
    # Passo 0: Estabilização do HUD do Menu
    print("A aguardar estabilização do menu...")
    timeout_menu = time.time() + 15  # Watchdog de 15 segundos
    while True:
        if time.time() > timeout_menu:
            falar("Error. Interface stabilization timeout.")
            abortar_com_erro("Timeout (15s) à espera que o botão 'Repair' estabilize no menu da estação.")
            
        m1, _ = procurar_template(templates['repair'], "ESTABILIZACAO", MONITOR_MENU, 0.85)
        if m1: break
        time.sleep(0.3)
    
    time.sleep(0.5)

    # Passo 1: Subida Mecânica
    print("\nA enviar comandos mecânicos: 3x 'w' + 1x 'space'...")
    for _ in range(3):
        pydirectinput.press('w')
        time.sleep(0.2)
    pydirectinput.press('space')
    time.sleep(0.3)
    
    # Passo 2: Validação Cega
    print("\nA validar 'NO_SELECTION' no topo do menu...")
    time.sleep(0.3) 
    noselect_val = 0.7
    sucesso_idle, score_idle = procurar_template(templates['noselection'], "VAL_NO_SELECTION", MONITOR_MENU, noselect_val)
    if not sucesso_idle:
        falar("Error. Validation failed at menu top. Aborting sequence.")
        abortar_com_erro(f"Falha crítica ótica no teto. Match real: {score_idle*100:.1f}% (Exigia: {noselect_val*100}%)")
        
    print(f"[OK] 'NO_SELECTION' validado com {score_idle*100:.1f}%.")

    # Passo 3: Descida Mecânica
    print("\nA navegar para a posição do botão: 2x 's'...")
    for _ in range(2):
        pydirectinput.press('s')
        time.sleep(0.25)
    
    # Passo 4: Validação do Alvo
    print("\nA auditar foco do botão Auto-Launch...")
    time.sleep(0.3) 
    autolaunch_val = 0.82
    sucesso_al, score_al = procurar_template(templates['autolaunch'], "VAL_AUTO_LAUNCH", MONITOR_MENU, autolaunch_val)
    if not sucesso_al:
        falar("Auto launch not detected.")
        abortar_com_erro(f"Botão Auto-Launch não detetado (Match real: {score_al*100:.1f}% / Exigia {autolaunch_val*100}%)")

    # Passo 5: Execução Limpa, com confirmação de que o press saiu do botão.
    # O press('space') é um subprocess.run ao ydotool sem verificar o codigo
    # de saida -- se falhar em silencio nesse instante, o botao continua
    # visivel e nunca saberiamos. Por isso confirmamos que ele desapareceu
    # (sinal de que o menu reagiu) antes de avançar para a espera longa.
    print("\n>>> TUDO VALIDADO! A disparar comando SPACE...")
    for tentativa in range(3):
        pydirectinput.press('space')
        time.sleep(1.0)
        ainda_visivel, _ = procurar_template(templates['autolaunch'], "VAL_AUTO_LAUNCH_POS", MONITOR_MENU, autolaunch_val)
        if not ainda_visivel:
            break
        print(f"[AVISO] Botão Auto-Launch ainda visível após o SPACE (tentativa {tentativa+1}/3). A repetir...")
    else:
        print("[AVISO] Auto-Launch pode não ter disparado após 3 tentativas -- a prosseguir mesmo assim.")

    return True

AUTO_COMPLETE_THRESHOLD = 0.89  # valor defensivo enquanto calibramos -- ver logs/auto_complete_scores.log

def aguardar_saida_estacao():
    print("\n>>> FASE: Detetar saída da estação...")
    print("[VISÃO] A monitorizar o HUD para a notificação 'AUTO LAUNCH COMPLETE'...")

    # Watchdog de 3 Minutos (A estação pode ter fila de trânsito)
    timeout_saida = time.time() + 180

    while True:
        if time.time() > timeout_saida:
            falar("Warning. Auto launch timeout exceeded.")
            abortar_com_erro("Timeout (180s) à espera de sair da estação. A nave está presa no trânsito?")

        encontrou, score = procurar_template(templates['auto_complete'], "AUTO_COMPLETE", MONITOR_CORNER, AUTO_COMPLETE_THRESHOLD)
        if encontrou:
            print(f"\n>>> [VISÃO] Notificação detetada com {score*100:.1f}% de precisão!")
            _logger.info(f"AUTO_COMPLETE detetado com {score*100:.1f}% de precisao (threshold {AUTO_COMPLETE_THRESHOLD*100:.0f}%).")
            _logger_calibracao.info(f"{score*100:.2f},{AUTO_COMPLETE_THRESHOLD*100:.0f}")
            print("[LOG] Saída da estação confirmada.")
            break
        time.sleep(0.4)
    
    print("[SUCESSO] Estamos no espaço aberto!")
    falar("Auto launch terminated commander.")

def sequencia_salto():
    print("\n>>> FASE: Impulso de Saída...")
    pydirectinput.keyDown('.')
    time.sleep(5)
    pydirectinput.keyUp('.')
    pydirectinput.press('tab')
    time.sleep(15.0)
    pydirectinput.press('x')
    time.sleep(8.0)

def aguardar_no_fire_zone_exit(ancora_log, timeout=60):
    """ Gate final antes de entregar o controlo ao OLHO: a deteção visual do
    AUTO_COMPLETE (acima) pode dar falso positivo -- já aconteceu a nave ficar
    presa junto ao pad com o HUD a bater ruído nos 70%+ e a manobra seguir em
    frente na mesma. Este evento vem do próprio Journal do jogo, por isso não
    há como fingir: só avançamos quando o jogo confirma "No fire zone exited".
    Se a nave estiver mesmo presa em trânsito (raro), abortamos como qualquer
    outra falha desta máquina de estados -- não vale a pena complicar com
    lógica de recuperação para um caso raro; aceitar o prejuízo e deixar o
    'a' (modo automático) tentar de novo é mais barato. """
    print(f"\n>>> FASE: A confirmar saída da no-fire-zone via Journal (timeout {timeout}s)...")
    timeout_real = time.time() + timeout

    while time.time() < timeout_real:
        for evento in ler_novos_eventos(ancora_log):
            if evento.get('event') == 'ReceiveText' and evento.get('Message') == '$STATION_NoFireZone_exited;':
                print("[OK] 'No fire zone exited' confirmado pelo Journal.")
                _logger.info("No fire zone exited confirmado -- handoff para OLHO autorizado.")
                return True
        time.sleep(0.5)

    falar("Warning. Still inside station no fire zone.")
    abortar_com_erro("Timeout à espera de 'No fire zone exited' no Journal. A nave pode estar presa/bloqueada perto da estação.")

# ==========================================
# 4. EXECUÇÃO PRINCIPAL
# ==========================================
def executar():
    inicializar_infraestrutura()

    print("O R2D2 assume os comandos em 1 segundos...")
    time.sleep(1)

    ancora_log = obter_tamanho_atual_log()

    sucesso_execucao = executar_auto_launch()
    if sucesso_execucao:
        aguardar_saida_estacao()
        sequencia_salto()
        aguardar_no_fire_zone_exit(ancora_log)

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
