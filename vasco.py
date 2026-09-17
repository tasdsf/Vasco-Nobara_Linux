#!/usr/bin/env python3
"""
VASCO - Vasco Automation Orchestrator
Elite Dangerous Automation with intelligent sequence handling
"""

import os
import sys
import time
import fcntl
import importlib
import json
import logging
import cv2
from datetime import datetime, timedelta
from pathlib import Path
from infra_bridge import keyboard
from los_checker import calcular_espera_los
from infra_bridge import ED_LOG_DIR
from infra_bridge import notificar_erro_discord, notificar_discord
from infra_bridge import reiniciar_leg_limpa, marcar_leg_suja
from infra_bridge import consumir_proximo_step_forcado
from infra_bridge import print_ts as print
import time

# Set up logging
SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "vasco_automated.log"
STATE_FILE = LOG_DIR / "vasco_state.json"
LOCK_FILE = LOG_DIR / "vasco.lock"

# Referencia global so para o handle nao ser fechado pelo garbage collector --
# e o proprio handle aberto que mantem o flock() vivo, nao o ficheiro em si.
_lock_handle = None

def adquirir_lock_unico():
    """ Impede duas instancias do vasco.py de correrem ao mesmo tempo e
    disputarem a mesma janela do jogo (nao ha nenhum mutex entre processos --
    ja aconteceu ficarem 3 instancias vivas em simultaneo, dias sem ninguem
    dar por isso, cada uma a mandar inputs por cima da outra). Usa
    fcntl.flock() em vez de um ficheiro de PID: o kernel liberta o lock
    sozinho quando o processo morre por qualquer razao (crash, kill -9,
    queda de energia), sem precisar de limpeza manual de lock ficheiro
    "orfao" -- um ficheiro de PID normal nao tem essa garantia. """
    global _lock_handle
    _lock_handle = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"\n[FATAL] Já existe uma instância do vasco.py a correr "
              f"(lock ocupado: {LOCK_FILE}). A abortar para não disputar "
              f"a janela do jogo com o outro processo.")
        sys.exit(1)
    _lock_handle.write(str(os.getpid()))
    _lock_handle.flush()

def setup_logger():
    logger = logging.getLogger("vasco")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fh = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
        logger.propagate = False
    return logger

def print_header():
    print("\n\n" + "="*60)
    print("     VASCO - ORQUESTRADOR DE AUTOMACAO ELITE DANGEROUS")
    print("="*60)
    print("Sequencia: Compra -> Carrier -> Venda -> Estacao")
    print("="*60 + "\n")

def load_state():
    if not STATE_FILE.exists():
        return {"last_step": -1, "completed_steps": []}
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"last_step": -1, "completed_steps": []}

def save_state(step, success, error=None, completed_steps=None):
    state = {
        "last_step": step,
        "success": success,
        "error": error,
        "completed_steps": completed_steps or []
    }
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)

SCRIPTS = {
    "comprar": "comprar.py",
    "target_carrier": "select_target.py",
    "undocking": "undocking.py",
    "olho": "olho.py",
    "supercruise": "supercruise_assist.py",
    "docking": "docking.py",
    "vender": "vender.py",
    "station": "select_target.py"
}

SEQUENCE = {
    # OLHO deixou de ser etapa própria (2026-08-19): o alinhamento passou
    # para dentro do supercruise_assist.py (_alinhar_com_olho), corrido
    # sempre depois do salto + Supercruise Assist ligado -- não faz
    # sentido alinhar antes de saltar (entrar em Supercruise não exige
    # apontar a nada) nem sem o Assist ligado (o alvo centra muito mais
    # depressa com ele ligado).
    1: {"name": "COMPRAR", "script": SCRIPTS["comprar"], "desc": "Comprar Fujin Tea na estacao atual"},
    2: {"name": "TARGET_CARRIER", "script": SCRIPTS["target_carrier"], "desc": "Selecionar Zahir como destino"},
    3: {"name": "UNDOCKING", "script": SCRIPTS["undocking"], "desc": "Undock da estacao"},
    4: {"name": "SUPERCRUISE", "script": SCRIPTS["supercruise"], "desc": "Supercruise assistido"},
    5: {"name": "DOCKING", "script": SCRIPTS["docking"], "desc": "Dock no fleet carrier"},
    6: {"name": "VENDER", "script": SCRIPTS["vender"], "desc": "Vender Fujin Tea no Zahir"},
    7: {"name": "SELECT_STATION", "script": SCRIPTS["station"], "desc": "Selecionar estacao de origem"},
    8: {"name": "UNDOCKING", "script": SCRIPTS["undocking"], "desc": "Undock da estacao"},
    9: {"name": "SUPERCRUISE", "script": SCRIPTS["supercruise"], "desc": "Supercruise assistido"},
    10: {"name": "DOCKING", "script": SCRIPTS["docking"], "desc": "Dock no fleet carrier"},
}

_modulos_carregados = {}
_modulos_mtime = {}

def _obter_modulo(script_name):
    """Importa o modulo do passo UMA UNICA VEZ por processo (cache), para que
    a sessao PipeWire singleton do infra_bridge.py seja reaproveitada por
    todas as etapas -- so pede confirmacao do popup do KDE uma vez por
    execucao do vasco.py, em vez de uma vez por etapa (subprocess antigo).
    Se o ficheiro no disco mudou desde o ultimo (re)carregamento, faz
    importlib.reload() antes de devolver -- sem isto, editar um script
    enquanto o vasco.py corre nao tinha efeito nenhum ate reiniciar."""
    modulo_nome = script_name[:-3] if script_name.endswith(".py") else script_name
    script_path = SCRIPT_DIR / script_name
    mtime_atual = script_path.stat().st_mtime if script_path.exists() else None

    if modulo_nome not in _modulos_carregados:
        _modulos_carregados[modulo_nome] = importlib.import_module(modulo_nome)
        _modulos_mtime[modulo_nome] = mtime_atual
    elif mtime_atual is not None and mtime_atual != _modulos_mtime.get(modulo_nome):
        print(f"[VASCO] Deteção de alteração em {script_name} -- a recarregar módulo...")
        _modulos_carregados[modulo_nome] = importlib.reload(_modulos_carregados[modulo_nome])
        _modulos_mtime[modulo_nome] = mtime_atual

    return _modulos_carregados[modulo_nome]

def executar_script(script_name, retry_count=3, retry_delay=5, **_ignorado):
    """Corre a etapa no MESMO processo (chama modulo.executar()), em vez de um
    subprocess isolado. Cada etapa sinaliza falha via sys.exit(1) dentro do seu
    proprio abortar_com_erro() -- isso levanta SystemExit aqui, que apanhamos
    sem deixar rebentar o vasco.py inteiro.
    Nota: perdemos o timeout/kill externo que o subprocess.Popen dava; cada
    etapa ja tem os seus proprios watchdogs internos (ver abortar_com_erro
    espalhado pelos scripts) que cobrem os cenarios de bloqueio."""
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        return False, f"Script nao encontrado: {script_name}", "FILE_NOT_FOUND"

    logger = setup_logger()
    logger.info(f"Executando (em processo): {script_name}")

    print(f"\n[ETAPA] Executando: {script_name}")
    print(f"    Retry: {retry_count}x")

    try:
        modulo = _obter_modulo(script_name)
    except Exception as e:
        logger.exception(f"Falha ao importar modulo para {script_name}: {e}")
        return False, f"Falha ao importar: {e}", "IMPORT_ERROR"

    error_msg = "Falha desconhecida"
    for attempt in range(retry_count):
        try:
            modulo.executar()
            logger.info(f"Etapa {script_name} concluida com sucesso (attempt: {attempt+1})")
            return True, "Sucesso", "OK"
        except SystemExit as e:
            codigo = e.code
            if codigo is None or codigo == 0:
                logger.info(f"Etapa {script_name} concluida com sucesso (attempt: {attempt+1})")
                return True, "Sucesso", "OK"
            error_msg = f"Exit code: {codigo}"
            logger.error(f"Etapa {script_name} falhou: {error_msg}")
            marcar_leg_suja()
        except Exception as e:
            error_msg = f"Excecao: {e}"
            logger.exception(f"Erro na etapa {script_name}: {e}")
            marcar_leg_suja()
        finally:
            # Sem isto, janelas cv2 abertas por uma etapa (ex: olho.py, que tem
            # VISUAL_DEBUG=True) ficam orfas -- no subprocess antigo, o fim do
            # processo fechava-as "de borla"; no processo partilhado, ninguem
            # as fecha a nao ser nos.
            cv2.destroyAllWindows()

        if attempt < retry_count - 1:
            print(f"\n[AVISO] Retry {attempt+1}/{retry_count} falhou. Esperando {retry_delay}s...")
            time.sleep(retry_delay)
        else:
            print(f"\n[ERRO] Max retries atingido. Falha: {error_msg[:200]}")
            _notificar_falha_discord(script_name, error_msg)

    return False, error_msg, "MAX_RETRIES"


# Mensagens de abortar_com_erro() que indicam bloqueio por oclusão (planeta/
# estrela a tapar o alvo) -- ver olho.py (timeout de manobra, geralmente por
# ficar preso a tentar desocluir) e supercruise_assist.py (diagnóstico
# explícito de salto falhado). Usado só para decidir SE se junta a previsão
# do los_checker à notificação -- não muda o comportamento da falha em si.
_MOTIVOS_OCLUSAO = ("obstruído por corpo celeste", "Bloqueio de timeout")

def _notificar_falha_discord(script_name, error_msg):
    """ Notifica o Discord só quando uma etapa esgota TODOS os retries (não
    em cada tentativa individual -- isso inundava o canal). Único ponto de
    integração para todas as etapas (comprar/select_target/undocking/
    supercruise_assist/docking/vender) em vez de mexer em cada script.

    Anexa o screenshot de erro mais recente (logs/erro_*.png, gravado
    automaticamente por abortar_com_erro em supercruise_assist.py) se
    tiver menos de 30s -- só se for mesmo desta falha, não de uma
    anterior. Best-effort: nunca pode impedir o vasco.py de continuar.

    Se a falha for por oclusão (ver _MOTIVOS_OCLUSAO), a mensagem ganha a
    hora exata em que parou e a previsão do los_checker (calcular_espera_los)
    de quando o alvo deve deixar de estar tapado -- pedido do utilizador
    (2026-09-16): "quero uma mensagem a informar quando parou, quando
    recomeça, etc". Nota: o vasco.py não retoma sozinho depois de esgotar
    os retries -- fica à espera de escolha manual (prompt "FASSA
    DETECTADA") -- por isso a previsão é só informativa (vale a pena olhar
    de novo por volta dessa hora), não uma garantia de retoma automática. """
    try:
        pasta_logs = SCRIPT_DIR / "logs"
        candidatos = sorted(pasta_logs.glob("erro_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        imagem_path = str(candidatos[0]) if candidatos and (time.time() - candidatos[0].stat().st_mtime) < 30 else None

        if any(motivo in error_msg for motivo in _MOTIVOS_OCLUSAO):
            agora = datetime.now().strftime('%H:%M:%S')
            mensagem = f"Parou às {agora} (oclusão): {error_msg}"
            try:
                espera = calcular_espera_los(ed_log_dir=ED_LOG_DIR)
                if espera and espera > 0:
                    fim_espera = (datetime.now() + timedelta(seconds=espera)).strftime('%H:%M:%S')
                    mensagem += f"\nPrevisão do LOS checker: livre por volta das {fim_espera}."
                else:
                    mensagem += "\nLOS checker não prevê bloqueio agora -- pode ser outra causa."
            except Exception as e:
                mensagem += f"\n(Previsão do LOS checker indisponível: {e})"
            mensagem += "\nÀ espera de intervenção manual -- o vasco.py não retoma sozinho."
            notificar_discord(script_name, mensagem, emoji="🪐", imagem_path=imagem_path)
        else:
            notificar_erro_discord(script_name, error_msg, imagem_path)
    except Exception as e:
        print(f"[AVISO] Falha ao notificar Discord: {e}")

def main():
    adquirir_lock_unico()

    # 'python vasco.py a' arranca já em modo automatico, sem perguntar no 1o ciclo
    # nem em nenhum ciclo seguinte -- fica sempre em automatico, sem countdown.
    auto_via_cli = len(sys.argv) > 1 and sys.argv[1].strip().lower() == 'a'
    auto = 1 if auto_via_cli else 0
    print_header()
    logger = setup_logger()
    logger.info("Vasco iniciado")
    
    while True:
        state = load_state()
        current_step = state.get("last_step", -1) + 1
        completed_steps = state.get("completed_steps", [])


        # Verifica se o ciclo já terminou anteriormente para limpar o ficheiro
        if current_step > len(SEQUENCE):
            current_step = 0
            completed_steps = []
            print("[INFO] Ciclo anterior detetado como completo. Reiniciando...")

        if current_step <= 0:
            print("Iniciando automacao do zero...")
            current_step = 1  # Comecar em 1, nao 0
            state["completed_steps"] = []
            # last_step=0, nao current_step (1) -- senao grava "etapa 1 ja
            # concluida" antes de sequer correr, e um crash a meio da etapa 1
            # faz o resume seguinte saltá-la (era o bug que tavas a corrigir
            # à mão).
            save_state(0, success=True, error="Init", completed_steps=[])
        else:
            print(f"Retornando da interrupcao na etapa: {current_step}")
            logger.info(f"Resumindo da etapa: {current_step}")

        while current_step <= len(SEQUENCE):
            # Janela curta para detetar 'p' (pausa manual) antes de cada etapa.
            fim_janela = time.time() + 0.5
            while time.time() < fim_janela:
                if keyboard.is_pressed('p'):
                    print("\n[VASCO] Pausa solicitada ('p'). Automacao suspensa antes da proxima etapa.")
                    input("Pressione Enter para continuar...")
                    break

            print_header()
            step_info = SEQUENCE[current_step]

            print(f"[ETAPA {current_step}] {step_info['name']}: {step_info['desc']}")
            print()

            if current_step in (8,3):  # UNDOCKING das duas pernas (era 9,3 antes da renumeracao 2026-08-19)
                # Reinicia o estado "perna limpa" aqui -- início da perna
                # undocking->supercruise. Só um salto que chegue ao fim
                # desta perna sem nenhum erro/retry (nem aqui, nem no
                # SUPERCRUISE a seguir) conta como observação de LOS
                # automática de confiança (ver infra_bridge.py).
                reiniciar_leg_limpa()
                espera = calcular_espera_los(ed_log_dir=ED_LOG_DIR)
                if espera and espera > 0:
                    h, resto = divmod(int(espera), 3600)
                    m, s = divmod(resto, 60)
                    agora = datetime.now().strftime('%H:%M:%S')
                    fim_espera = (datetime.now() + timedelta(seconds=espera)).strftime('%H:%M:%S')
                    print(f"[LOS] Planeta no meio. A aguardar {h}h {m}m {s}s... (livre por volta das {fim_espera})")
                    # Esta espera é proativa (nada falhou) -- não passava por
                    # _notificar_falha_discord, que só dispara em falhas
                    # reais. Sem isto, uma espera longa (horas) ficava só na
                    # consola, sem aviso nenhum no Discord. Pedido do
                    # utilizador (2026-09-16).
                    notificar_discord(
                        step_info["script"],
                        f"Parou às {agora} (LOS bloqueada, à espera pré-emptiva antes do UNDOCKING).\n"
                        f"Duração prevista: {h}h {m}m {s}s -- livre por volta das {fim_espera}.\n"
                        f"O vasco.py está a dormir -- retoma sozinho quando a espera acabar.",
                        emoji="🪐",
                    )
                    time.sleep(espera)
            
            success, error_msg, error_code = executar_script(
                step_info["script"],
                retry_count=step_info.get("retry_count", 3),
                retry_delay=step_info.get("retry_delay", 5)
            )
            
            if success:
                print(f"[SUCESSO] Etapa {step_info['name']} concluida!")
                completed_steps.append(current_step)
                save_state(current_step, success=True, completed_steps=completed_steps)

                # Redirecionamento por oclusão confirmada (ver
                # supercruise_assist.py/_redirecionar_para_partida): só
                # consumido aqui, depois do passo ATUAL ter terminado com
                # sucesso -- se o SUPERCRUISE+DOCKING de regresso à
                # partida não tivessem resultado, "success" seria False e
                # a bandeira nunca seria lida (fica pendente, sem efeito,
                # até ao próximo sucesso real). Pedido do utilizador,
                # 2026-09-16.
                passo_forcado = consumir_proximo_step_forcado()
                if passo_forcado is not None:
                    print(f"[VASCO] Redirecionamento por oclusão confirmado -- a saltar para o passo {passo_forcado} "
                          f"em vez de {current_step + 1}.")
                    current_step = passo_forcado
                else:
                    current_step += 1
            else:
                # Falha - requer intervencao. Fica neste prompt em loop
                # enquanto o "Retry manual" (escolha 1) continuar a falhar --
                # antes disto, uma retry falhada marcava a etapa como
                # concluida na mesma ("mas continuamos") e avancava para a
                # etapa seguinte com o trabalho desta por fazer (bug real:
                # 2026-08-19 20:13, TARGET_CARRIER falhou o retry manual e o
                # UNDOCKING seguinte avancou sem nenhum alvo trancado).
                cancelar_automacao = False
                while True:
                    print(f"\n[FASSA DETECTADA] Etapa {current_step} ({step_info['name']}): {error_msg}")
                    print(f"Opcoes:")
                    print("  1 - Retry manual (ignora erros anteriores)")
                    print("  2 - Fallback (operação cega, executada manualmente)")
                    print("  3 - Menu Manual (executar via menu.py)")
                    print("  4 - Skip (pular, continuar)")
                    print("  5 - Cancelar (abortar)")

                    try:
                        choice = input(f"\nEscolha (1-5): ").strip()
                        logger.info(f"User choice: {choice} for step {current_step}")

                        if choice == "1":
                            print("Retry manual solicitado...")
                            success, error_msg, _ = executar_script(step_info["script"])
                            if success:
                                print(f"[SUCESSO] Retry manual bem sucedido!")
                                completed_steps.append(current_step)
                                save_state(current_step, success=True, completed_steps=completed_steps)
                                current_step += 1
                                break
                            else:
                                print(f"[AVISO] Retry manual falhou: {error_msg} -- a voltar a perguntar.")
                                continue

                        elif choice == "2":
                            print("Fallback manual. Abra (script).py manualmente.")
                            print("Esta vende sem identificar o item.")
                            time.sleep(3)
                            completed_steps.append(current_step)
                            save_state(current_step, success=True, completed_steps=completed_steps)
                            current_step += 1
                            break

                        elif choice == "3":
                            print("Execucao via menu solicitada. Use menu.py normalmente.")
                            print("Pressione Enter quando terminar...")
                            input()
                            current_step += 1
                            break

                        elif choice == "4":
                            print(f"[SKIP] Etapa {step_info['name']} pulada.")
                            completed_steps.append(current_step)
                            save_state(current_step, success=True, completed_steps=completed_steps)
                            current_step += 1
                            break

                        elif choice == "5": # Abort solicitado pelo utilizador
                            print("[VASCO] A cancelar automação...")
                            cancelar_automacao = True
                            break

                        else:
                            print("Opcao invalida. Tente novamente.")
                            continue

                    except KeyboardInterrupt:
                        print("\nCancelado por Ctrl+C")
                        cancelar_automacao = True
                        break
                    except ValueError:
                        print("Opcao invalida.")
                        continue

                if cancelar_automacao:
                    break
        
        # Finalizacao
        print_header()
        print(f"="*60)
        print("     AUTOMACAO CONCLUIDA!")
        print(f"     Etapas concluidas: {len(completed_steps)}/{len(SEQUENCE)}")
        print(f"     Estado salvo em: {STATE_FILE}")
        print(f"     Log completo: {LOG_FILE}")
        print(f"="*60)
        
        if current_step > len(SEQUENCE):
            print("\n[*] Automacao concluida com sucesso!")
            if auto_via_cli:
                # Iniciado com 'vasco.py a': nunca pergunta nada, segue sempre em automatico.
                print("\n[VASCO] Modo automatico ('vasco.py a'). A prosseguir para o proximo ciclo...")
            elif auto == 0:
                choice = input("\nPressione Enter para sair ou 'a' para entrar em modo automatico: ").strip().lower()
                if choice == 'a':
                    auto = 1
                else:
                    print("\n[VASCO] A terminar por escolha do utilizador.")
                    try:
                        os.remove(STATE_FILE)
                    except:
                        pass
                    break
            else:
                # auto ativado por escolha (nao via CLI): countdown com hipotese de cancelar.
                print(f"\n ====> pressione 'a' para desativar auto-ciclico ")
                cont = 5
                while cont > 0:
                    cont -= 1
                    print(f"\n ---- Automacao vai recomeçar em {cont} ")
                    start = time.time()
                    while time.time() - start < 1:
                        if keyboard.is_pressed("a"):
                            auto = 0
                            break
                    if auto == 0:
                        break
            # Limpa o ficheiro de estado para o próximo ciclo
            try:
                os.remove(STATE_FILE)
            except:
                pass
            continue # Volta ao topo do 'while True' e recomeça
        else:
            print(f"\n[AVISO] Automacao interrompida. Revisa logs.")       
            break
    
    input("\nPressione Enter para sair...")

if __name__ == "__main__":
    main()
