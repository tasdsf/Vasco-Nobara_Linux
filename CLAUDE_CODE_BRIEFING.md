# Briefing — Projeto vasco-r2d2 (Elite Dangerous Automation)

## Contexto
Bot de automação Python (visão computacional + input injection) para o jogo
Elite Dangerous, a correr em Linux Nobara (KDE Plasma 6, Wayland puro, sem
X11), via Steam/Proton. Migrado de Windows 11.

## Infraestrutura já resolvida (não mexer sem necessidade)

**infra_bridge.py** — camada de abstração central, usada por todos os módulos:

- **Captura de ecrã**: `mss` NÃO funciona em Wayland puro. Solução:
  substituído por sessão PipeWire via `xdg-desktop-portal-kde` (singleton
  lazy, só abre na primeira captura, partilhada por todos os módulos no
  mesmo processo). API mantém-se idêntica a `mss.mss()`.
- **Input**: `pyautogui`/`pydirectinput` não conseguem injetar em janelas
  Proton/XWayland. Solução: `ydotool` com **scancodes explícitos** —
  CRÍTICO: `ydotool key 1` envia scancode 1 = Esc, não a tecla "1". A
  tabela `_SCANCODES` em `infra_bridge.py` faz a tradução correta.
- **Foco de janela** (`_focar_janela_linux`, `infra_bridge.py`) — histórico
  importante, **não reverter às cegas para nenhuma das versões anteriores**:
  - Versão 1 (original, Windows→Linux): `wmctrl`/`xdotool windowactivate`.
    Nota antiga registava que isto disparava o menu de pause do Elite — não
    foi re-verificado nesta base de código; ver "risco em aberto" abaixo.
  - Versão 2: clique físico (`ydotool mousemove` + `ydotool click 0xC0`)
    no centro do ecrã de jogo, para contornar o problema da v1. Funcionava
    para focar, mas **causava inputs de jogo reais e indesejados**
    (heatsink, hardpoints) — o Elite trata cliques do rato como ações de
    voo/armas associadas ao botão, independentemente de onde o cursor
    esteja no ecrã, não como um clique posicional numa UI.
  - **Versão atual (2026-07)**: `xdotool search --name <título>
    windowactivate`, com fallback para `wmctrl -a <título>`, e só como
    último recurso um clique físico numa posição neutra (100,5 — fora da
    zona 3D do cockpit) se ambos falharem. Motivo: eliminar o risco de
    disparo de armas/módulos da v2.
  - **Risco em aberto**: a v1 foi abandonada por, alegadamente, disparar o
    menu de pause — mas essa observação nunca foi reproduzida ou explicada
    nesta sessão (não se sabe se foi um problema do ambiente antigo, de
    X11 puro, ou algo já obsoleto). A versão atual usa essencialmente o
    mesmo mecanismo (`xdotool`/`wmctrl`) que a v1. **Se o menu de pause
    voltar a aparecer sozinho durante a automação, é a primeira suspeita**
    — mas a correção não é reverter para o clique físico (reintroduz o
    problema da v2); é investigar porque é que `windowactivate` desperta
    o pause (ex: falta de flag específica, comportamento diferente entre
    X11 e XWayland, versão do KDE) e resolver a causa raiz.
- **`keyboard.is_pressed()`** exige root hardcoded no Linux (`ensure_root()`
  verifica euid==0 literalmente, não respeita grupo `input`). Solução:
  `evdev_keyboard.py` — wrapper próprio via evdev, respeita grupo `input`.

**vasco.py** — orquestrador, corre cada etapa **no mesmo processo** (não
subprocess): importa cada módulo uma única vez (`importlib.import_module`,
cache em `_modulos_carregados`) e chama `modulo.executar()` diretamente.
Motivo: a sessão PipeWire singleton só pede confirmação do popup do KDE
uma vez por execução do `vasco.py`, em vez de uma vez por etapa. Cada
módulo sinaliza falha via `sys.exit(1)` dentro do seu `abortar_com_erro()`
— o `vasco.py` apanha isso como `SystemExit` sem morrer. Deteta alterações
no ficheiro `.py` de cada etapa (mtime) e recarrega (`importlib.reload`)
antes de a correr, para apanhar edições feitas com o processo já a correr.

**Logging**: cada módulo tem o seu próprio `logging.getLogger(<nome>)` com
handler e `propagate = False` — **nunca usar `logging.basicConfig()`** num
módulo novo. Com vários módulos no mesmo processo (ver acima), só o
primeiro `basicConfig()` chamado tem efeito; todos os módulos seguintes
ficavam a escrever no log partilhado com o prefixo errado (bug real, já
corrigido em todos os módulos existentes).

**los_checker.py** — calcula tempo de oclusão orbital planeta↔carrier, por
sistema (não hardcoded a Fujin, embora só Fujin tenha constantes orbitais
calibradas em `CONSTANTES_POR_SISTEMA` neste momento). Observações
(`visivel`/`oclusos`) vivem numa tabela Postgres partilhada (`los_observacoes`,
base `ED`, no mesmo servidor Postgres do projeto `tradingBot`), não num
ficheiro local — evita misturar observações de sistemas diferentes e
permite juntar dados de outras máquinas no futuro. Ligação via variáveis
de ambiente (`.env`, nunca commitado): `R2D2_DB_HOST`, `R2D2_DB_PORT`,
`R2D2_DB_NAME` (default `ED`), `R2D2_DB_USER`. Password vem do
`~/.pgpass`, nunca do `.env` nem do código. Registar observação: `python los_calibrar.py`. O antigo
`los_calibracao.json` foi descontinuado e removido.

## Ficheiros do projeto

- `infra_bridge.py` — bridge central (captura, input, foco)
- `evdev_keyboard.py` — substituto de `keyboard.is_pressed()`
- `los_checker.py` / `los_calibrar.py` — oclusão orbital planeta↔carrier
  (ver acima), backend Postgres
- `vasco.py` — orquestrador principal (in-process, ver acima)
- `comprar.py`, `vender.py`, `docking.py`, `undocking.py`,
  `select_target.py`, `supercruise_assist.py`, `olho.py` — módulos de
  automação, cada um com área de captura `MONITOR_*` calibrada para
  1920x1080
- `menu.py` — menu manual de execução individual de módulos, referenciado
  pelo próprio `vasco.py` no fluxo de intervenção manual ("Menu Manual").
  `menuz.py` (equivalente, não referenciado em lado nenhum) foi movido
  para `temp/` por ser redundante.
- `input_controller.py`, `screen_capture.py` — legacy Windows/`mss`,
  não importados por nada; movidos para `temp/`.
- `debug/` — laboratórios de calibração/teste (um por módulo de produção,
  espelham as mesmas zonas `MONITOR_*`/templates/thresholds), incluindo
  `debug/calc_periodo_orbital.py` (cálculo dos períodos orbitais usados em
  `los_checker.py`, antes mal-nomeado como `import math`).

## Trabalho pendente

- **Leitura da distância no HUD (`git stash`)** — há um `git stash` com uma
  versão de `docking.py`/`supercruise_assist.py` para aproximação
  controlada por leitura de dígitos do HUD, ainda não commitada por dois
  problemas confirmados por resolver (posição dinâmica da etiqueta,
  contaminação por fundo colorido). Ver `NOTA_STASH_LEITURA_DISTANCIA.md`
  antes de aplicar (`git stash pop`) ou de mexer em
  `debug/coletar_digitos_hud.py` / `images/digitos_hud/`.

## Regras de trabalho

- Nunca reintroduzir lógica win32 (`sys.platform == "win32"`) — o projeto
  é Linux-only.
- `cv2.error "assertion failed"` em `matchTemplate` é quase sempre
  `MONITOR_*` desatualizado ou fora dos limites do ecrã (ex: uma região
  com `top+height` > altura do monitor devolve um crop vazio) — comparar
  `template.shape` com a área `MONITOR_*` usada, e confirmar que a região
  cabe no monitor real.
- Thresholds de `matchTemplate` calibrados numa sessão podem deixar de
  bater dias depois (variações de luz/render) — se um watchdog passa a
  falhar sistematicamente com match "quase lá" (ex: 1-5% abaixo do
  threshold, sempre na mesma zona), normalmente basta recalibrar o
  threshold com folga, usando os `debug/*_teste.py` para medir o valor
  real ao vivo antes de decidir o novo número.
- Templates que incluem números/quantidades variáveis (unidades no
  inventário, preços) não devem ser usados como imagem de referência —
  captura só o texto fixo (nome do item), como aconteceu com
  `RARE_NOT_SELECTED.png`.
- Daemon `ydotoold` corre via `systemd --user`
  (`~/.config/systemd/user/ydotoold.service`).
- Antes de afirmar que um ficheiro existe, uma dependência está instalada,
  ou um comando produziu X, confirmar com uma ferramenta (`ls`, `grep`,
  `python -c "import ..."`) em vez de assumir.
- Manter o âmbito da tarefa restrito ao que foi pedido; não refatorar
  código fora do pedido.
