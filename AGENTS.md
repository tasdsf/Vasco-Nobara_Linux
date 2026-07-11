# AGENTS.md — Projeto vasco-r2d2 (Elite Dangerous Automation)

<!-- CANARY: VASCO-R2D2-AGENTS-LOADED-9f3a1c -->

Este ficheiro é lido automaticamente por agentes locais (ex: Hermes) a correr
neste diretório. É um resumo derivado de `CLAUDE_CODE_BRIEFING.md` — esse
ficheiro é a fonte de verdade; se houver conflito, ele prevalece.

Se este AGENTS.md foi carregado corretamente, sabes o token que se segue a
"CANARY:" na linha acima. É usado por `vasco-hermes` para confirmar que as
regras deste projeto chegaram mesmo ao modelo antes de aceitar tarefas reais.

## Contexto
Bot de automação Python (visão computacional + input injection) para o jogo
Elite Dangerous, a correr em Linux Nobara (KDE Plasma 6, Wayland puro, sem
X11), via Steam/Proton.

## Regras críticas — NÃO violar

- **Nunca reintroduzir lógica win32** (`sys.platform == "win32"`). O projeto
  é Linux-only.
- **Captura de ecrã**: `mss` não funciona em Wayland puro. A solução já
  implementada em `infra_bridge.py` usa uma sessão PipeWire via
  `xdg-desktop-portal-kde` (singleton lazy, partilhada por todos os
  módulos no mesmo processo). Não substituir por `mss` nem duplicar a
  lógica de captura fora de `infra_bridge.py`.
- **Input**: `pyautogui`/`pydirectinput` não funcionam contra janelas
  Proton/XWayland. Usa-se `ydotool` com scancodes explícitos via a tabela
  `_SCANCODES` em `infra_bridge.py`. CRÍTICO: `ydotool key 1` envia o
  scancode 1 (= Esc), não a tecla "1" — nunca chamar `ydotool key` com um
  número literal à espera que corresponda à tecla desse número.
- **Foco de janela** (`_focar_janela_linux`): método atual é
  `xdotool windowactivate` → fallback `wmctrl -a` → último recurso clique
  físico neutro em (100,5). **NÃO usar clique físico no centro do ecrã de
  jogo** — já causou disparo real de heatsink/hardpoints (o Elite trata
  cliques do rato como ações de voo/armas do botão, não como clique
  posicional). Se o menu de pause do Elite aparecer sozinho durante a
  automação, é suspeito do `xdotool`/`wmctrl` (problema antigo, nunca
  confirmado nesta base de código) — investigar a causa, não reverter
  para o clique físico. Ver `CLAUDE_CODE_BRIEFING.md` para o histórico
  completo desta decisão.
- **`keyboard.is_pressed()`** não funciona sem root literal no Linux. Usar
  sempre `evdev_keyboard.py`.
- `infra_bridge.py` é a camada de abstração central usada por todos os
  módulos — não mexer sem necessidade, e nunca duplicar as suas soluções
  noutro ficheiro.
- **`vasco.py` corre cada etapa no mesmo processo** (import + chamada
  direta, não subprocess) — não voltar a subprocess sem motivo forte,
  perde-se a partilha da sessão PipeWire (implicaria voltar a pedir o
  popup do KDE por etapa). Cada módulo novo precisa de um `logging.getLogger`
  próprio, nunca `logging.basicConfig()` (só o primeiro chamado no
  processo tem efeito, os restantes ficam com o prefixo de log errado).
- **`los_checker.py`** usa Postgres partilhado (base `ED`, tabela
  `los_observacoes`), não um ficheiro local — configuração via `.env`
  (`R2D2_DB_*`). O antigo `los_calibracao.json` foi removido; não
  reintroduzir esse padrão.

## Ficheiros do projeto

- `infra_bridge.py` — bridge central (captura, input, foco)
- `evdev_keyboard.py` — substituto de `keyboard.is_pressed()`
- `los_checker.py` / `los_calibrar.py` — oclusão orbital planeta↔carrier,
  backend Postgres partilhado
- `vasco.py` — orquestrador principal (in-process)
- `comprar.py`, `vender.py`, `docking.py`, `undocking.py`,
  `select_target.py`, `supercruise_assist.py`, `olho.py` — módulos de
  automação, cada um com área de captura `MONITOR_*` calibrada para
  1920x1080
- `menu.py` — menu manual, referenciado pelo `vasco.py` (fluxo de
  intervenção manual). `menuz.py`, `input_controller.py`,
  `screen_capture.py` são legacy, movidos para `temp/`.

## Diagnóstico comum

Se houver `cv2.error "assertion failed"` em `matchTemplate`, a causa é
quase sempre `MONITOR_*` desatualizado ou fora dos limites do monitor
real — comparar `template.shape` com a área `MONITOR_*` usada, e
confirmar que a região cabe no ecrã.

Se um watchdog de visão passa a falhar sistematicamente com match "quase
lá" (poucos % abaixo do threshold), o threshold provavelmente ficou
desatualizado (luz/render mudou) — recalibrar com folga usando os
`debug/*_teste.py` para medir o valor real ao vivo.

## Regras de trabalho para agentes locais

- Antes de afirmares que um ficheiro existe, tens uma dependência, ou um
  comando produziu X, **confirma com uma ferramenta** (`ls`, `cat`, grep)
  em vez de assumir a partir de padrões vistos noutros projetos. Já houve
  casos de alucinação de nomes de ficheiros que não existem.
  Não inventes ficheiros, funções ou resultados — se não tens a certeza,
  di-lo explicitamente em vez de inventar.
- Mantém o âmbito da tarefa restrito ao que foi pedido. Não refatores nem
  "melhores" código fora do pedido.
- No fim de qualquer alteração, corre/verifica o que for possível (sintaxe,
  imports, teste rápido) antes de reportar como concluído.
